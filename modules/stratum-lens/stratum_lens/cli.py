"""
cli.py — Click-based CLI for stratum-lens.

Commands:
  stratum-lens index [--force] [--reports] [--verbose]
      Scan workspace files and index any that have changed since last run.
      Use --force to re-index everything from scratch.

  stratum-lens query <text> [--top-k N] [--min-score F] [--compact]
      Semantic search across all indexed workspace files.
      Returns ranked results with source file, section, and chunk text.

  stratum-lens status
      Show index statistics: total chunks, source file count, last indexed.

  stratum-lens sources
      List all source files currently in the index.

  stratum-lens watch [--interval N]
      Run continuous background indexing (polls every N seconds, default 60).
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from .indexer import IndexStats, run_index, run_watch
from .store import WorkspaceStore
from .lock import write_lock, LockHeld, signal_reindex, is_lock_held

console = Console()


@click.group()
@click.version_option("0.1.0", prog_name="stratum-lens")
def main() -> None:
    """
    stratum-lens — Semantic workspace indexer for OpenClaw.

    Query your memory files, daily notes, and workspace documents by meaning
    rather than keyword. All embeddings are computed locally (no cloud calls).

    Quick start:
      stratum-lens index          # index changed files
      stratum-lens query "PHANTOM PROTOCOL current state"
    """


@main.command()
@click.option("--force", is_flag=True, help="Re-index all files, ignoring mtime cache.")
@click.option("--reports", is_flag=True, help="Also index ~/.stratum-workspace/reports/markdown/.")
@click.option("--verbose", "-v", is_flag=True, help="Show per-file progress.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output (for scripted use).")
def index(force: bool, reports: bool, verbose: bool, quiet: bool) -> None:
    """Index workspace files (incremental by default).

    If the watch service is running and holds the write lock, this command
    creates a reindex signal file instead of opening ChromaDB directly —
    preventing concurrent-write corruption. The service picks up the signal
    on its next poll cycle (within 120 seconds).
    """
    try:
        with write_lock(timeout_secs=3):
            # We got the lock — service is not running, index directly
            store = WorkspaceStore()
            if force and not quiet:
                console.print("[yellow]Force mode: re-indexing all files.[/yellow]")

            with console.status("[cyan]Indexing workspace files...[/cyan]", spinner="dots"):
                stats: IndexStats = run_index(
                    store, force=force, include_reports=reports, verbose=verbose,
                )

            if not quiet:
                table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
                table.add_column("key", style="bold cyan", no_wrap=True)
                table.add_column("value")
                table.add_row("Files checked", str(stats.files_checked))
                table.add_row("Files indexed", f"[green]{stats.files_indexed}[/green]")
                table.add_row("Files skipped", f"[dim]{stats.files_skipped} (unchanged)[/dim]")
                table.add_row("Chunks added", f"[green]{stats.chunks_added}[/green]")
                table.add_row("Total in index", str(store.count()))
                console.print(Panel(table, title="[bold]Index complete[/bold]", border_style="cyan"))

            if stats.errors:
                console.print(f"\n[red]{len(stats.errors)} error(s):[/red]")
                for err in stats.errors:
                    console.print(f"  [red]✗[/red] {err}")

    except LockHeld:
        # Service is running and owns the lock — signal it to reindex instead
        signal_reindex()
        if not quiet:
            console.print(
                "[cyan]ℹ[/cyan]  stratum-lens service is running and holds the write lock.\n"
                "    Reindex signal sent — service will pick it up within 120 seconds.\n"
                "    (This prevents concurrent-write corruption of ChromaDB.)"
            )


@main.command()
@click.argument("text")
@click.option("--top-k", "-k", default=6, show_default=True, help="Number of results to return.")
@click.option(
    "--min-score", "-s", default=0.25, show_default=True,
    help="Minimum similarity score (0.0–1.0) to show a result."
)
@click.option("--compact", "-c", is_flag=True, help="Show one-line summaries instead of full chunks.")
def query(text: str, top_k: int, min_score: float, compact: bool) -> None:
    """
    Semantic search across indexed workspace files.

    Example:
      stratum-lens query "PHANTOM PROTOCOL test count"
      stratum-lens query "Synology SSH setup" --top-k 3
      stratum-lens query "morning briefing cron" --compact
    """
    store = WorkspaceStore()
    total = store.count()

    if total == 0:
        console.print("[yellow]Index is empty. Run [bold]stratum-lens index[/bold] first.[/yellow]")
        return

    results = store.query(text, top_k=top_k)
    results = [r for r in results if r.score >= min_score]

    if not results:
        console.print(
            f"[dim]No results above score {min_score:.2f}. "
            f"Try lowering --min-score or re-indexing.[/dim]"
        )
        return

    console.print(f"\n[bold cyan]Query:[/bold cyan] {text}")
    console.print(f"[dim]Searching {total:,} chunks · top {len(results)} results[/dim]\n")

    for i, r in enumerate(results, 1):
        # Score bar: visual indicator of similarity
        bar_width = 12
        filled = int(r.score * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        score_color = "green" if r.score >= 0.5 else "yellow" if r.score >= 0.35 else "dim"

        # Source label
        fname = Path(r.source_path).name
        label = f"{fname}"
        if r.section_title and r.section_title != fname:
            label += f" § {r.section_title[:50]}"
        if r.approx_line > 0:
            label += f"  [dim](~line {r.approx_line})[/dim]"

        console.print(
            f"[bold]{i}.[/bold]  [{score_color}]{bar} {r.score:.3f}[/{score_color}]  {label}"
        )

        if compact:
            # One-line preview
            preview = r.text.replace("\n", " ").strip()[:120]
            console.print(f"    [dim]{preview}…[/dim]\n")
        else:
            # Full chunk in a subtle panel
            console.print(
                Panel(
                    r.text.strip(),
                    border_style="dim",
                    padding=(0, 1),
                )
            )


@main.command()
def status() -> None:
    """Show index statistics."""
    store = WorkspaceStore()
    sources = store.sources()
    total_chunks = store.count()

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("key", style="bold cyan", no_wrap=True)
    table.add_column("value")
    table.add_row("Total chunks", f"{total_chunks:,}")
    table.add_row("Source files", str(len(sources)))
    table.add_row("Store location", _chroma_path())

    console.print(Panel(table, title="[bold]stratum-lens status[/bold]", border_style="cyan"))

    if total_chunks == 0:
        console.print("\n[yellow]Index is empty. Run [bold]stratum-lens index[/bold] to populate it.[/yellow]")


@main.command()
def sources() -> None:
    """List all source files currently in the index."""
    store = WorkspaceStore()
    source_list = store.sources()

    if not source_list:
        console.print("[dim]No sources indexed yet.[/dim]")
        return

    console.print(f"\n[bold]{len(source_list)}[/bold] source files in index:\n")
    for path in source_list:
        # Show path relative to home for readability
        try:
            rel = Path(path).relative_to(Path.home())
            display = f"~/{rel}"
        except ValueError:
            display = path
        console.print(f"  [cyan]•[/cyan] {display}")


@main.command()
@click.option("--interval", default=60, show_default=True, help="Poll interval in seconds.")
def watch(interval: int) -> None:
    """Run continuous background indexing (for use as a long-running process).

    This is the daemon mode — it is the SOLE writer to ChromaDB.
    It holds an exclusive write lock during each index pass and checks for
    reindex signals sent by CLI `index` calls that detected the lock being held.
    """
    store = WorkspaceStore()
    # Initial index pass (holding write lock)
    console.print("[cyan]Initial index pass...[/cyan]")
    with write_lock(timeout_secs=30):
        stats = run_index(store, force=False, verbose=True)
    console.print(str(stats))
    console.print()
    # Hand off to watch loop (which manages its own lock per cycle)
    run_watch(store, interval_seconds=interval)


def _chroma_path() -> str:
    home = os.environ.get("HOME", "~")
    return f"{home}/.local/share/stratum-lens/chroma"
