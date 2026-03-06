// stratum-agent-monitor — Stratum coding agent session monitor
//
// Watches a Zellij or tmux session running Claude Code.
// Sends "1" ONLY when an explicit "Do you want to proceed?" confirmation prompt is visible.
// After a nudge, watches every 20s for 2 minutes for rapid follow-up prompts.
// Notifies via `openclaw message` when completion pattern is detected, then exits.
//
// Usage:
//   stratum-agent-monitor watch --session <zellij-session> \
//       --complete-pattern "v0\.16\.0" \
//       --complete-also "push|pushed|github" \
//       --notify-channel telegram --notify-to {{TELEGRAM_CHAT_ID}} \
//       --notify-msg "VeridianOS Phase 7.5 Wave 8 complete and pushed!"
//   stratum-agent-monitor check --session <session>   # single check, exit 0=nudged 1=active 2=idle
//   stratum-agent-monitor status                       # show log tail

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::process::Command;
use std::thread;
use std::time::Duration;

const LOG_PATH: &str = "/tmp/stratum-agent-monitor.log";
const DUMP_PATH: &str = "/tmp/stratum-agent-monitor-screen.txt";
const CONFIRM_PROMPT: &str = "Do you want to proceed?";

#[derive(Parser)]
#[command(
    name = "stratum-agent-monitor",
    about = "Coding agent session monitor",
    version = "0.1.0"
)]
struct Cli {
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Monitor session, nudge at confirmation prompts, notify on completion
    Watch {
        /// Zellij session name
        #[arg(long)]
        session: String,
        /// Regex pattern that must appear for completion (e.g. "v0\\.16\\.0")
        #[arg(long, default_value = "")]
        complete_pattern: String,
        /// Second regex that must also appear for completion (e.g. "push|pushed")
        #[arg(long, default_value = "push|pushed|github|released")]
        complete_also: String,
        /// Telegram channel for completion notification
        #[arg(long, default_value = "telegram")]
        notify_channel: String,
        /// Telegram user ID to notify
        #[arg(long, default_value = "{{TELEGRAM_CHAT_ID}}")]
        notify_to: String,
        /// Message to send on completion
        #[arg(long, default_value = "Coding agent session complete.")]
        notify_msg: String,
    },
    /// Single check — nudge if needed, then exit
    Check {
        #[arg(long)]
        session: String,
    },
    /// Show monitor log
    Status,
}

fn log(msg: &str) {
    let ts = chrono::Local::now().format("%H:%M:%S");
    let line = format!("[{}] {}\n", ts, msg);
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(LOG_PATH)
    {
        let _ = f.write_all(line.as_bytes());
    }
}

fn dump_screen(session: &str) -> Result<String> {
    Command::new("zellij")
        .args(["--session", session, "action", "dump-screen", DUMP_PATH])
        .output()
        .context("zellij dump-screen failed")?;
    std::fs::read_to_string(DUMP_PATH).context("read dump failed")
}

fn needs_nudge(screen: &str) -> bool {
    screen.contains(CONFIRM_PROMPT)
}

fn is_complete(screen: &str, pattern: &str, also: &str) -> bool {
    if pattern.is_empty() {
        return false;
    }
    // Simple substring check (patterns are short, no need for full regex overhead)
    let has_pattern = screen.contains(pattern) ||
        // Handle common escaped patterns like v0\.16\.0 → v0.16.0
        screen.contains(&pattern.replace("\\.", "."));
    let has_also = also
        .split('|')
        .any(|p| screen.to_lowercase().contains(&p.to_lowercase()));
    has_pattern && has_also
}

fn send_nudge(session: &str) {
    let _ = Command::new("zellij")
        .args(["--session", session, "action", "write-chars", "1"])
        .output();
    thread::sleep(Duration::from_millis(300));
    let _ = Command::new("zellij")
        .args(["--session", session, "action", "write-chars", "\n"])
        .output();
    log("Nudge sent (1 + Enter)");
}

fn notify(channel: &str, to: &str, msg: &str) {
    let _ = Command::new("openclaw")
        .args([
            "message",
            "send",
            "--channel",
            channel,
            "--to",
            to,
            "--message",
            msg,
        ])
        .output();
    log(&format!("Notification sent: {}", msg));
}

fn run_check(session: &str) -> Result<u8> {
    let screen = dump_screen(session)?;
    if needs_nudge(&screen) {
        log(&format!("⏸ '{}' detected — sending nudge", CONFIRM_PROMPT));
        send_nudge(session);
        // Follow-up watch: 6 × 20s = 2 minutes
        for i in 1..=6 {
            thread::sleep(Duration::from_secs(20));
            let s2 = dump_screen(session).unwrap_or_default();
            if needs_nudge(&s2) {
                log(&format!("⏸ Follow-up nudge #{}", i));
                send_nudge(session);
            } else {
                log(&format!("Follow-up #{} — no prompt", i));
            }
        }
        return Ok(0); // nudged
    }
    log("No confirmation prompt — no nudge");
    Ok(2) // idle/active
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Cmd::Watch {
            session,
            complete_pattern,
            complete_also,
            notify_channel,
            notify_to,
            notify_msg,
        } => {
            log(&format!("Monitor started for session '{}'", session));
            // Single check + completion test (called by cron every 5m)
            let screen = dump_screen(&session)?;
            if is_complete(&screen, &complete_pattern, &complete_also) {
                log("🎉 Completion detected");
                notify(&notify_channel, &notify_to, &notify_msg);
                std::process::exit(42); // signal completion to caller
            }
            run_check(&session)?;
        }
        Cmd::Check { session } => {
            let code = run_check(&session)?;
            std::process::exit(code as i32);
        }
        Cmd::Status => {
            let log = std::fs::read_to_string(LOG_PATH).unwrap_or_else(|_| "(no log yet)".into());
            let lines: Vec<&str> = log.lines().collect();
            let tail = lines.iter().rev().take(20).collect::<Vec<_>>();
            for line in tail.iter().rev() {
                println!("{}", line);
            }
        }
    }
    Ok(())
}
