"""
analyze.py — Cross-tool pattern analysis for stratum-brain.

Reads from all six data stores and surfaces trends, recurring failures,
lesson category distribution, stash aging, and cross-correlations.

Designed to accumulate value over time: run it weekly (or whenever the
data feels rich enough) to get actionable insights about what's actually
breaking, what lessons are being applied, and where attention should go.

Usage via CLI:
  stratum-brain analyze [--window 7d|30d|all] [--json]
"""

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOME = Path.home()
CRON_HEALTH_DB = HOME / ".local/share/clawd-cron-health/health.db"
STASH_DB       = HOME / ".local/share/clawd-stash/stash.db"
LESSON_DB      = HOME / ".local/share/clawd-lesson/lessons.db"
BUFFER_DB      = HOME / ".local/share/clawd-buffer/buffer.db"


def _window_secs(window: str) -> int | None:
    """Convert window string to seconds, or None for 'all'."""
    if window == "all":
        return None
    if window.endswith("d"):
        return int(window[:-1]) * 86400
    if window.endswith("h"):
        return int(window[:-1]) * 3600
    if window.endswith("w"):
        return int(window[:-1]) * 7 * 86400
    return None


# ─── Cron Reliability ────────────────────────────────────────────────────────

@dataclass
class CronReliability:
    name: str
    total_runs: int
    success: int
    partial: int
    failure: int
    unknown: int
    success_rate: float
    streak_current: str   # "3 success" / "2 failure" / "1 partial"
    last_signals: list[str]

def analyze_cron_reliability(window_secs: int | None = None) -> list[CronReliability]:
    """Compute per-job reliability stats over the given window."""
    if not CRON_HEALTH_DB.exists():
        return []
    try:
        con = sqlite3.connect(CRON_HEALTH_DB)
        now = int(time.time())
        cutoff = (now - window_secs) if window_secs else 0

        rows = con.execute("""
            SELECT cron_name, status, signals, ended_at
            FROM cron_runs
            WHERE cron_name != 'Unknown'
              AND ended_at >= ?
            ORDER BY cron_name, ended_at DESC
        """, (cutoff,)).fetchall()
        con.close()

        # Group by job name
        from collections import defaultdict
        by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for (name, status, signals_json, ended_at) in rows:
            by_name[name].append((status or "unknown", signals_json or "[]"))

        result = []
        for name, runs in by_name.items():
            counts = {"success": 0, "partial": 0, "failure": 0, "unknown": 0}
            for (status, _) in runs:
                counts[status if status in counts else "unknown"] += 1
            total = len(runs)
            rate = counts["success"] / total if total else 0.0

            # Current streak: count leading identical statuses
            streak_status = runs[0][0] if runs else "unknown"
            streak_count = 0
            for (s, _) in runs:
                if s == streak_status:
                    streak_count += 1
                else:
                    break

            # Last signals from most recent run
            last_sigs: list[str] = json.loads(runs[0][1]) if runs else []

            result.append(CronReliability(
                name=name,
                total_runs=total,
                success=counts["success"],
                partial=counts["partial"],
                failure=counts["failure"],
                unknown=counts["unknown"],
                success_rate=rate,
                streak_current=f"{streak_count} {streak_status}",
                last_signals=last_sigs[:3],
            ))

        result.sort(key=lambda r: r.success_rate)  # worst first
        return result
    except Exception:
        return []


# ─── Lesson Distribution ──────────────────────────────────────────────────────

@dataclass
class LessonDistribution:
    total: int
    unresolved: int
    resolved: int
    resolution_rate: float
    by_category: dict[str, int]
    by_severity: dict[str, int]
    oldest_unresolved_days: float
    top_sources: list[tuple[str, int]]   # [(source, count), ...]
    recent_unresolved: list[tuple[int, str, str]]  # [(id, severity, content[:80]), ...]

