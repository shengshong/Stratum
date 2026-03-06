// stratum-ops — Stratum operations manager
//
// Replaces: clawd-preflight, clawd-cron-reconcile, clawd-cron-cleanup
// Adds:     ops-queue — persistent queue for privileged operations that require sudo/root
//
// Database: ~/.local/share/stratum/ops.db
//
// Usage:
//   stratum-ops queue add "sudo cp /tmp/conf /etc/..." --reason "sshd fix" [--elevated]
//   stratum-ops queue list
//   stratum-ops queue apply <id>     # run queued op (requires elevated context)
//   stratum-ops queue done <id>      # mark manually completed
//   stratum-ops preflight run        # run preflight checklist
//   stratum-ops cron reconcile       # check for cron drift
//   stratum-ops cron cleanup         # prune completed one-shot crons
//   stratum-ops status               # dashboard

use anyhow::Result;
use clap::{Parser, Subcommand};
use colored::Colorize;
use dirs::data_dir;
use rusqlite::{Connection, params};
use std::path::PathBuf;
use std::process::Command;

fn db_path() -> PathBuf {
    data_dir().unwrap_or_else(|| PathBuf::from("~/.local/share"))
        .join("clawd").join("ops.db")
}

fn open_db() -> Result<Connection> {
    let path = db_path();
    std::fs::create_dir_all(path.parent().unwrap())?;
    let conn = Connection::open(&path)?;
    conn.execute_batch("PRAGMA journal_mode=WAL;")?;
    conn.execute_batch("
        CREATE TABLE IF NOT EXISTS op_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            command     TEXT NOT NULL,
            reason      TEXT,
            requires_elevated INTEGER NOT NULL DEFAULT 1,
            status      TEXT NOT NULL DEFAULT 'pending',  -- pending|applied|failed|cancelled
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            applied_at  TEXT,
            result      TEXT
        );
    ")?;
    Ok(conn)
}

#[derive(Parser)]
#[command(name = "stratum-ops", about = "Stratum operations manager", version = "0.1.0")]
struct Cli {
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Privileged operation queue
    Queue {
        #[command(subcommand)]
        action: QueueAction,
    },
    /// Preflight checks
    Preflight {
        #[command(subcommand)]
        action: PreflightAction,
    },
    /// Cron management
    Cron {
        #[command(subcommand)]
        action: CronAction,
    },
    /// Full status dashboard
    Status,
}

#[derive(Subcommand)]
enum QueueAction {
    /// Queue a privileged operation
    Add {
        command: String,
        #[arg(long)]
        reason: Option<String>,
        #[arg(long)]
        elevated: bool,
    },
    /// List queued operations
    List {
        #[arg(long)]
        all: bool,
    },
    /// Apply (run) a queued operation
    Apply { id: i64 },
    /// Mark a queued operation as done (manually completed)
    Done { id: i64 },
    /// Cancel a queued operation
    Cancel { id: i64 },
}

#[derive(Subcommand)]
enum PreflightAction {
    /// Run preflight checks
    Run,
    /// Show preflight status
    Status,
}

#[derive(Subcommand)]
enum CronAction {
    /// Check for cron drift (jobs that should run but haven't)
    Reconcile,
    /// Prune completed one-shot cron jobs
    Cleanup,
    /// Show cron health summary
    Health,
}

fn queue_add(conn: &Connection, command: &str, reason: Option<&str>, elevated: bool) -> Result<i64> {
    conn.execute(
        "INSERT INTO op_queue (command, reason, requires_elevated) VALUES (?1, ?2, ?3)",
        params![command, reason, elevated as i32],
    )?;
    let id = conn.last_insert_rowid();
    println!("Queued op [{}]: {}", id.to_string().bold(), &command[..command.len().min(60)]);
    if let Some(r) = reason { println!("  Reason: {}", r.dimmed()); }
    if elevated { println!("  {} Requires elevated access", "⚠".yellow()); }
    Ok(id)
}

