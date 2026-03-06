# HEARTBEAT.md — Proactive Agent Behavior

*This file defines what your agent does between conversations — during heartbeat polls
and background cron jobs. Edit to match your life and workflow.*

---

## Session Start (First Heartbeat Only)

```bash
stratum-continuity primer --check
# If it prints "inject": cat ~/.local/share/stratum/session-brief.md
# If it prints "skip": same session, brief already applied
```

---

## Every Heartbeat — Run First

```bash
stratum-brain heartbeat
```

This auto-handles:
- Cron failure detection → stash alerts
- Context pressure → checkpoint if ≥60%
- Feed indexing for lens
- Returns `needs_attention: true/false`

If `needs_attention: true` → surface to user. Otherwise continue silently.

---

## Periodic Checks (rotate 2–4× daily)

Track state in `memory/heartbeat-state.json`.

**Email** — Check for urgent/important unread messages. Alert only if something needs attention.

**Calendar** — Check upcoming events in next 2–4 hours. Remind if something is soon.

**Weather** — If morning or user might be going out, check local weather.

*(Add/remove based on what integrations you have configured)*

---

## Knowledge & File Maintenance (every 3–4 hours during active hours)

When triggered:

1. **Skills** — Review recent work/lessons. Update relevant files with new operational lessons.
2. **Core files** — Scan MEMORY.md, SOUL.md, AGENTS.md, HEARTBEAT.md, USER.md. Add/modify anything that reflects new decisions, tools, lessons. Remove stale info.
3. **Daily notes** — Ensure `memory/YYYY-MM-DD.md` captures today's significant work.
4. **Sync index** — `stratum-lens index` (incremental — only re-embeds changed files).
5. **Stash review** — `stratum-mind stash list` to surface pending items.

Keep edits surgical. If nothing meaningful happened since last run, skip silently.

---

## Before Any Complex Task

```bash
stratum-brain query "<topic>"
```

Surfaces relevant lessons, prior decisions, known gotchas. 3 seconds. Prevents re-learning what you already know.

---

## Lesson Capture (habit, not checklist)

When something teaches you something — immediately:

```bash
stratum-mind lesson learn "..." --category <correction|discovery|workflow|insight>
```

Don't defer to "I'll write it up later."

---

## Weekly Self-Reflection

```bash
stratum-brain reflect
```

Reads all core files, cross-references brain ecosystem data, updates SOUL.md and LEARNING.md with genuine shifts. Runs automatically Sunday 2 AM (via cron) — also run manually anytime.

---

## Rules

- Quiet hours: 23:00–08:00 local time (skip unless urgent)
- Don't repeat a check done < 30 minutes ago
- Only alert if something actually needs attention
- If nothing needs attention: reply `HEARTBEAT_OK`

---

## Proactive Work (no permission needed)

- Read and organize memory files
- Update documentation
- Run `stratum-brain heartbeat`
- Check `stratum-mind stash list`
- Review recent daily notes
- Run `stratum-lens index` after major file changes

*(Add anything specific to your workflow here)*
