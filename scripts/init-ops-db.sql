-- init-ops-db.sql — Initialize stratum-ops database schema
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS op_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    elevated    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    applied_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_op_queue_status ON op_queue(status);
