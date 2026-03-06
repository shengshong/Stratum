"""
lock.py — Exclusive write-lock for the ChromaDB store.

ChromaDB's Rust bindings use SQLite underneath. SQLite in WAL mode handles
concurrent *reads* safely, but two simultaneous *writers* corrupt the HNSW
index files (the Rust-native vector index is not SQLite-managed and has no
built-in cross-process locking).

Strategy
--------
- All WRITE operations (upsert_chunks, delete_by_source, and the WorkspaceStore
  constructor when it runs get_or_create_collection) acquire an exclusive flock
  on LOCK_FILE before touching ChromaDB.
- READ operations (query, count, sources) are safe without the lock because
  SQLite WAL + ChromaDB's read path are concurrent-read-safe.
- The lock is process-exclusive (LOCK_EX) and non-blocking (LOCK_NB) for CLI
  callers: if the write daemon (the `watch` service) already holds the lock,
  the CLI prints a warning and creates a SIGNAL_FILE so the daemon picks up
  the reindex request on its next poll.

Lock files
----------
  LOCK_FILE   = ~/.local/share/stratum-lens/write.lock   (flock target)
  SIGNAL_FILE = ~/.local/share/stratum-lens/reindex.signal  (daemon trigger)
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

HOME = Path(os.environ.get("HOME", "~"))
STATE_DIR = HOME / ".local/share/stratum-lens"
LOCK_FILE   = STATE_DIR / "write.lock"
SIGNAL_FILE = STATE_DIR / "reindex.signal"


@contextmanager
def write_lock(timeout_secs: float = 0.0):
    """
    Acquire an exclusive write lock on the ChromaDB store.

    If timeout_secs == 0 (default): non-blocking — raises LockHeld immediately
    if another process holds the lock.

    If timeout_secs > 0: retry up to timeout_secs before raising LockHeld.

    Usage:
        try:
            with write_lock():
                store.upsert_chunks(chunks)
        except LockHeld:
            # Service is running — signal it to reindex instead
            signal_reindex()
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        deadline = time.monotonic() + timeout_secs
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break  # acquired
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    lock_fd.close()
                    raise LockHeld(
                        "ChromaDB write lock is held by another process "
                        "(stratum-lens service is running). "
                        "Signal it to reindex: create_reindex_signal()"
                    )
                time.sleep(0.5)
        # Write our PID into the lock file so debugging is easy
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        lock_fd.close()


class LockHeld(RuntimeError):
    """Raised when the ChromaDB write lock is held by another process."""
    pass


def signal_reindex() -> None:
    """
    Create the signal file that tells the running `watch` daemon to reindex.
    Safe to call even if the daemon isn't running — it's just a file.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SIGNAL_FILE.write_text(str(time.time()))


def check_and_clear_signal() -> bool:
    """
    Check whether the reindex signal is pending; clear it if so.
    Called by the `watch` daemon on each poll cycle.
    Returns True if a reindex was signalled.
    """
    if SIGNAL_FILE.exists():
        try:
            SIGNAL_FILE.unlink()
        except FileNotFoundError:
            pass
        return True
    return False


def is_lock_held() -> bool:
    """Return True if another process currently holds the write lock."""
    if not LOCK_FILE.exists():
        return False
    try:
        fd = open(LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        return False
    except BlockingIOError:
        return True
