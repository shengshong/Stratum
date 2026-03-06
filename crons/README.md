# Stratum Cron Jobs

`seed-crons.sh` installs the canonical Stratum background job set into OpenClaw.

## Usage

```bash
# Install with default timezone (America/New_York)
bash crons/seed-crons.sh

# Custom timezone
bash crons/seed-crons.sh --tz "America/Chicago"

# Preview without installing
bash crons/seed-crons.sh --dry-run
```

## Canonical Job Set

| Job | Schedule | Purpose |
|-----|----------|---------|
| Knowledge-Consolidation | 3:00 AM daily | Belief decay, FTS5 rebuild, memory tier check |
| Weekly-Self-Reflection | Sunday 2:00 AM | Core file review, SOUL.md updates |
| World-Model-Sync | 3:15 AM daily | Sync dynamic beliefs from project state |
| Continuity-Checkpoint-Loop | Every 2 hours | Session snapshot + drift analysis |
| Session-Primer-Refresh | 7:30 AM daily | Regenerate next-session start brief |
| Cron-Health-Check | 9:00 AM daily | Detect silent job failures |
| Version-Drift-Check | Monday 10:00 AM | Node/OpenClaw/Stratum version parity |
| Ops-Queue-Check | 8:00 AM daily | Surface pending privileged ops for approval |
| Cron-Cleanup | Sunday 4:00 AM | Remove completed one-shot jobs |
| Daily-Autonomous-Research | 5:00 AM daily | Work research queue |
| Lens-Index-Rebuild | 2:30 AM daily | Incremental semantic index update |

## Adding Your Own

```bash
openclaw cron add \
  --name "My-Custom-Job" \
  --cron "0 9 * * 1" \
  --timezone "America/New_York" \
  --session isolated \
  --model "anthropic/claude-sonnet-4-6" \
  --message "Your task description here"
```

## Notes

- All jobs run in isolated sessions (separate from your main chat session)
- Use `--no-deliver` on status-check jobs to prevent routine spam to your channel
- Jobs intended to notify you should use `--announce`
- Never restart the OpenClaw gateway from within an active session — it kills the session before the reply sends