def analyze_lessons(window_secs: int | None = None) -> LessonDistribution:
    """Summarize lesson capture and resolution patterns."""
    if not LESSON_DB.exists():
        return LessonDistribution(0, 0, 0, 0.0, {}, {}, 0.0, [], [])
    try:
        con = sqlite3.connect(LESSON_DB)
        now = int(time.time())
        cutoff = (now - window_secs) if window_secs else 0

        rows = con.execute("""
            SELECT id, content, category, severity, source, created_at, resolved_at
            FROM lessons WHERE created_at >= ?
        """, (cutoff,)).fetchall()
        con.close()

        total = len(rows)
        resolved_rows = [r for r in rows if r[6] is not None]
        unresolved_rows = [r for r in rows if r[6] is None]

        by_cat: dict[str, int] = {}
        by_sev: dict[str, int] = {}
        sources: dict[str, int] = {}
        for r in unresolved_rows:
            cat = r[2] or "unknown"
            sev = r[3] or "unknown"
            src = r[4] or ""
            by_cat[cat] = by_cat.get(cat, 0) + 1
            by_sev[sev] = by_sev.get(sev, 0) + 1
            if src:
                sources[src] = sources.get(src, 0) + 1

        oldest_days = 0.0
        if unresolved_rows:
            oldest_ts = min(r[5] for r in unresolved_rows)
            oldest_days = (now - oldest_ts) / 86400

        top_sources = sorted(sources.items(), key=lambda x: -x[1])[:5]

        recent_unresolved = [
            (r[0], r[3], r[1][:80])
            for r in sorted(unresolved_rows, key=lambda r: r[5], reverse=True)[:5]
        ]

        rate = len(resolved_rows) / total if total else 0.0
        return LessonDistribution(
            total=total,
            unresolved=len(unresolved_rows),
            resolved=len(resolved_rows),
            resolution_rate=rate,
            by_category=by_cat,
            by_severity=by_sev,
            oldest_unresolved_days=oldest_days,
            top_sources=top_sources,
            recent_unresolved=recent_unresolved,
        )
    except Exception:
        return LessonDistribution(0, 0, 0, 0.0, {}, {}, 0.0, [], [])


# ─── Stash Aging ─────────────────────────────────────────────────────────────

@dataclass
class StashAging:
    total_pending: int
    older_than_7d: int
    older_than_30d: int
    by_category: dict[str, int]
    by_priority: dict[str, int]
    oldest_item_days: float
    stalest_items: list[tuple[int, str, float]]  # [(id, content[:60], days_old), ...]

def analyze_stash(window_secs: int | None = None) -> StashAging:
    """Analyze stash item age and distribution — surfaces things sitting too long."""
    if not STASH_DB.exists():
        return StashAging(0, 0, 0, {}, {}, 0.0, [])
    try:
        con = sqlite3.connect(STASH_DB)
        now = int(time.time())

        rows = con.execute("""
            SELECT id, content, category, priority, created_at
            FROM items WHERE done_at IS NULL
            ORDER BY created_at ASC
        """).fetchall()
        con.close()

        total = len(rows)
        older7  = sum(1 for r in rows if now - r[4] > 7  * 86400)
        older30 = sum(1 for r in rows if now - r[4] > 30 * 86400)

        by_cat: dict[str, int] = {}
        by_pri: dict[str, int] = {}
        for r in rows:
            c = r[2] or "note"
            p = r[3] or "normal"
            by_cat[c] = by_cat.get(c, 0) + 1
            by_pri[p] = by_pri.get(p, 0) + 1

        oldest_days = (now - rows[0][4]) / 86400 if rows else 0.0

        stalest = [
            (r[0], r[1][:60], (now - r[4]) / 86400)
            for r in rows[:5]  # already sorted oldest-first
        ]

        return StashAging(
            total_pending=total,
            older_than_7d=older7,
            older_than_30d=older30,
            by_category=by_cat,
            by_priority=by_pri,
            oldest_item_days=oldest_days,
            stalest_items=stalest,
        )
    except Exception:
        return StashAging(0, 0, 0, {}, {}, 0.0, [])


