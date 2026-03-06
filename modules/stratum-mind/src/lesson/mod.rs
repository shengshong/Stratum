// src/lesson/mod.rs — Lesson capture and retrieval (replaces clawd-lesson)

use anyhow::Result;
use colored::*;
use rusqlite::{params, Connection};

pub fn learn(
    conn: &Connection,
    content: &str,
    category: &str,
    severity: &str,
    source: Option<&str>,
) -> Result<i64> {
    conn.execute(
        "INSERT INTO lessons (content, category, severity, source) VALUES (?1, ?2, ?3, ?4)",
        params![content, category, severity, source],
    )?;
    Ok(conn.last_insert_rowid())
}

pub fn list(
    conn: &Connection,
    severity: Option<&str>,
    category: Option<&str>,
    resolved: bool,
    limit: usize,
) -> Result<()> {
    let mut conditions = vec!["1=1"];
    if !resolved {
        conditions.push("resolved=0");
    }
    let sev_filter;
    if let Some(s) = severity {
        sev_filter = format!("severity='{}'", s);
        conditions.push(&sev_filter);
    }
    let cat_filter;
    if let Some(c) = category {
        cat_filter = format!("category='{}'", c);
        conditions.push(&cat_filter);
    }

    let q = format!(
        "SELECT id, content, category, severity, resolved, created_at FROM lessons WHERE {} ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, created_at DESC LIMIT {}",
        conditions.join(" AND "), limit
    );

    let mut stmt = conn.prepare(&q)?;
    let rows: Vec<_> = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, i32>(4)?,
                row.get::<_, String>(5)?,
            ))
        })?
        .filter_map(|r| r.ok())
        .collect();

    if rows.is_empty() {
        println!("{}", "No lessons found.".dimmed());
        return Ok(());
    }

    for (id, content, category, severity, resolved, created_at) in rows {
        let sev_color = match severity.as_str() {
            "critical" => severity.red().bold(),
            "high" => severity.yellow().bold(),
            "medium" => severity.cyan(),
            _ => severity.dimmed(),
        };
        let status = if resolved == 1 {
            "✓".green()
        } else {
            "○".dimmed()
        };
        let date = &created_at[..10];
        println!(
            "{} [{:>4}] {} {}  {}",
            status,
            id.to_string().dimmed(),
            sev_color,
            category.dimmed(),
            date.dimmed()
        );
        // Wrap content at 80 chars
        for line in textwrap(content.as_str(), 72) {
            println!("         {}", line);
        }
    }
    Ok(())
}

pub fn resolve(conn: &Connection, id: i64, note: Option<&str>) -> Result<bool> {
    let n = conn.execute(
        "UPDATE lessons SET resolved=1, resolve_note=?2, updated_at=datetime('now') WHERE id=?1",
        params![id, note],
    )?;
    Ok(n > 0)
}

pub fn stats(conn: &Connection) -> Result<()> {
    let total: i64 = conn.query_row("SELECT COUNT(*) FROM lessons", [], |r| r.get(0))?;
    let resolved: i64 =
        conn.query_row("SELECT COUNT(*) FROM lessons WHERE resolved=1", [], |r| {
            r.get(0)
        })?;
    let rate = if total > 0 { resolved * 100 / total } else { 0 };
    println!(
        "Lessons: {} total, {} resolved ({}%)",
        total, resolved, rate
    );
    Ok(())
}

fn textwrap(s: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut current = String::new();
    for word in s.split_whitespace() {
        if current.len() + word.len() + 1 > width && !current.is_empty() {
            lines.push(current.clone());
            current.clear();
        }
        if !current.is_empty() {
            current.push(' ');
        }
        current.push_str(word);
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}