fn queue_list(conn: &Connection, all: bool) -> Result<()> {
    let q = if all {
        "SELECT id, command, reason, requires_elevated, status, created_at FROM op_queue ORDER BY created_at DESC"
    } else {
        "SELECT id, command, reason, requires_elevated, status, created_at FROM op_queue WHERE status='pending' ORDER BY created_at"
    };
    let mut stmt = conn.prepare(q)?;
    let rows: Vec<_> = stmt.query_map([], |r| {
        Ok((r.get::<_,i64>(0)?, r.get::<_,String>(1)?, r.get::<_,Option<String>>(2)?,
            r.get::<_,i32>(3)?, r.get::<_,String>(4)?, r.get::<_,String>(5)?))
    })?.filter_map(|r| r.ok()).collect();

    if rows.is_empty() {
        println!("{}", "No queued operations.".dimmed());
        return Ok(());
    }
    for (id, cmd, reason, elevated, status, created_at) in rows {
        let icon = match status.as_str() {
            "applied" => "✓".green(),
            "failed"  => "✗".red(),
            "cancelled" => "–".dimmed(),
            _ => if elevated == 1 { "⚠".yellow() } else { "·".dimmed() },
        };
        let date = &created_at[..10];
        println!("{} [{:>3}] {}  {}",
            icon, id.to_string().dimmed(), &cmd[..cmd.len().min(70)], date.dimmed());
        if let Some(r) = reason { println!("         {}", r.dimmed()); }
    }
    Ok(())
}

fn queue_apply(conn: &Connection, id: i64) -> Result<()> {
    let (cmd, reason): (String, Option<String>) = conn.query_row(
        "SELECT command, reason FROM op_queue WHERE id=?1 AND status='pending'",
        params![id],
        |r| Ok((r.get(0)?, r.get(1)?)),
    ).map_err(|_| anyhow::anyhow!("Op [{}] not found or not pending", id))?;

    println!("Applying op [{}]: {}", id, cmd.bold());
    if let Some(r) = &reason { println!("Reason: {}", r.dimmed()); }

    let result = Command::new("sh").args(["-c", &cmd]).output();
    match result {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            let status = if out.status.success() { "applied" } else { "failed" };
            let result_text = format!("exit={} stdout={} stderr={}", out.status.code().unwrap_or(-1), stdout.trim(), stderr.trim());
            conn.execute(
                "UPDATE op_queue SET status=?2, applied_at=datetime('now'), result=?3 WHERE id=?1",
                params![id, status, &result_text],
            )?;
            if out.status.success() {
                println!("{} Op [{}] applied successfully.", "✓".green(), id);
                if !stdout.trim().is_empty() { println!("{}", stdout.trim()); }
            } else {
                println!("{} Op [{}] failed.", "✗".red(), id);
                println!("{}", stderr.trim().red());
            }
        }
        Err(e) => {
            conn.execute("UPDATE op_queue SET status='failed', result=?2 WHERE id=?1", params![id, e.to_string()])?;
            println!("{} Could not run op: {}", "✗".red(), e);
        }
    }
    Ok(())
}

fn preflight_run() -> Result<()> {
    let bin = dirs::home_dir().unwrap().join(".local/bin/clawd-preflight");
    if bin.exists() {
        let out = Command::new(&bin).arg("run").output()?;
        print!("{}", String::from_utf8_lossy(&out.stdout));
    } else {
        // Built-in minimal preflight
        println!("{}", "=== Preflight Checks ===".bold());
        let checks = [
            ("openclaw gateway", Command::new("systemctl").args(["--user", "is-active", "openclaw-gateway"]).output().map(|o| o.status.success()).unwrap_or(false)),
            ("clawd-context-watch", Command::new("systemctl").args(["--user", "is-active", "clawd-context-watch"]).output().map(|o| o.status.success()).unwrap_or(false)),
            ("clawd-buffer", Command::new("systemctl").args(["--user", "is-active", "clawd-buffer"]).output().map(|o| o.status.success()).unwrap_or(false)),
            ("tailscale", Command::new("tailscale").arg("status").output().map(|o| o.status.success()).unwrap_or(false)),
            ("stratum-mind", std::path::Path::new("$HOME/.local/bin/stratum-mind").exists()),
        ];
        for (name, ok) in checks {
            let icon = if ok { "✓".green() } else { "✗".red() };
            println!("  {} {}", icon, name);
        }
    }
    Ok(())
}