# ─── Buffer Accumulation ──────────────────────────────────────────────────────

@dataclass
class BufferAccumulation:
    total_entries: int
    unacked: int
    oldest_unacked_days: float
    recent_rate_per_day: float   # entries in last 7 days / 7

def analyze_buffer(window_secs: int | None = None) -> BufferAccumulation:
    """Analyze buffer accumulation rate and backlog."""
    if not BUFFER_DB.exists():
        return BufferAccumulation(0, 0, 0.0, 0.0)
    try:
        con = sqlite3.connect(BUFFER_DB)
        now_ms = int(time.time() * 1000)
        cutoff_ms = (now_ms - window_secs * 1000) if window_secs else 0

        total = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        unacked = con.execute(
            "SELECT COUNT(*) FROM results WHERE status = 'captured'"
        ).fetchone()[0]

        oldest_row = con.execute(
            "SELECT captured_at FROM results WHERE status = 'captured' ORDER BY captured_at ASC LIMIT 1"
        ).fetchone()
        oldest_days = (now_ms - oldest_row[0]) / 86400000 if oldest_row else 0.0

        # Rate over last 7 days
        week_ago_ms = now_ms - 7 * 86400 * 1000
        week_count = con.execute(
            "SELECT COUNT(*) FROM results WHERE captured_at >= ?", (week_ago_ms,)
        ).fetchone()[0]
        rate = week_count / 7.0

        con.close()
        return BufferAccumulation(
            total_entries=total,
            unacked=unacked,
            oldest_unacked_days=oldest_days,
            recent_rate_per_day=rate,
        )
    except Exception:
        return BufferAccumulation(0, 0, 0.0, 0.0)


# ─── Cross-Correlations ───────────────────────────────────────────────────────

@dataclass
class Correlation:
    description: str
    finding: str
    confidence: str  # "high" / "medium" / "low" (qualitative, not numeric)

def analyze_correlations(
    cron: list[CronReliability],
    lessons: LessonDistribution,
    stash: StashAging,
    buffer: BufferAccumulation,
) -> list[Correlation]:
    """Surface cross-tool correlations and inferences."""
    correlations: list[Correlation] = []

    # Cron failure → lesson gap
    failing_jobs = [r for r in cron if r.success_rate < 0.5 and r.total_runs >= 2]
    for job in failing_jobs:
        correlations.append(Correlation(
            description=f"Cron job '{job.name}' has {job.success_rate:.0%} success rate",
            finding=f"No lesson recorded for this failure pattern. Consider: "
                    f"`lesson learn \"[{job.name}] fails because...\" --category tool_failure`",
            confidence="medium",
        ))

    # Stash aging → completion drift
    if stash.older_than_7d > 3:
        correlations.append(Correlation(
            description=f"{stash.older_than_7d} stash items sitting >7d",
            finding="Stash items aging out suggests tasks are being captured but not acted on. "
                    "Consider bulk-review: `stash list`",
            confidence="high",
        ))

    # High lesson unresolved rate
    if lessons.total > 0 and lessons.resolution_rate < 0.3 and lessons.total >= 5:
        correlations.append(Correlation(
            description=f"Lesson resolution rate is {lessons.resolution_rate:.0%} "
                        f"({lessons.resolved}/{lessons.total})",
            finding="Most lessons are captured but not marked resolved. As patterns get "
                    "applied in practice, run `lesson resolve <id>` to track what's working.",
            confidence="medium",
        ))

    # Buffer backlog growth
    if buffer.unacked > 10:
        correlations.append(Correlation(
            description=f"Buffer has {buffer.unacked} unacknowledged entries",
            finding="Sub-agent outputs not being reviewed. Bulk-ack when appropriate: "
                    "`clawd-buffer ack <id>` or filter by label.",
            confidence="low",
        ))

    # No lessons from cron sources
    cron_names = {r.name.lower() for r in cron}
    lesson_sources = {s.lower() for s, _ in lessons.top_sources}
    untouched = cron_names - lesson_sources
    if untouched and lessons.total > 0:
        sample = list(untouched)[:3]
        correlations.append(Correlation(
            description=f"Cron jobs with no captured lessons: {', '.join(sample)}",
            finding="These jobs run regularly but no lessons have been tagged from them. "
                    "Worth a review pass after failures.",
            confidence="low",
        ))

    return correlations


