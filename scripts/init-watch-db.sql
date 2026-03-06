-- init-watch-db.sql — Initialize stratum-watch database schema
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS cron_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name       TEXT NOT NULL,
    run_at         TEXT NOT NULL DEFAULT (datetime('now')),
    outcome        TEXT NOT NULL DEFAULT 'unknown',
    confidence     REAL DEFAULT 0.5,
    output_snippet TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    value       TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS version_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    component   TEXT NOT NULL,
    version     TEXT NOT NULL,
    host        TEXT NOT NULL DEFAULT 'local',
    checked_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cron_runs_job   ON cron_runs(job_name);
CREATE INDEX IF NOT EXISTS idx_cron_runs_at    ON cron_runs(run_at);
CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(type);
