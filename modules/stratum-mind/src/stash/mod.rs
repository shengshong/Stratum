// src/stash/mod.rs — Persistent scratch-pad (replaces clawd-stash)

use anyhow::Result;
use colored::*;
use rusqlite::{Connection, params};

pub fn add(conn: &Connection, content: &str, priority: &str, tags: Option<&str>) -> Result<i64> {
    let tags_json = tags.map(|t| {
        let v: Vec<&str> = t.split(',').map(str::trim).collect();
        serde_json::to_string(&v).unwrap_or_default()
    });
    conn.execute(
        "INSERT INTO stash (content, priority, tags) VALUES (?1, ?2, ?3)",
        params![content, priority, tags_json],
    )?;
    Ok(conn.last_insert_rowid())
}

pub fn list(conn: &Connection, show_done: bool, priority_filter: Option<&str>) -> Result<()> {
    let query = if show_done {
        "SELECT id, content, priority, tags, done, created_at FROM stash ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, created_at DESC"
    } else {
        "SELECT id, content, priority, tags, done, created_at FROM stash WHERE done=0 ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, created_at DESC"
    };

    let mut stmt = conn.prepare(query)?;
    let rows: Vec<_> = stmt.query_map([], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<String>>(3)?,
            row.get::<_, i32>(4)?,
            row.get::<_, String>(5)?,
        ))
    })?.filter_map(|r| r.ok()).collect();

    if rows.is_empty() {
        println!("{}", "No stash items.".dimmed());
        return Ok(());
    }

    for (id, content, priority, tags, done, created_at) in rows {
        if let Some(pf) = priority_filter {
            if priority != pf { continue; }
        }
        let prefix = if done == 1 { "✓".green() } else {
            match priority.as_str() {
                "urgent" => "!".red().bold(),
                "high"   => "↑".yellow(),
                _        => "·".dimmed(),
            }
        };
        let date = &created_at[..10];
        let tag_str = tags.map(|t| {
            let v: Vec<String> = serde_json::from_str(&t).unwrap_or_default();
            if v.is_empty() { String::new() } else { format!(" [{}]", v.join(", ")) }
        }).unwrap_or_default();
        println!("{} {:>4}  {}{}  {}", prefix, id.to_string().dimmed(), content, tag_str.dimmed(), date.dimmed());
    }
    Ok(())
}

pub fn done(conn: &Connection, id: i64) -> Result<bool> {
    let n = conn.execute(
        "UPDATE stash SET done=1, updated_at=datetime('now') WHERE id=?1",
        params![id],
    )?;
    Ok(n > 0)
}

pub fn remove(conn: &Connection, id: i64) -> Result<bool> {
    let n = conn.execute("DELETE FROM stash WHERE id=?1", params![id])?;
    Ok(n > 0)
}