fn cron_cleanup() -> Result<()> {
    let out = Command::new("openclaw")
        .args(["cron", "list"])
        .output()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let mut pruned = 0;
    for line in text.lines() {
        // One-shot crons that already ran (delete-after-run typically removes them, but catch strays)
        if line.contains("once") && (line.contains("done") || line.contains("disabled")) {
            let id = line.split_whitespace().next().unwrap_or("");
            if !id.is_empty() {
                let _ = Command::new("openclaw").args(["cron", "remove", id]).output();
                pruned += 1;
            }
        }
    }
    println!("Cron cleanup: {} stale jobs pruned", pruned);
    Ok(())
}

fn status(conn: &Connection) -> Result<()> {
    println!("{}", "=== stratum-ops status ===".bold());
    let pending: i64 = conn.query_row("SELECT COUNT(*) FROM op_queue WHERE status='pending'", [], |r| r.get(0))?;
    let applied: i64 = conn.query_row("SELECT COUNT(*) FROM op_queue WHERE status='applied'", [], |r| r.get(0))?;
    if pending > 0 {
        println!("{} {} pending ops in queue — run `stratum-ops queue list`", "⚠".yellow(), pending);
    } else {
        println!("{} Op queue empty", "✓".green());
    }
    println!("  Applied: {}", applied);
    preflight_run()?;
    Ok(())
}

fn main() -> Result<()> {
    let conn = open_db()?;
    let cli = Cli::parse();
    match cli.command {
        Cmd::Queue { action } => match action {
            QueueAction::Add { command, reason, elevated } => {
                queue_add(&conn, &command, reason.as_deref(), elevated)?;
            }
            QueueAction::List { all } => queue_list(&conn, all)?,
            QueueAction::Apply { id } => queue_apply(&conn, id)?,
            QueueAction::Done { id } => {
                conn.execute("UPDATE op_queue SET status='applied', applied_at=datetime('now'), result='manually completed' WHERE id=?1", params![id])?;
                println!("Op [{}] marked done.", id);
            }
            QueueAction::Cancel { id } => {
                conn.execute("UPDATE op_queue SET status='cancelled' WHERE id=?1", params![id])?;
                println!("Op [{}] cancelled.", id);
            }
        },
        Cmd::Preflight { action } => match action {
            PreflightAction::Run => preflight_run()?,
            PreflightAction::Status => preflight_run()?,
        },
        Cmd::Cron { action } => match action {
            CronAction::Reconcile => {
                let bin = dirs::home_dir().unwrap().join(".local/bin/clawd-cron-reconcile");
                if bin.exists() {
                    let out = Command::new(&bin).arg("report").output()?;
                    print!("{}", String::from_utf8_lossy(&out.stdout));
                } else {
                    println!("clawd-cron-reconcile not found; run `stratum-brain heartbeat` instead.");
                }
            }
            CronAction::Cleanup => cron_cleanup()?,
            CronAction::Health => {
                let out = Command::new("stratum-brain").arg("status").output()?;
                let text = String::from_utf8_lossy(&out.stdout);
                for line in text.lines().filter(|l| l.contains("Cron") || l.contains("cron")) {
                    println!("{}", line);
                }
            }
        },
        Cmd::Status => status(&conn)?,
    }
    Ok(())
}
