# Stratum — Architecture

## Philosophy

Stratum is built on three principles:

1. **Structured > unstructured.** Plain text memory files are better than nothing, but a queryable knowledge graph with typed beliefs and confidence scores is better than plain text. Stratum moves the agent's self-knowledge from prose into structured, queryable form.

2. **Persistent > ephemeral.** Every lesson, every stash note, every goal, every cron outcome is stored in SQLite. Session restarts don't reset what the agent knows.

3. **Emergent capability > designed features.** The most valuable behaviors in Stratum weren't planned — they emerged from connecting well-designed primitives. `stratum-brain query` searches lessons, stash, cron history, and the lens index simultaneously. That cross-tool search wasn't a feature request; it fell out of the architecture.

---

## Module Breakdown

### stratum-mind (Rust)

The knowledge store. Unified SQLite database (`mind.db`) with:

- **Lessons** — captured errors, corrections, discoveries, workflow notes. Severity, category, resolution status.
- **Stash** — persistent scratch notes. Anything that doesn't fit elsewhere but needs to survive a restart.
- **Goals** — hierarchical goal tree with evaluation history and completion tracking.
- **Knowledge graph** — entities, typed relations, timestamped beliefs with confidence scores.
- **Memory tiers** — hot (MEMORY.md), warm (`memory/warm/`), cold (daily notes). Governance commands check budget and suggest demotions.

FTS5 full-text search is built into mind.db for fast cross-table queries.

### stratum-watch (Rust)

Observability layer. SQLite database (`watch.db`) with:

- **Cron health** — tracks outcomes of every cron job run. Classifies as success/failure/unknown based on output patterns. Detects silent failures.
- **Context window monitoring** — watches OpenClaw session context size, triggers pre-compaction checkpoints.
- **Version drift** — tracks installed versions of Node, OpenClaw, Stratum modules across hosts. Alerts when versions diverge.

Uses `inotify` on Linux for efficient file watching.

### stratum-ops (Rust)

Operations management. SQLite database (`ops.db`) with:

- **Privileged op queue** — commands requiring elevated permissions are queued here, not run automatically. Human approval required via `--elevated` flag.
- **Cron cleanup** — identifies and removes completed one-shot cron jobs.
- **Preflight checks** — validates environment before major operations.

The explicit design goal: nothing with elevated permissions runs without a human seeing it first.

### stratum-brain (Python)

The integration hub. No database of its own — aggregates all other modules.

Key capabilities:
- **Heartbeat** — runs all module health checks in sequence, returns structured alerts.
- **Hybrid FTS5 search** — queries lessons, stash, cron history, and lens simultaneously.
- **Belief decay** — reduces confidence on old beliefs that haven't been reinforced.
- **BFS graph traversal** — walks the knowledge graph to surface related entities.
- **Nightly consolidation** — full knowledge synthesis pass that runs while you sleep.

This is the only module most users interact with directly day-to-day.

### stratum-lens (Python)

Semantic search using local embeddings.

- Model: `all-MiniLM-L6-v2` via `fastembed` (ONNX Runtime — no PyTorch, no GPU required, ARM64-compatible)
- Vector store: ChromaDB (local, persistent)
- Indexes: workspace files, daily notes, all module feeds, document library
- Auto-scaling: triggers full reindex when memory pressure exceeds threshold

Supports semantic queries ("what did I learn about X?") that keyword search misses.

### stratum-continuity (Python)

Session continuity.

- **Snapshots** — every 2 hours, saves a structured summary of current session state.
- **Drift analysis** — compares snapshots across time to detect behavioral drift.
- **Primer injection** — generates a concise "start-of-session brief" that orients the agent on active context.
- **Lesson autopilot** — automatically resolves lessons that match predefined patterns.

### stratum-reports (Python)

Document management.

