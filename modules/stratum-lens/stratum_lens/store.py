"""
store.py — ChromaDB vector store wrapper for stratum-lens.

Purpose: Persist document chunks as embeddings in a local ChromaDB collection.
  Provides upsert (insert-or-update) and semantic query operations.

Design:
  - Persistent ChromaDB at ~/.local/share/stratum-lens/chroma/
  - Single collection: "workspace" — all chunks from all indexed files go here
  - Embedding model: all-MiniLM-L6-v2 via fastembed (ONNX Runtime, CPU-only)
    (22MB ONNX model, offline after first download, ~256-token max input)
    No PyTorch dependency — fastembed uses onnxruntime-cpu, ~150MB total install.
  - Document IDs are deterministic: sha256(source_path + section_title + approx_line)
    so re-indexing a file replaces its old chunks cleanly via upsert.
  - Metadata stored per chunk: source_path, section_title, approx_line, mtime

ChromaDB persistence note:
  ChromaDB uses SQLite for the metadata store and HNSW for the vector index.
  Both are local files — no network calls after the ONNX model is cached.

fastembed vs sentence-transformers:
  sentence-transformers pulls in PyTorch (~900MB).
  fastembed pulls in onnxruntime-cpu (~150MB total), identical model weights.
  Performance is the same; inference is marginally faster via ONNX graph optimization.
  Model cache: ~/.cache/fastembed/ (first run downloads the ONNX file, ~22MB).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import NamedTuple

import chromadb
from chromadb import EmbeddingFunction, Documents, Embeddings

from .chunker import Chunk
from .lock import write_lock, LockHeld, signal_reindex  # noqa: F401 (re-exported for CLI use)


# ─── Path Configuration ──────────────────────────────────────────────────────

def chroma_persist_dir() -> Path:
    """Return the ChromaDB persistence directory, creating it if needed."""
    base = Path(os.environ.get("HOME", "/")) / ".local/share/stratum-lens/chroma"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ─── Embedding Function ───────────────────────────────────────────────────────

class _FastEmbedFn(EmbeddingFunction[Documents]):
    """
    ChromaDB embedding function backed by fastembed (ONNX Runtime, CPU-only).

    Model: all-MiniLM-L6-v2
      - 22MB ONNX file, fast inference (~3-8ms/chunk on CPU via ONNX graph)
      - 256-token max input, 384-dimensional output
      - Strong semantic similarity for English prose
      - Model cache: ~/.cache/fastembed/

    Privacy: zero network calls during inference. ONNX downloaded once on first run.

    ChromaDB 1.x note: The collection registry stores the embedding function name
    returned by name() for conflict detection on re-open. We return a stable string
    so repeated opens of the same collection don't raise ValueError.
    """

    MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self) -> None:
        # Lazy import so the CLI starts fast even if fastembed takes a moment
        from fastembed import TextEmbedding  # type: ignore[import]
        self._model = TextEmbedding(model_name=self.MODEL_NAME)

    @classmethod
    def name(cls) -> str:
        """Stable identifier stored in the ChromaDB collection registry."""
        return "clawd_fastembed_minilm_l6"

    def __call__(self, input: Documents) -> Embeddings:
        """Embed a batch of documents. Returns list of float vectors."""
        return [vec.tolist() for vec in self._model.embed(input)]


def _embedding_fn() -> _FastEmbedFn:
    """Return a singleton-style embedding function for the lifetime of the process."""
    return _FastEmbedFn()


# ─── Store Class ──────────────────────────────────────────────────────────────

class QueryResult(NamedTuple):
    """A single result from a semantic query."""
    score: float          # cosine similarity (0–1, higher is more similar)
    source_path: str      # absolute path of the source file
    section_title: str    # section/header this chunk belongs to
    approx_line: int      # approximate line number in source file
    text: str             # the chunk text itself


class WorkspaceStore:
    """
    Persistent semantic index of workspace markdown chunks.

    Usage:
        store = WorkspaceStore()
        store.upsert_chunks(chunks)
        results = store.query("PHANTOM PROTOCOL current status", top_k=5)
    """

    COLLECTION_NAME = "workspace"

    def __init__(self) -> None:
        persist_dir = chroma_persist_dir()
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._ef = _embedding_fn()
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self._ef,
            metadata={
                "hnsw:space": "cosine",   # cosine distance for semantic similarity
                "description": "OpenClaw workspace semantic index",
            },
        )

    def upsert_chunks(self, chunks: list[Chunk]) -> int:
        """
        Insert or update a list of chunks in the collection.

        Uses deterministic IDs so re-indexing replaces stale chunks cleanly.
        Returns the number of chunks upserted.
        """
        if not chunks:
            return 0

        ids = [_chunk_id(c) for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [
            {
                "source_path": c.source_path,
                "section_title": c.section_title,
                "approx_line": c.approx_line,
            }
            for c in chunks
        ]

        # ChromaDB upsert: insert if new, update if ID exists
        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        return len(chunks)

    def delete_by_source(self, source_path: str) -> int:
        """
        Remove all chunks from a specific source file.
        Called before re-indexing a modified file to avoid stale chunks.
        """
        results = self._collection.get(
            where={"source_path": source_path},
            include=[],  # only need IDs
        )
        ids = results.get("ids", [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def query(self, text: str, top_k: int = 8) -> list[QueryResult]:
        """
        Semantic query: find the top_k most similar chunks to `text`.

        Returns results sorted by descending cosine similarity.
        Score of 1.0 = identical, 0.0 = completely unrelated.
        """
        if not text.strip():
            return []

        count = self.count()
        if count == 0:
            return []

        raw = self._collection.query(
            query_texts=[text],
            n_results=min(top_k, count),
            include=["documents", "metadatas", "distances"],
        )

        results: list[QueryResult] = []
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        dists = raw.get("distances", [[]])[0]

        for doc, meta, dist in zip(docs, metas, dists):
            # ChromaDB cosine space: distance ∈ [0,2], 0 = identical.
            # Convert to similarity ∈ [0,1]: similarity = 1 - dist/2
            similarity = max(0.0, 1.0 - dist / 2.0)
            results.append(QueryResult(
                score=round(similarity, 4),
                source_path=meta.get("source_path", "unknown"),
                section_title=meta.get("section_title", ""),
                approx_line=int(meta.get("approx_line", 0)),
                text=doc,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def count(self) -> int:
        """Return the total number of chunks in the index."""
        return self._collection.count()

    def sources(self) -> list[str]:
        """Return a sorted list of all unique source file paths in the index."""
        if self.count() == 0:
            return []
        results = self._collection.get(include=["metadatas"])
        paths = {m.get("source_path", "") for m in results.get("metadatas", [])}
        return sorted(p for p in paths if p)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _chunk_id(chunk: Chunk) -> str:
    """
    Generate a deterministic ID for a chunk.

    ID is sha256(source_path + section_title + str(approx_line)) truncated to 16 hex chars.
    This means re-indexing a file produces the same IDs → upsert replaces cleanly.
    """
    key = f"{chunk.source_path}\x00{chunk.section_title}\x00{chunk.approx_line}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
