"""
cli.py — stratum-brain command-line interface.

Commands:
  status     Rich dashboard of all six tools (now includes lessons)
  heartbeat  Structured check — returns alerts + auto-actions (for heartbeat use)
  query      Cross-tool semantic search (lens + stash + cron + lessons + world graph)
  checkpoint Force a context checkpoint now
  analyze    Pattern analysis: cron reliability, lesson trends, stash aging, correlations
  world      Knowledge graph commands: search, traverse, status, consolidate
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.markup import escape

from .sources import (
    get_context_status, get_latest_cron_per_job, get_stash_items,
    get_buffer_summary, lens_query, fmt_age,
    get_lesson_items, world_search, world_traverse, get_world_summary,
)
from .integrations import run_heartbeat_integrations, maybe_checkpoint

console = Console()

STATUS_COLORS = {
    "success": "green",
    "partial": "yellow",
    "failure": "red",
    "unknown": "dim",
}

LEVEL_COLORS = {
    "low": "dim",
    "medium": "cyan",
    "high": "yellow",
    "critical": "red",
    "urgent": "bold red",
    "inactive": "dim",
}

PRIORITY_COLORS = {
    "urgent": "bold red",
    "high": "yellow",
    "normal": "white",
    "low": "dim",
}


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


@click.group()
def main():
    """stratum-brain — unified status and integration hub for Stratum self-optimization tools."""
    pass


@main.command()
def status():
    """Full dashboard: context, cron health, stash, buffer."""
    console.print()
    console.rule("[bold cyan]🧠 CLAWD-BRAIN STATUS[/bold cyan]")
    console.print()

    # ── Context Watch ──
    ctx = get_context_status()
    if ctx.active:
        bar = _progress_bar(ctx.pct)
        level_color = LEVEL_COLORS.get(ctx.level, "white")
        ctx_text = (
            f"[{level_color}]{bar}[/{level_color}] "
            f"[{level_color}]{ctx.pct:.0f}%[/{level_color}] "
            f"(~{ctx.estimated_tokens:,} / {ctx.max_tokens:,} tokens) "
            f"[dim][{ctx.level}][/dim]"
        )
        if ctx.recommendation:
            ctx_text += f"\n[yellow]  ⚠ {ctx.recommendation}[/yellow]"
        ctx_text += f"\n[dim]  Updated {fmt_age(ctx.age_secs)}[/dim]"
    else:
        ctx_text = "[dim]No active session detected[/dim]"
    console.print(Panel(ctx_text, title="[bold]📊 Context Window[/bold]", border_style="cyan"))

    # ── Cron Health ──
    jobs = get_latest_cron_per_job()
    if jobs:
        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        tbl.add_column("Cron Job", style="white")
        tbl.add_column("Last Run", style="dim")
        tbl.add_column("Duration", style="dim")
        tbl.add_column("Status", justify="center")
        tbl.add_column("Signals", style="dim")
        for job in jobs:
            color = STATUS_COLORS.get(job.status, "white")
            icon = {"success": "✅", "failure": "❌", "partial": "⚠️", "unknown": "❓"}.get(job.status, "❓")
            dur = f"{job.duration_secs:.0f}s" if job.duration_secs else "—"
            sigs = ", ".join(job.signals[:3]) if job.signals else "none"
            tbl.add_row(
                job.cron_name,
                fmt_age(job.age_secs),
                dur,
                f"[{color}]{icon} {job.status}[/{color}]",
                sigs[:50],
            )
        console.print(Panel(tbl, title="[bold]⚙️  Cron Health[/bold]", border_style="cyan", padding=(0,1)))
    else:
        console.print(Panel("[dim]No cron health data yet — run: clawd-cron-health scan[/dim]",
                            title="[bold]⚙️  Cron Health[/bold]", border_style="cyan"))

    # ── Stash ──
    stash = get_stash_items()
    if stash:
        stash_lines = []
        for item in stash[:8]:
            color = PRIORITY_COLORS.get(item.priority, "white")
            stash_lines.append(
                f"[dim][{item.id}][/dim] [{color}]({item.priority}/{item.category})[/{color}] "
                f"{item.content[:70]}  [dim]{fmt_age(item.age_secs)}[/dim]"
            )
        if len(stash) > 8:
            stash_lines.append(f"[dim]  … {len(stash)-8} more[/dim]")
        console.print(Panel("\n".join(stash_lines),
                            title=f"[bold]📝 Stash ({len(stash)} pending)[/bold]",
                            border_style="cyan"))
    else:
        console.print(Panel("[dim]No pending stash items[/dim]",
                            title="[bold]📝 Stash[/bold]", border_style="cyan"))

    # ── Lessons ──
    SEVERITY_COLORS = {"critical": "bold red", "high": "yellow", "medium": "white", "low": "dim"}
    lessons = get_lesson_items(include_resolved=False)
    if lessons:
        lesson_lines = []
        for l in lessons[:6]:
            sev_color = SEVERITY_COLORS.get(l.severity, "white")
            src_note = f" [dim]({l.source})[/dim]" if l.source else ""
            lesson_lines.append(
                f"[dim][[{l.id}]][/dim] [{sev_color}]{l.severity}/{l.category}[/{sev_color}]"
                f"{src_note}  {escape(l.content[:65])}  [dim]{fmt_age(l.age_secs)}[/dim]"
            )
        if len(lessons) > 6:
            lesson_lines.append(f"[dim]  … {len(lessons)-6} more[/dim]")
        console.print(Panel("\n".join(lesson_lines),
                            title=f"[bold]📚 Lessons ({len(lessons)} unresolved)[/bold]",
                            border_style="cyan"))
    else:
        console.print(Panel("[dim]No unresolved lessons[/dim]",
                            title="[bold]📚 Lessons[/bold]", border_style="cyan"))

    # ── Buffer ──
    buf = get_buffer_summary()
    unacked_color = "red" if buf['unacked'] > 0 else "green"
    buf_text = (f"Total captured: [bold]{buf['total']}[/bold]   "
                f"Unacknowledged: [bold {unacked_color}]{buf['unacked']}[/bold {unacked_color}]")
    console.print(Panel(buf_text, title="[bold]📦 Buffer[/bold]", border_style="cyan"))

    # ── Research ──
    try:
        from pathlib import Path as _P
        import json as _json
        research_dir = _P.home() / "clawd/research"
        research_idx = research_dir / "index.json"
        queue_file   = research_dir / "queue.json"
        interests_file = research_dir / "interests.json"
        total_notes = len(_json.loads(research_idx.read_text())) if research_idx.exists() else 0
        queue_items = _json.loads(queue_file.read_text()) if queue_file.exists() else []
        interests = _json.loads(interests_file.read_text()) if interests_file.exists() else []
        now_ts = time.time()
        freq_secs = {"daily": 86400, "weekly": 604800, "monthly": 2592000}
        due_count = sum(1 for i in interests
                        if i.get("last_researched") is None
                        or (now_ts - i["last_researched"]) >= freq_secs.get(i.get("frequency","weekly"), 604800))
        queue_color = "yellow" if queue_items else "dim"
        due_color = "yellow" if due_count > 0 else "green"
        research_text = (
            f"Notes saved: [bold]{total_notes}[/bold]   "
            f"Queue: [{queue_color}]{len(queue_items)} pending[/{queue_color}]   "
            f"Due interests: [{due_color}]{due_count}/{len(interests)}[/{due_color}]"
        )
        console.print(Panel(research_text, title="[bold]🔬 Research[/bold]", border_style="cyan"))
    except Exception:
        pass

    # ── Continuity ──
    try:
        import json
        cb = Path.home() / ".local/bin/stratum-continuity"
        cf = Path.home() / ".local/share/stratum-continuity/feed.md"
        cs = Path.home() / ".local/share/stratum-continuity/status.json"
        lines = [f"{'✅' if cb.exists() else '❌'} continuity tool: {'installed' if cb.exists() else 'missing'}"]
        if cf.exists():
            age_h = int((time.time() - cf.stat().st_mtime) // 3600)
            lines.append(f"feed age: {age_h}h")
        else:
            lines.append("feed: missing")

        if cs.exists():
            try:
                data = json.loads(cs.read_text(encoding='utf-8'))
                flags = data.get('flags', [])
                hi = sum(1 for f in flags if f.get('severity') in ('high', 'critical'))
                lines.append(f"flags: {len(flags)} total ({hi} high/critical)")
            except Exception:
                lines.append("flags: unreadable")
        else:
            lines.append("flags: unknown (run stratum-continuity analyze)")

        console.print(Panel("\n".join(lines), title="[bold]🧠 Continuity[/bold]", border_style="magenta"))
    except Exception:
        pass

    # ── v5 Core Modules ──
    try:
        core_bins = {
            "stratum-mind":             Path.home() / ".local/bin/stratum-mind",
            "stratum-watch":            Path.home() / ".local/bin/stratum-watch",
            "stratum-ops":              Path.home() / ".local/bin/stratum-ops",
            "stratum-continuity":       Path.home() / ".local/bin/stratum-continuity",
            "stratum-lens":             Path.home() / ".local/bin/stratum-lens",
            "deep-report-validator":  Path.home() / ".local/bin/deep-report-validator",
        }
        lines = []
        now = time.time()
        for name, b in core_bins.items():
            ok = b.exists()
            icon = "✅" if ok else "❌"
            status = "installed" if ok else "MISSING"
            lines.append(f"{icon} {name}: {status}")
        # Deep report validator last result
        vpath = Path.home() / ".local/share/clawd-report-runbook/validator-status.json"
        if vpath.exists():
            try:
                import json as _json
                vdata = _json.loads(vpath.read_text())
                passed = vdata.get("passed", True)
                wc = vdata.get("word_count", 0)
                checked = vdata.get("checked_at", "")[:10]
                icon2 = "✅" if passed else "❌"
                lines.append(f"{icon2} last report: {'pass' if passed else 'FAIL'} · {wc:,} words · {checked}")
            except Exception:
                pass
        console.print(Panel("\n".join(lines), title="[bold]🛡️ v5 Core Modules[/bold]", border_style="cyan"))
    except Exception:
        pass

    # ── Last Reflection ──
    try:
        reflection_feed = Path.home() / ".local/share/stratum-brain/reflection-feed.md"
        if reflection_feed.exists():
            content = reflection_feed.read_text(encoding="utf-8")
            # Find the most recent reflection entry
            entries = [e.strip() for e in content.split("---") if "# Reflection" in e]
            if entries:
                last = entries[-1]
                # Extract date line
                date_line = next((l for l in last.splitlines() if "# Reflection" in l), "")
                # Extract first 2 lines of content after the date
                body_lines = [l for l in last.splitlines()
                              if l.strip() and "# Reflection" not in l and "##" not in l]
                preview = " | ".join(body_lines[:2])[:100] if body_lines else "(no summary)"
                ref_text = f"[dim]{date_line.replace('# Reflection — ', '')}[/dim]  {preview}"
                console.print(Panel(ref_text,
                                    title="[bold]🪞 Last Reflection[/bold]",
                                    border_style="cyan"))
    except Exception:
        pass

    # ── Lens Memory ──
    try:
        from .integrations import check_lens_memory
        lm = check_lens_memory()
        if lm.max_bytes > 0:
            pct = lm.pct
            mem_color = "red" if pct > 0.85 else ("yellow" if pct > 0.75 else "green")
            cur_mb = lm.current_bytes // (1024 * 1024)
            max_mb = lm.max_bytes // (1024 * 1024)
            lens_text = (
                f"Memory: [{mem_color}]{cur_mb}MB / {max_mb}MB ({pct:.0%})[/{mem_color}]"
            )
            if lm.alert and not lm.scaled:
                lens_text += f"\n[yellow]  ⚠ {lm.alert}[/yellow]"
            elif lm.scaled:
                lens_text += f"\n[green]  ✓ Auto-scaled: {lm.alert}[/green]"
            console.print(Panel(lens_text, title="[bold]🧲 Lens (ChromaDB)[/bold]", border_style="cyan"))
    except Exception:
        pass

    console.print()


@main.command()
@click.option("--json-out", is_flag=True, help="Output as JSON (for scripting)")
def heartbeat(json_out):
    """Run all cross-tool integration checks. Returns alerts and auto-actions."""
    result = run_heartbeat_integrations()

    if json_out:
        click.echo(json.dumps({
            "alerts": result.alerts,
            "recommendations": result.recommendations,
            "auto_actions": result.auto_actions,
            "context_pct": result.context_pct,
            "context_level": result.context_level,
            "cron_failures": result.cron_failures,
            "stash_pending": result.stash_pending,
            "buffer_unacked": result.buffer_unacked,
            "lesson_unresolved": result.lesson_unresolved,
            "needs_attention": result.needs_attention,
        }))
        return

    if result.auto_actions:
        console.print("[dim]Auto-actions:[/dim]")
        for a in result.auto_actions:
            console.print(f"  [cyan]→[/cyan] {a}")

    if result.alerts:
        for alert in result.alerts:
            console.print(f"[bold red]⚠ {alert}[/bold red]")

    if result.recommendations:
        for rec in result.recommendations:
            console.print(f"[yellow]• {rec}[/yellow]")

    if not result.needs_attention and not result.recommendations:
        console.print("[dim green]All systems nominal.[/dim green]")
    elif result.needs_attention:
        sys.exit(1)  # non-zero so heartbeat caller knows to surface this


@main.command()
@click.argument("query_text")
@click.option("--top", default=5, help="Number of results per source")
def query(query_text, top):
    """Cross-tool semantic + text search. Searches lens, stash, and cron health."""
    console.print(f"\n[bold cyan]🔍 Query:[/bold cyan] {query_text}\n")

    # Lens (semantic search over workspace files)
    lens_results = lens_query(query_text, top_k=top)
    if lens_results:
        tbl = Table(title="Workspace (stratum-lens)", box=box.SIMPLE, show_header=True)
        tbl.add_column("Score", style="cyan", width=6)
        tbl.add_column("Source", style="dim", width=25)
        tbl.add_column("Preview", style="white")
        for r in lens_results:
            tbl.add_row(f"{r.score:.3f}", r.source[:25], r.text_preview[:80])
        console.print(tbl)

    # Stash text search
    stash = get_stash_items(include_done=True)
    q_lower = query_text.lower()
    stash_matches = [i for i in stash if q_lower in i.content.lower()][:top]
    if stash_matches:
        tbl2 = Table(title="Stash (clawd-stash)", box=box.SIMPLE, show_header=True)
        tbl2.add_column("ID", width=4)
        tbl2.add_column("Priority", width=8)
        tbl2.add_column("Content")
        tbl2.add_column("Age", width=8)
        for i in stash_matches:
            done_mark = "[dim](done) [/dim]" if i.done else ""
            tbl2.add_row(str(i.id), i.priority, done_mark + i.content[:80], fmt_age(i.age_secs))
        console.print(tbl2)

    # Cron health search
    jobs = get_latest_cron_per_job()
    cron_matches = [j for j in jobs if q_lower in j.cron_name.lower() or
                    any(q_lower in s.lower() for s in j.signals)][:top]
    if cron_matches:
        tbl3 = Table(title="Cron Health (clawd-cron-health)", box=box.SIMPLE, show_header=True)
        tbl3.add_column("Job")
        tbl3.add_column("Status", width=10)
        tbl3.add_column("Age", width=8)
        tbl3.add_column("Signals")
        for j in cron_matches:
            color = STATUS_COLORS.get(j.status, "white")
            tbl3.add_row(j.cron_name, f"[{color}]{j.status}[/{color}]",
                         fmt_age(j.age_secs), ", ".join(j.signals[:3]))
        console.print(tbl3)

    # Lesson search (all lessons, including resolved)
    all_lessons = get_lesson_items(include_resolved=True)
    lesson_matches = [l for l in all_lessons if q_lower in l.content.lower()
                      or q_lower in l.source.lower()
                      or q_lower in l.category.lower()][:top]
    if lesson_matches:
        tbl4 = Table(title="Lessons (clawd-lesson)", box=box.SIMPLE, show_header=True)
        tbl4.add_column("ID", width=4)
        tbl4.add_column("Sev/Cat", width=16)
        tbl4.add_column("Source", width=14)
        tbl4.add_column("Content")
        tbl4.add_column("Age", width=8)
        for l in lesson_matches:
            resolved_mark = "[dim](resolved) [/dim]" if l.resolved else ""
            tbl4.add_row(str(l.id), f"{l.severity}/{l.category}",
                         l.source[:14], resolved_mark + escape(l.content[:70]), fmt_age(l.age_secs))
        console.print(tbl4)

    # Research notes — highlight separately (they come from lens but deserve distinction)
    research_lens = [r for r in lens_results if "/clawd/research/" in getattr(r, 'source_path', getattr(r, 'source', ''))]
    if research_lens:
        tbl5 = Table(title="Research Notes (clawd-research)", box=box.SIMPLE, show_header=True)
        tbl5.add_column("Score", style="cyan", width=6)
        tbl5.add_column("Note ID", style="dim", width=28)
        tbl5.add_column("Preview", style="white")
        for r in research_lens:
            note_id = Path(getattr(r, 'source_path', getattr(r, 'source', ''))).stem[:28]
            tbl5.add_row(f"{r.score:.3f}", note_id, r.text_preview[:80])
        console.print(tbl5)

    # World graph search (FTS5 + LIKE over beliefs and lessons in mind.db)
    world_results = world_search(query_text, limit=top)
    if world_results:
        tbl6 = Table(title="Knowledge Graph (stratum-mind world)", box=box.SIMPLE, show_header=True)
        tbl6.add_column("Kind", width=7)
        tbl6.add_column("Entity/Attr", width=22)
        tbl6.add_column("Value / Content")
        tbl6.add_column("Conf", width=5)
        for r in world_results:
            if r.kind == "belief":
                tbl6.add_row(
                    "[cyan]belief[/cyan]",
                    f"[bold]{r.entity}[/bold][{r.attribute}]",
                    r.value[:80],
                    f"{r.confidence:.1f}",
                )
            else:
                tbl6.add_row(
                    "[yellow]lesson[/yellow]",
                    r.attribute,   # "severity/category"
                    r.value[:80],
                    "",
                )
        console.print(tbl6)

    if not lens_results and not stash_matches and not cron_matches and not lesson_matches and not world_results:
        console.print("[dim]No results found.[/dim]")
    console.print()


@main.command()
def checkpoint():
    """Force a context checkpoint: dump stash state and re-index lens."""
    result = maybe_checkpoint()
    if result.triggered:
        console.print(f"[green]✅ Checkpoint saved.[/green]")
        console.print(f"   Context: {result.pct:.0f}% ({result.level})")
        console.print(f"   Stash items saved: {result.stash_dumped}")
        console.print(f"   Lens re-indexed: {'yes' if result.lens_reindexed else 'no'}")
    else:
        console.print(f"[dim]Checkpoint skipped: {result.message}[/dim]")


@main.command()
@click.option("--window", default="7d",
              help="Analysis window: 7d, 30d, all (default: 7d)")
@click.option("--json", "json_out", is_flag=True, help="Output raw JSON")
@click.option("--create-lessons", is_flag=True,
              help="Auto-create clawd-lesson entries from top recommendations")
def analyze(window, json_out, create_lessons):
    """Cross-tool pattern analysis: cron reliability, lesson trends, stash aging, correlations.

    Designed to be run weekly (or on-demand) once enough data has accumulated.
    Works immediately but is more useful after 1-2 weeks of normal cron operation.

    --create-lessons: converts top recommendations into searchable lesson entries,
    closing the loop between pattern analysis and the lesson knowledge base.
    """
    from .analyze import run_analysis
    from .sources import LESSON_BIN
    report = run_analysis(window)

    # ── Auto-create lessons from recommendations ──────────────────────────────
    if create_lessons and report.recommendations:
        created = []
        # Filter: skip meta-observations about the analysis tool itself
        # Only convert concrete, actionable operational recommendations
        SKIP_PATTERNS = [
            "data still sparse", "run again after", "analysis improves",
            "unresolved high/critical lesson", "lesson list --severity",
            "more cron cycles", "after more data",
        ]
        actionable = [
            rec for rec in report.recommendations
            if not any(skip in rec.lower() for skip in SKIP_PATTERNS)
            and len(rec) > 30   # skip trivially short observations
        ]
        for rec in actionable[:5]:  # top 5 actionable only
            text_lower = rec.lower()
            if any(w in text_lower for w in ["cron", "failure", "error", "broken", "flaky"]):
                category, severity = "correction", "high"
            elif any(w in text_lower for w in ["research", "queue", "domain", "pattern", "cluster"]):
                category, severity = "discovery", "medium"
            elif any(w in text_lower for w in ["stale", "pending", "aging", "old", "review"]):
                category, severity = "workflow", "medium"
            else:
                category, severity = "workflow", "low"

            lesson_text = f"[analyze/{window}] {rec}"
            r = subprocess.run(
                [str(LESSON_BIN), "learn", lesson_text,
                 "--category", category, "--severity", severity,
                 "--source", f"stratum-brain analyze {window}"],
                capture_output=True, text=True
            )
            if r.returncode == 0:
                created.append(f"[{severity}/{category}] {rec[:70]}")

        if created and not json_out:
            console.print(f"\n[green]✅ Created {len(created)} lesson(s) from analysis:[/green]")
            for entry in created:
                console.print(f"  [dim]•[/dim] {entry}")
        elif not json_out:
            console.print("\n[dim]No recommendations available to convert to lessons.[/dim]")

    if json_out:
        import dataclasses
        def _serialize(obj):
            if dataclasses.is_dataclass(obj):
                return dataclasses.asdict(obj)
            raise TypeError(f"Not serializable: {type(obj)}")
        click.echo(json.dumps(dataclasses.asdict(report), default=str, indent=2))
        return

    console.print()
    console.rule(f"[bold cyan]🔬 CLAWD-BRAIN ANALYSIS[/bold cyan] [dim](window: {window})[/dim]")
    console.print()

    # ── Cron Reliability ──
    if report.cron:
        tbl = Table(title="⚙️  Cron Reliability", box=box.SIMPLE, show_header=True, header_style="bold")
        tbl.add_column("Job")
        tbl.add_column("Runs", width=5)
        tbl.add_column("Rate", width=7)
        tbl.add_column("✅", width=4)
        tbl.add_column("⚠️", width=4)
        tbl.add_column("❌", width=4)
        tbl.add_column("Streak")
        tbl.add_column("Signals", style="dim")
        for r in report.cron:
            rate_color = "green" if r.success_rate >= 0.8 else ("yellow" if r.success_rate >= 0.5 else "red")
            tbl.add_row(
                r.name,
                str(r.total_runs),
                f"[{rate_color}]{r.success_rate:.0%}[/{rate_color}]",
                str(r.success),
                str(r.partial),
                str(r.failure),
                r.streak_current,
                ", ".join(r.last_signals[:2])[:30],
            )
        console.print(Panel(tbl, border_style="cyan"))
    else:
        console.print(Panel("[dim]No cron health data in this window.[/dim]",
                            title="⚙️  Cron Reliability", border_style="cyan"))

    # ── Lesson Distribution ──
    ld = report.lessons
    if ld.total > 0:
        lesson_lines = []
        lesson_lines.append(
            f"Total: [bold]{ld.total}[/bold]  "
            f"Unresolved: [bold yellow]{ld.unresolved}[/bold yellow]  "
            f"Resolved: [bold green]{ld.resolved}[/bold green]  "
            f"Resolution rate: [cyan]{ld.resolution_rate:.0%}[/cyan]"
        )
        if ld.oldest_unresolved_days > 0:
            lesson_lines.append(f"Oldest unresolved: [dim]{ld.oldest_unresolved_days:.1f} days[/dim]")
        if ld.by_category:
            cat_str = "  ".join(f"{k}: {v}" for k, v in sorted(ld.by_category.items(), key=lambda x: -x[1]))
            lesson_lines.append(f"By category: [dim]{cat_str}[/dim]")
        if ld.by_severity:
            sev_order = ["critical", "high", "medium", "low"]
            sev_str = "  ".join(
                f"{k}: {ld.by_severity[k]}"
                for k in sev_order if k in ld.by_severity
            )
            lesson_lines.append(f"By severity: [dim]{sev_str}[/dim]")
        if ld.top_sources:
            src_str = "  ".join(f"{s}: {n}" for s, n in ld.top_sources)
            lesson_lines.append(f"Top sources: [dim]{src_str}[/dim]")
        if ld.recent_unresolved:
            lesson_lines.append("")
            lesson_lines.append("[dim]Recent unresolved:[/dim]")
            for (lid, sev, content) in ld.recent_unresolved:
                sev_color = "red" if sev == "critical" else ("yellow" if sev == "high" else "white")
                # Use [[…]] to escape literal brackets in Rich markup
                lesson_lines.append(f"  [[{lid}]] [{sev_color}]{sev}[/{sev_color}] {escape(content)}")
        console.print(Panel("\n".join(lesson_lines),
                            title="[bold]📚 Lesson Distribution[/bold]", border_style="cyan"))
    else:
        console.print(Panel("[dim]No lesson data in this window. Start recording: `lesson learn \"...\"`[/dim]",
                            title="[bold]📚 Lesson Distribution[/bold]", border_style="cyan"))

    # ── Stash Aging ──
    sa = report.stash
    if sa.total_pending > 0:
        stash_lines = [
            f"Pending: [bold]{sa.total_pending}[/bold]  "
            f">7d: [yellow]{sa.older_than_7d}[/yellow]  "
            f">30d: [red]{sa.older_than_30d}[/red]  "
            f"Oldest: [dim]{sa.oldest_item_days:.1f}d[/dim]"
        ]
        if sa.stalest_items:
            stash_lines.append("")
            stash_lines.append("[dim]Stalest items:[/dim]")
            for (sid, content, days) in sa.stalest_items:
                stash_lines.append(f"  [[{sid}]] [dim]{days:.1f}d[/dim] {content}")
        console.print(Panel("\n".join(stash_lines),
                            title="[bold]📝 Stash Aging[/bold]", border_style="cyan"))

    # ── Buffer Accumulation ──
    ba = report.buffer
    buf_text = (
        f"Total: [bold]{ba.total_entries}[/bold]  "
        f"Unacked: [bold]{ba.unacked}[/bold]  "
        f"Rate: [cyan]{ba.recent_rate_per_day:.1f}/day[/cyan] (7d avg)  "
        f"Oldest unacked: [dim]{ba.oldest_unacked_days:.1f}d[/dim]"
    )
    console.print(Panel(buf_text, title="[bold]📦 Buffer Accumulation[/bold]", border_style="cyan"))

    # ── Cross-Correlations ──
    if report.correlations:
        corr_lines = []
        for c in report.correlations:
            conf_color = "green" if c.confidence == "high" else ("yellow" if c.confidence == "medium" else "dim")
            corr_lines.append(
                f"[{conf_color}]●[/{conf_color}] [bold]{c.description}[/bold]\n"
                f"    [dim]{c.finding}[/dim]"
            )
        console.print(Panel("\n".join(corr_lines),
                            title="[bold]🔗 Cross-Correlations[/bold]", border_style="cyan"))

    # ── Recommendations ──
    if report.recommendations:
        rec_lines = [f"  [cyan]{i+1}.[/cyan] {r}" for i, r in enumerate(report.recommendations)]
        console.print(Panel("\n".join(rec_lines),
                            title="[bold]💡 Recommendations[/bold]", border_style="cyan"))
    else:
        console.print(Panel("[dim green]No immediate action items.[/dim green]",
                            title="[bold]💡 Recommendations[/bold]", border_style="cyan"))

    console.print(f"\n[dim]Analysis window: {window}  |  Generated: {time.strftime('%Y-%m-%d %H:%M ET')}[/dim]")
    console.print()


@main.command()
@click.option("--context-only", is_flag=True, help="Show reflection context without scheduling cron")
@click.option("--model", default="anthropic/claude-opus-4-6", help="Model for reflection sub-agent")
def reflect(context_only, model):
    """Deep self-reflection: analyzes core files + brain data, triggers Opus editing session.

    Reads SOUL.md, IDENTITY.md, LEARNING.md, MEMORY.md, USER.md, AGENTS.md, TOOLS.md,
    HEARTBEAT.md — then schedules an Opus sub-agent to reflect, update, and grow.

    Runs automatically weekly (Sunday 2 AM) via cron. Call manually anytime.
    """
    from .reflect import get_reflection_context, schedule_reflection_cron

    console.print()
    console.rule("[bold cyan]🪞 SELF-REFLECTION[/bold cyan]")
    console.print()

    ctx = get_reflection_context()

    # Show context summary
    console.print(Panel(ctx.summary, title="[bold]Core File State[/bold]", border_style="cyan"))

    if ctx.missing_files:
        console.print(f"[red]⚠ Missing: {', '.join(ctx.missing_files)}[/red]")
    if ctx.stale_files:
        console.print(f"[yellow]⚠ Stale (>7d): {', '.join(ctx.stale_files)}[/yellow]")
    if ctx.large_files:
        console.print(f"[dim]📏 Large files: {', '.join(ctx.large_files)}[/dim]")

    if ctx.lesson_count_critical > 0:
        console.print(f"[bold red]⚠ {ctx.lesson_count_critical} critical unresolved lesson(s)[/bold red]")

    if context_only:
        console.print("\n[dim](--context-only: not scheduling reflection cron)[/dim]")
        return

    console.print()
    console.print(f"[cyan]→[/cyan] Scheduling reflection sub-agent ({model})...")
    result = schedule_reflection_cron(model=model)
    if result:
        console.print(f"[green]✅ {result}[/green]")
        console.print("[dim]The Opus agent will read all core files, reflect, and make targeted edits.[/dim]")
        console.print("[dim]Check results in: ~/.local/share/stratum-brain/reflection-feed.md[/dim]")
    else:
        console.print("[red]❌ Failed to schedule reflection cron[/red]")
    console.print()


@main.command()
def version():
    """Show stratum-brain version and tool availability."""
    from pathlib import Path
    home = Path.home()
    tools = {
        "clawd-buffer":           home / ".local/bin/clawd-buffer",
        "stratum-lens":             home / ".local/bin/stratum-lens",
        "stratum-mind":             home / ".local/bin/stratum-mind",
        "stratum-watch":            home / ".local/bin/stratum-watch",
        "stratum-ops":              home / ".local/bin/stratum-ops",
        "stratum-agent-monitor":    home / ".local/bin/stratum-agent-monitor",
        "stratum-reports":          home / ".local/bin/stratum-reports",
        "stratum-continuity":       home / ".local/bin/stratum-continuity",
        "stratum-lens":             home / ".local/bin/stratum-lens",
        "stratum-boot-health":      home / ".local/bin/stratum-boot-health",
    }
    console.print("\n[bold cyan]stratum-brain v0.2.0[/bold cyan]")
    console.print("\n[bold]Tool availability:[/bold]")
    for name, path in tools.items():
        status_str = "[green]✅ installed[/green]" if path.exists() else "[red]❌ missing[/red]"
        short_path = str(path).replace(str(home), "~")
        console.print(f"  {name:<25} {status_str}  [dim]{short_path}[/dim]")
    console.print()


# ── World / Goals / Context subcommands (added 2026-02-25) ──────────────────

@main.group(invoke_without_command=True)
@click.pass_context
def world(ctx):
    """
    Knowledge graph commands: search, traverse, verify, consolidate, status.

    \b
    Examples:
      stratum-brain world status
      stratum-brain world search "VeridianOS phase"
      stratum-brain world traverse VeridianOS --hops 2
      stratum-brain world consolidate
      stratum-brain world verify VeridianOS current_version
    """
    if ctx.invoked_subcommand is None:
        # default: show rich world status panel
        summ = get_world_summary()
        stale_str = f"  [yellow]{summ.stale_count} stale[/yellow]" if summ.stale_count else ""
        last_str  = f"  last consolidated: [dim]{summ.last_consolidated[:16]}[/dim]" if summ.last_consolidated else "  [dim]never consolidated[/dim]"
        text = (
            f"Entities: [bold]{summ.entity_count}[/bold]  "
            f"Relations: [bold]{summ.relationship_count}[/bold]  "
            f"Beliefs: [bold]{summ.belief_count}[/bold]{stale_str}\n"
            f"Low-confidence (<0.7): [yellow]{summ.low_confidence_count}[/yellow]"
            f"{last_str}"
        )
        console.print(Panel(text, title="[bold]🌍 World Model[/bold]", border_style="cyan"))


@world.command(name="search")
@click.argument("query_text")
@click.option("--limit", default=10, help="Max results per category.")
def world_search_cmd(query_text: str, limit: int):
    """FTS5 + LIKE hybrid search across beliefs and lessons in mind.db."""
    results = world_search(query_text, limit=limit)
    if not results:
        console.print("[dim]No results found.[/dim]")
        return
    beliefs = [r for r in results if r.kind == "belief"]
    lessons = [r for r in results if r.kind == "lesson"]
    if beliefs:
        tbl = Table(title=f"Beliefs matching '{query_text}'", box=box.SIMPLE)
        tbl.add_column("Entity[Attr]", width=28)
        tbl.add_column("Value")
        tbl.add_column("Conf", width=5)
        for r in beliefs:
            tbl.add_row(f"[bold]{r.entity}[/bold][{r.attribute}]", r.value[:70], f"{r.confidence:.1f}")
        console.print(tbl)
    if lessons:
        tbl2 = Table(title=f"Lessons matching '{query_text}'", box=box.SIMPLE)
        tbl2.add_column("ID", width=4)
        tbl2.add_column("Sev/Cat", width=16)
        tbl2.add_column("Content")
        for r in lessons:
            tbl2.add_row(str(r.id), r.attribute, r.value[:80])
        console.print(tbl2)


@world.command(name="traverse")
@click.argument("entity")
@click.option("--hops", default=2, help="Max BFS depth (default: 2).")
def world_traverse_cmd(entity: str, hops: int):
    """BFS graph traversal — show all entities reachable from ENTITY within N hops."""
    data = world_traverse(entity, hops)
    edges   = data["edges"]
    beliefs = data["beliefs"]
    nodes   = data["nodes"]
    if not edges:
        console.print(f"[dim]No connected entities found for '{entity}'.[/dim]")
        return
    tbl = Table(title=f"Graph traversal from '{entity}' (max {hops} hops)", box=box.SIMPLE)
    tbl.add_column("Hop", width=4)
    tbl.add_column("Subject", width=22)
    tbl.add_column("Predicate", style="dim", width=18)
    tbl.add_column("Object", width=22)
    for subj, pred, obj, hop in sorted(edges, key=lambda e: e[3]):
        tbl.add_row(str(hop), f"[bold]{subj}[/bold]", pred, f"[bold]{obj}[/bold]")
    console.print(tbl)
    if beliefs:
        tbl2 = Table(title="Beliefs in subgraph", box=box.SIMPLE)
        tbl2.add_column("Entity[Attr]", width=28)
        tbl2.add_column("Value")
        tbl2.add_column("Conf", width=5)
        for ent, attr, val, conf in beliefs:
            tbl2.add_row(f"[bold]{ent}[/bold][{attr}]", val[:60], f"{conf:.1f}")
        console.print(tbl2)
    console.print(f"\n[dim]{len(nodes) - 1} node(s) reachable from '{entity}'.[/dim]")


@world.command(name="verify")
@click.argument("entity")
@click.argument("attribute")
@click.option("--confidence", default=1.0, help="New confidence value (default: 1.0).")
def world_verify_cmd(entity: str, attribute: str, confidence: float):
    """Mark a belief as verified — resets its decay clock and confidence."""
    from .sources import WORLD_BIN
    import subprocess
    result = subprocess.run(
        [str(WORLD_BIN), "world", "verify", entity, attribute, "--confidence", str(confidence)],
        capture_output=True, text=True,
    )
    click.echo(result.stdout or result.stderr)


@world.command(name="consolidate")
@click.option("--decay-days", default=30, help="Days before beliefs start decaying.")
@click.option("--stale-threshold", default=0.3, help="Confidence below which beliefs are marked stale.")
@click.option("--dry-run", is_flag=True, help="Preview without writing changes.")
def world_consolidate_cmd(decay_days: int, stale_threshold: float, dry_run: bool):
    """Nightly consolidation: decay unverified beliefs, rebuild FTS5 indices."""
    from .sources import WORLD_BIN
    import subprocess
    args = [str(WORLD_BIN), "world", "consolidate",
            "--decay-days", str(decay_days),
            "--stale-threshold", str(stale_threshold)]
    if dry_run:
        args.append("--dry-run")
    result = subprocess.run(args, capture_output=True, text=True)
    click.echo(result.stdout or result.stderr)
    # After real consolidation, refresh the world feed for lens indexing
    if not dry_run and result.returncode == 0:
        from .integrations import update_world_feed
        update_world_feed()
        console.print("[dim]World feed updated for lens indexing.[/dim]")


@world.command(name="log")
def world_log_cmd():
    """Show consolidation run history."""
    from .sources import WORLD_BIN
    import subprocess
    result = subprocess.run([str(WORLD_BIN), "world", "consolidate-log"],
                            capture_output=True, text=True)
    click.echo(result.stdout or result.stderr)


@main.command()
def goals():
    """Show active goals summary (stratum-mind goals list)."""
    from .sources import GOALS_BIN  # now points to stratum-mind
    if not GOALS_BIN.exists():
        console.print("[yellow]stratum-mind not installed.[/yellow]")
        return
    import subprocess
    result = subprocess.run([str(GOALS_BIN), "goals", "list", "--status", "active"], capture_output=True, text=True)
    click.echo(result.stdout or result.stderr)


@main.command()
@click.option("--budget", default=2000, type=int, help="Token budget for context block.")
def context(budget: int):
    """Assemble full session context (goals + world + lessons + stash)."""
    from .integrations import assemble_session_context
    output = assemble_session_context(token_budget=budget)
    click.echo(output)