- Tracks a library of long-form documents (reports, research, analysis).
- Ingest pipeline: extracts entities and insights, adds them to `stratum-mind` knowledge graph.
- Validator: checks new documents against quality criteria before archiving.
- Timeline: tracks what's ingested, what's pending.

### stratum-agent-monitor (Rust)

Coding agent session monitoring. Watches for Claude Code / Codex sessions that stall at interactive prompts ("Do you want to proceed?") and sends notifications. Supports completion detection and structured handoff on session end.

### stratum-boot-health (Rust)

Security stack verification (Linux x86_64 only).

Checks at boot:
- Secure Boot enabled and active
- Lockdown mode (if kernel supports it)
- MOK (Machine Owner Key) enrollment and trust status
- DKMS module signing — all out-of-tree modules signed correctly
- `systemd` failed units from last boot

Runs as a systemd oneshot service. Writes findings to a feed file that `stratum-brain` ingests.

---

## Data Flow

```
External world (Telegram, RSS, API calls)
    ↓
OpenClaw gateway
    ↓
Heartbeat trigger → stratum-brain
    ↓
stratum-brain fans out to all modules
    ↓
Results aggregated into structured alerts
    ↓
Alerts surfaced to user if needs_attention=true
    ↓
Outcomes → lessons → mind.db
    ↓
Nightly consolidation → beliefs updated, stale data decayed
    ↓
stratum-lens index → semantic search updated
    ↓
Next session: stratum-brain query surfaces relevant prior context
```

---

## Dual-Host Setup (Optional)

Stratum supports active/standby dual-host operation:

- **Primary host** runs the gateway and all active cron jobs.
- **Standby host** keeps the workspace in sync (via NFS or rsync) and can take over at any time.
- Switching is manual — there's no automatic failover. The operator (or a migration script) handles the switch.

**Important:** Only one host should run the gateway at a time. SQLite databases must not be shared over NFS between hosts (use separate DB files per host; workspace files sync, DB files don't).

See [`docs/dual-host.md`](dual-host.md) for setup instructions.

---

## Unified Database Schema Overview

### mind.db

```sql
lessons(id, content, category, severity, resolved, created_at, resolved_at)
stash(id, content, priority, resolved, created_at)
goals(id, title, parent_id, priority, status, created_at)
goal_evaluations(id, goal_id, note, created_at)
entities(id, name, type, description, created_at)
relations(id, source_id, target_id, relation_type, created_at)
beliefs(id, entity_id, key, value, confidence, evidence, created_at, updated_at)
memory_tiers(id, key, tier, path, word_count, last_accessed)
consolidation_log(id, run_at, lessons_processed, beliefs_decayed, summary)
-- FTS5 virtual tables for full-text search across all above
```

### watch.db

```sql
cron_runs(id, job_name, run_at, outcome, confidence, output_snippet)
observations(id, type, value, recorded_at)
version_checks(id, component, version, host, checked_at)
```

### ops.db

```sql
op_queue(id, command, reason, status, elevated, created_at, applied_at)
```

---

## Design Decisions

**Why Rust for the knowledge modules?**
Startup time. `stratum-mind lesson list` needs to be fast enough to run in a heartbeat without adding noticeable latency. Rust binaries start in milliseconds; Python with imports takes 300–800ms.

**Why Python for brain/lens/continuity?**
These modules have heavy dependencies (ChromaDB, fastembed, numpy) that are impractical to compile into Rust. The trade-off is acceptable because they're called less frequently.

**Why SQLite instead of a proper graph DB or vector DB?**
Portability and zero-ops. SQLite works on any machine without a running server process. FTS5 handles the search case adequately. ChromaDB handles vectors. A "proper" graph DB would add operational complexity without proportional benefit at this scale.

**Why local embeddings instead of OpenAI/Cohere?**
Privacy. Workspace content, lessons, and notes contain sensitive operational context. Sending that to an external embedding API is an unnecessary privacy risk when `all-MiniLM-L6-v2` works well locally.
