"""
chunker.py — Markdown document chunking for semantic indexing.

Purpose: Split workspace markdown files into semantically coherent chunks
  that are small enough to embed accurately but large enough to be useful
  as query results.

Design:
  - Split on Markdown headers (##, ###) as natural section boundaries.
  - Fall back to paragraph splitting (double newline) for large header-less
    sections to stay within embedding model token limits (~512 tokens).
  - Preserve source file path and approximate line number in metadata so
    the caller knows where to go to read more.
  - Each chunk carries: text, source_path, section_title, approx_line.

Target embedding model: all-MiniLM-L6-v2 (max 256 tokens, ~1000 chars)
We target chunks of 600–1200 characters to stay safely under the limit
while providing enough context per chunk to be useful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# Maximum characters per chunk before we sub-split at paragraph boundaries.
# all-MiniLM-L6-v2 has a 256-token limit; 1200 chars ≈ 250 tokens of dense prose.
MAX_CHUNK_CHARS = 1200

# Minimum chunk size — ignore chunks smaller than this (headers with no body, etc.)
MIN_CHUNK_CHARS = 60


@dataclass
class Chunk:
    """A single indexable unit of text extracted from a workspace file."""

    # The text content to embed and search
    text: str

    # Absolute path of the source file
    source_path: str

    # Section title (nearest parent markdown header, or filename if none)
    section_title: str

    # Approximate 1-indexed line number where this chunk begins
    approx_line: int

    # Human-friendly display label: "MEMORY.md § Goals"
    @property
    def display_label(self) -> str:
        fname = Path(self.source_path).name
        if self.section_title and self.section_title != fname:
            return f"{fname} § {self.section_title}"
        return fname


def chunk_file(path: Path) -> list[Chunk]:
    """
    Read a markdown file and split it into indexable chunks.

    Returns an empty list if the file cannot be read or produces no content.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    if not text.strip():
        return []

    chunks = list(_split_markdown(text, str(path)))
    # Filter out chunks that are too small to be useful
    return [c for c in chunks if len(c.text.strip()) >= MIN_CHUNK_CHARS]


def _split_markdown(text: str, source_path: str) -> Iterator[Chunk]:
    """
    Split markdown text into chunks at header boundaries, with paragraph-level
    fallback for large sections.

    Strategy:
      1. Split on ## or ### headers — these represent distinct topics in
         our workspace files (MEMORY.md, active-context.md, etc.)
      2. If a section's text exceeds MAX_CHUNK_CHARS, split further at
         double-newline (paragraph) boundaries, keeping the section title
         as context for all sub-chunks.
      3. Include the header text in each chunk so the embedding captures
         the topic, not just the body.
    """
    fname = Path(source_path).name

    # Regex to match markdown headers (# through ####)
    header_re = re.compile(r'^(#{1,4})\s+(.+)$', re.MULTILINE)

    # Find all header positions
    headers = list(header_re.finditer(text))

    if not headers:
        # No headers — split the whole file by paragraphs
        yield from _split_paragraphs(text, source_path, fname, line_offset=1)
        return

    # Process each section between consecutive headers
    for i, match in enumerate(headers):
        section_title = match.group(2).strip()
        section_start = match.start()
        section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section_text = text[section_start:section_end].strip()

        if not section_text:
            continue

        # Approximate line number of this section
        approx_line = text[:section_start].count('\n') + 1

        if len(section_text) <= MAX_CHUNK_CHARS:
            # Section fits in one chunk
            yield Chunk(
                text=section_text,
                source_path=source_path,
                section_title=section_title,
                approx_line=approx_line,
            )
        else:
            # Section is too large — split at paragraph boundaries,
            # prefixing each sub-chunk with the section header for context
            header_prefix = f"{match.group(0)}\n"
            body = section_text[len(match.group(0)):].strip()
            for chunk in _split_paragraphs(
                body, source_path, section_title, line_offset=approx_line,
                prefix=header_prefix
            ):
                yield chunk


def _split_paragraphs(
    text: str,
    source_path: str,
    section_title: str,
    line_offset: int,
    prefix: str = "",
) -> Iterator[Chunk]:
    """
    Split text at double-newline paragraph boundaries, grouping consecutive
    paragraphs together until MAX_CHUNK_CHARS is reached.
    """
    paragraphs = re.split(r'\n{2,}', text)
    current_parts: list[str] = []
    current_len = len(prefix)
    current_line = line_offset

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Would adding this paragraph exceed the limit?
        if current_parts and current_len + len(para) + 2 > MAX_CHUNK_CHARS:
            # Emit current accumulation as a chunk
            chunk_text = (prefix + "\n\n".join(current_parts)).strip()
            if len(chunk_text) >= MIN_CHUNK_CHARS:
                yield Chunk(
                    text=chunk_text,
                    source_path=source_path,
                    section_title=section_title,
                    approx_line=current_line,
                )
            current_line += sum(p.count('\n') + 2 for p in current_parts)
            current_parts = []
            current_len = len(prefix)

        current_parts.append(para)
        current_len += len(para) + 2

    # Emit any remaining content
    if current_parts:
        chunk_text = (prefix + "\n\n".join(current_parts)).strip()
        if len(chunk_text) >= MIN_CHUNK_CHARS:
            yield Chunk(
                text=chunk_text,
                source_path=source_path,
                section_title=section_title,
                approx_line=current_line,
            )