# ─── Recommendations ─────────────────────────────────────────────────────────

def generate_recommendations(
    cron: list[CronReliability],
    lessons: LessonDistribution,
    stash: StashAging,
    buffer: BufferAccumulation,
    correlations: list[Correlation],
) -> list[str]:
    """Distill analysis into a short prioritized action list."""
    recs: list[str] = []

    # Cron reliability < 50%
    bad_cron = [r for r in cron if r.success_rate < 0.5 and r.total_runs >= 3]
    for r in bad_cron[:3]:
        recs.append(f"Fix '{r.name}': {r.success_rate:.0%} success ({r.failure} failures) — "
                    f"signals: {', '.join(r.last_signals) or 'none'}")

    # High-severity unresolved lessons
    crit = lessons.by_severity.get("critical", 0) + lessons.by_severity.get("high", 0)
    if crit > 0:
        recs.append(f"Review {crit} unresolved high/critical lesson(s): `lesson list --severity high`")

    # Stale stash
    if stash.older_than_30d > 0:
        recs.append(f"Prune {stash.older_than_30d} stash item(s) older than 30 days: `stash list --all`")

    # World model health: stale beliefs + consolidation freshness (v2 — 2026-03-05)
    try:
        from .sources import get_world_summary
        import datetime as _dt
        world = get_world_summary()
        if world.stale_count > 0:
            recs.append(
                f"{world.stale_count} stale belief(s) in world model — "
                f"run: stratum-brain world consolidate  OR  stratum-mind world consolidate"
            )
        if world.last_consolidated:
            try:
                last_run = _dt.datetime.fromisoformat(world.last_consolidated)
                age_h = (_dt.datetime.utcnow() - last_run).total_seconds() / 3600
                if age_h > 48:
                    recs.append(
                        f"Knowledge consolidation stale ({age_h:.0f}h ago) — "
                        f"run: stratum-mind world consolidate"
                    )
            except Exception:
                pass
        if world.low_confidence_count > 5:
            recs.append(
                f"{world.low_confidence_count} low-confidence belief(s) (<0.7) — "
                f"verify key beliefs: stratum-brain world verify <entity> <attribute>"
            )
    except Exception:
        pass

    # Data richness note (if window is short)
    if lessons.total < 5 or (cron and all(r.total_runs < 3 for r in cron)):
        recs.append("Data still sparse — analysis improves after 1-2 weeks of normal operation. "
                    "Run again after more cron cycles and lesson captures.")

    return recs


# ─── Main analyze entry point ────────────────────────────────────────────────

@dataclass
class AnalysisReport:
    window: str
    generated_at: int
    cron: list[CronReliability]
    lessons: LessonDistribution
    stash: StashAging
    buffer: BufferAccumulation
    correlations: list[Correlation]
    recommendations: list[str]

def run_analysis(window: str = "7d") -> AnalysisReport:
    """Run full cross-tool analysis. Returns a structured report."""
    window_secs = _window_secs(window)
    cron    = analyze_cron_reliability(window_secs)
    lessons = analyze_lessons(window_secs)
    stash   = analyze_stash(window_secs)
    buffer  = analyze_buffer(window_secs)
    corrs   = analyze_correlations(cron, lessons, stash, buffer)
    recs    = generate_recommendations(cron, lessons, stash, buffer, corrs)
    return AnalysisReport(
        window=window,
        generated_at=int(time.time()),
        cron=cron,
        lessons=lessons,
        stash=stash,
        buffer=buffer,
        correlations=corrs,
        recommendations=recs,
    )
