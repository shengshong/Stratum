// src/world/mod.rs — Knowledge graph: entities, relations, beliefs (replaces clawd-world)
// v2: adds traverse (BFS on preloaded graph), search (FTS5+LIKE hybrid), consolidate (decay+FTS5)

use anyhow::Result;
use colored::*;
use rusqlite::{Connection, params};
use std::collections::{VecDeque, HashSet};

pub fn add_entity(conn: &Connection, name: &str, entity_type: &str, description: Option<&str>) -> Result<()> {
    conn.execute(
        "INSERT OR IGNORE INTO entities (name, entity_type, description) VALUES (?1, ?2, ?3)",
        params![name, entity_type, description],
    )?;
    println!("Entity '{}' ({}) added.", name.bold(), entity_type);
    Ok(())
}

pub fn add_relation(conn: &Connection, subject: &str, predicate: &str, object: &str) -> Result<()> {
    conn.execute(
        "INSERT OR REPLACE INTO relations (subject, predicate, object) VALUES (?1, ?2, ?3)",
        params![subject, predicate, object],
    )?;
    println!("Relation: {} {} {}", subject.bold(), predicate.dimmed(), object.bold());
    Ok(())
}

pub fn add_belief(conn: &Connection, entity: &str, attribute: &str, value: &str, confidence: f64, evidence: Option<&str>) -> Result<()> {
    conn.execute(
        "INSERT OR REPLACE INTO beliefs (entity, attribute, value, confidence, evidence, last_verified, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, datetime('now'), datetime('now'))",
        params![entity, attribute, value, confidence, evidence],
    )?;
    println!("Belief: {}[{}] = {} (conf: {:.1})", entity.bold(), attribute.cyan(), value.green(), confidence);
    Ok(())
}

pub fn query(conn: &Connection, term: &str) -> Result<()> {
    let pattern = format!("%{}%", term);

    let mut stmt_e = conn.prepare("SELECT name, entity_type, description FROM entities WHERE name LIKE ?1 OR description LIKE ?1")?;
    let entities: Vec<_> = stmt_e.query_map(params![&pattern], |r| {
        Ok((r.get::<_,String>(0)?, r.get::<_,String>(1)?, r.get::<_,Option<String>>(2)?))
    })?.filter_map(|r| r.ok()).collect();

    let mut stmt_b = conn.prepare("SELECT entity, attribute, value, confidence, updated_at FROM beliefs WHERE entity LIKE ?1 OR value LIKE ?1")?;
    let beliefs: Vec<_> = stmt_b.query_map(params![&pattern], |r| {
        Ok((r.get::<_,String>(0)?, r.get::<_,String>(1)?, r.get::<_,String>(2)?, r.get::<_,f64>(3)?, r.get::<_,String>(4)?))
    })?.filter_map(|r| r.ok()).collect();

    let mut stmt_r = conn.prepare("SELECT subject, predicate, object FROM relations WHERE subject LIKE ?1 OR object LIKE ?1")?;
    let relations: Vec<_> = stmt_r.query_map(params![&pattern], |r| {
        Ok((r.get::<_,String>(0)?, r.get::<_,String>(1)?, r.get::<_,String>(2)?))
    })?.filter_map(|r| r.ok()).collect();

    if entities.is_empty() && beliefs.is_empty() && relations.is_empty() {
        println!("{}", format!("No results for '{}'.", term).dimmed());
        return Ok(());
    }
    if !entities.is_empty() {
        println!("{}", "Entities:".bold().underline());
        for (name, etype, desc) in &entities {
            println!("  {} ({}){}", name.bold(), etype.dimmed(),
                desc.as_deref().map(|d| format!(" — {}", d)).unwrap_or_default());
        }
    }
    if !beliefs.is_empty() {
        println!("{}", "Beliefs:".bold().underline());
        for (entity, attr, val, conf, updated) in &beliefs {
            println!("  {}[{}] = {}  conf:{:.1}  {}", entity.bold(), attr.cyan(), val.green(), conf,
                updated.get(..10).unwrap_or("").dimmed());
        }
    }
    if !relations.is_empty() {
        println!("{}", "Relations:".bold().underline());
        for (subj, pred, obj) in &relations {
            println!("  {} {} {}", subj.bold(), pred.dimmed(), obj.bold());
        }
    }
    Ok(())
}

/// BFS graph traversal — loads all relations first, then walks in Rust (avoids lifetime issues).
pub fn traverse(conn: &Connection, start: &str, max_hops: u32) -> Result<()> {
    println!("{}", format!("Graph traversal from '{}' (max {} hops):", start, max_hops).bold());

    // Load ALL relations into memory once — small table, no repeated DB queries needed
    let mut stmt_all = conn.prepare("SELECT subject, predicate, object FROM relations")?;
    let all_rels: Vec<(String, String, String)> = stmt_all.query_map([], |r| {
        Ok((r.get::<_,String>(0)?, r.get::<_,String>(1)?, r.get::<_,String>(2)?))
    })?.filter_map(|r| r.ok()).collect();

    let mut visited: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<(String, u32)> = VecDeque::new();
    let mut edges_seen: Vec<(String, String, String, u32)> = Vec::new();

    let start_lower = start.to_lowercase();
    queue.push_back((start.to_string(), 0));
    visited.insert(start_lower.clone());

    while let Some((node, depth)) = queue.pop_front() {
        if depth >= max_hops { continue; }
        let node_lower = node.to_lowercase();

        for (subj, pred, obj) in &all_rels {
            let subj_lower = subj.to_lowercase();
            let obj_lower = obj.to_lowercase();
            let matches_subj = subj_lower.contains(&node_lower);
            let matches_obj = obj_lower.contains(&node_lower);

            if !matches_subj && !matches_obj { continue; }

            let neighbor = if matches_subj { obj.clone() } else { subj.clone() };
            let neighbor_lower = neighbor.to_lowercase();

            if !visited.contains(&neighbor_lower) {
                visited.insert(neighbor_lower);
                queue.push_back((neighbor.clone(), depth + 1));
            }
            let edge_key = format!("{}\x00{}\x00{}", subj, pred, obj);
            if !edges_seen.iter().any(|(s,p,o,_)| format!("{}\x00{}\x00{}", s,p,o) == edge_key) {
                edges_seen.push((subj.clone(), pred.clone(), obj.clone(), depth + 1));
            }
        }
    }

    if edges_seen.is_empty() {
        println!("  {}", "No connected entities found.".dimmed());
        return Ok(());
    }

    println!("{}", "Connections:".underline());
    for (s, p, o, hop) in &edges_seen {
        println!("  [hop {}]  {} {} {}", hop.to_string().dimmed(), s.bold(), p.dimmed(), o.bold());
    }

    // Show beliefs for all visited nodes
    println!("\n{}", "Beliefs in subgraph:".underline());
    let mut found_beliefs = false;
    for node in &visited {
        let pattern = format!("%{}%", node);
        let mut stmt_bel = conn.prepare(
            "SELECT entity, attribute, value, confidence FROM beliefs
             WHERE lower(entity) LIKE lower(?1) AND stale=0 LIMIT 5"
        )?;
        let bels: Vec<(String,String,String,f64)> = stmt_bel.query_map(params![&pattern], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
        })?.filter_map(|r| r.ok()).collect();
        for (e, a, v, c) in bels {
            println!("  {}[{}] = {}  conf:{:.1}", e.bold(), a.cyan(), v.green(), c);
            found_beliefs = true;
        }
    }
    if !found_beliefs { println!("  {}", "(none)".dimmed()); }

    println!("\n{} node(s) reachable from '{}' (excl. start).", visited.len().saturating_sub(1), start);
    Ok(())
}

/// Hybrid FTS5 + LIKE keyword search across beliefs and lessons.
pub fn search(conn: &Connection, query_str: &str) -> Result<()> {
    println!("{}", format!("Search: '{}'", query_str).bold());

    // FTS5 on beliefs (may fail if table not populated)
    let mut fts_beliefs: Vec<(String,String,String,f64)> = Vec::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT b.entity, b.attribute, b.value, b.confidence
         FROM beliefs_fts f JOIN beliefs b ON b.id = f.rowid
         WHERE beliefs_fts MATCH ?1 ORDER BY rank LIMIT 20"
    ) {
        fts_beliefs = stmt.query_map(params![query_str], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
        }).map(|rows| rows.filter_map(|r| r.ok()).collect()).unwrap_or_default();
    }

    // FTS5 on lessons
    let mut fts_lessons: Vec<(i64,String,String,String)> = Vec::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT l.id, l.content, l.category, l.severity
         FROM lessons_fts f JOIN lessons l ON l.id = f.rowid
         WHERE lessons_fts MATCH ?1 AND l.resolved=0 ORDER BY rank LIMIT 10"
    ) {
        fts_lessons = stmt.query_map(params![query_str], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
        }).map(|rows| rows.filter_map(|r| r.ok()).collect()).unwrap_or_default();
    }

    // Fallback LIKE search on beliefs if FTS returned nothing
    if fts_beliefs.is_empty() {
        let pattern = format!("%{}%", query_str);
        let mut stmt_like = conn.prepare(
            "SELECT entity, attribute, value, confidence FROM beliefs
             WHERE (entity LIKE ?1 OR attribute LIKE ?1 OR value LIKE ?1) AND stale=0 LIMIT 15"
        )?;
        fts_beliefs = stmt_like.query_map(params![&pattern], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
        })?.filter_map(|r| r.ok()).collect();
    }

    if fts_beliefs.is_empty() && fts_lessons.is_empty() {
        println!("{}", "No results found.".dimmed());
        return Ok(());
    }
    if !fts_beliefs.is_empty() {
        println!("{}", "Beliefs:".bold().underline());
        for (e, a, v, c) in &fts_beliefs {
            println!("  {}[{}] = {}  conf:{:.1}", e.bold(), a.cyan(), v.green(), c);
        }
    }
    if !fts_lessons.is_empty() {
        println!("{}", "Lessons:".bold().underline());
        for (id, content, cat, sev) in &fts_lessons {
            let short = if content.len() > 80 { &content[..80] } else { content };
            println!("  [{}] ({}/{}) {}", id.to_string().dimmed(), sev, cat, short);
        }
    }
    Ok(())
}

/// Nightly consolidation: decay unverified beliefs, rebuild FTS5, log run.
pub fn consolidate(conn: &Connection, decay_days: i64, stale_threshold: f64, dry_run: bool) -> Result<(usize, usize, usize)> {
    println!("{}", "=== Knowledge Consolidation ===".bold());

    // 1. Find beliefs unverified for longer than decay_days
    let sql_aging = format!(
        "SELECT id, entity, attribute, confidence, decay_rate, coalesce(last_verified, updated_at)
         FROM beliefs WHERE date(coalesce(last_verified, updated_at)) < date('now', '-{} days') AND stale=0",
        decay_days
    );
    let mut stmt_aging = conn.prepare(&sql_aging)?;
    let aging: Vec<(i64, String, String, f64, f64, String)> = stmt_aging.query_map([], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?))
    })?.filter_map(|r| r.ok()).collect();

    println!("Aging beliefs (unverified >{} days): {}", decay_days, aging.len().to_string().yellow());

    let mut decayed = 0usize;
    let mut stale_marked = 0usize;

    for (id, entity, attr, conf, decay_rate, last_verified) in &aging {
        let new_conf = (conf * (1.0 - decay_rate)).max(0.01);
        let is_stale = new_conf < stale_threshold;
        if !dry_run {
            conn.execute(
                "UPDATE beliefs SET confidence=?1, stale=?2, updated_at=datetime('now') WHERE id=?3",
                params![new_conf, is_stale as i32, id],
            )?;
        }
        decayed += 1;
        if is_stale {
            stale_marked += 1;
            println!("  {} STALE: {}[{}]  {:.2}→{:.2}  (last verified: {})",
                "⚠".yellow(), entity.bold(), attr.cyan(), conf, new_conf,
                last_verified.get(..10).unwrap_or("?"));
        } else {
            println!("  {} {}[{}]  {:.2}→{:.2}",
                "↓".dimmed(), entity, attr, conf, new_conf);
        }
    }

    // 2. Report stale beliefs needing verification
    let mut stmt_stale = conn.prepare(
        "SELECT entity, attribute, value FROM beliefs WHERE stale=1 ORDER BY entity, attribute LIMIT 30"
    )?;
    let stale_list: Vec<(String,String,String)> = stmt_stale.query_map([], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?))
    })?.filter_map(|r| r.ok()).collect();

    let conflict_count = stale_list.len();
    if !stale_list.is_empty() {
        println!("\n{}", "Stale beliefs needing re-verification:".bold().yellow());
        for (e, a, v) in &stale_list {
            println!("  {}[{}] = {}", e.bold(), a.cyan(), v.dimmed());
        }
    }

    // 3. Rebuild FTS5 indices
    println!("\n{}", "Rebuilding FTS5 indices...".dimmed());
    if !dry_run {
        // Try 'rebuild' command first; fall back to delete+reinsert
        let rebuild_ok = conn.execute_batch(
            "INSERT INTO beliefs_fts(beliefs_fts) VALUES('rebuild');
             INSERT INTO lessons_fts(lessons_fts) VALUES('rebuild');"
        ).is_ok();
        if !rebuild_ok {
            let _ = conn.execute_batch(
                "DELETE FROM beliefs_fts;
                 INSERT INTO beliefs_fts(rowid, entity, attribute, value)
                   SELECT id, entity, attribute, value FROM beliefs;
                 DELETE FROM lessons_fts;
                 INSERT INTO lessons_fts(rowid, content, category)
                   SELECT id, content, category FROM lessons;"
            );
        }
    }
    println!("  FTS5 {}.", if dry_run { "would be rebuilt" } else { "rebuilt" });

    // 4. Log
    if !dry_run {
        conn.execute(
            "INSERT INTO consolidation_log (decayed, stale_marked, conflicts, summary) VALUES (?1,?2,?3,?4)",
            params![decayed as i64, stale_marked as i64, conflict_count as i64,
                format!("decay_days={} threshold={:.2} aging={}", decay_days, stale_threshold, aging.len())],
        )?;
    }

    // 5. Summary
    println!("\n{}", "=== Summary ===".bold());
    println!("  Aging processed:  {}", aging.len().to_string().yellow());
    println!("  Decayed:          {}", decayed.to_string().cyan());
    println!("  Marked stale:     {}", if stale_marked > 0 { stale_marked.to_string().red().to_string() } else { "0".to_string() });
    println!("  Stale (total):    {}", conflict_count.to_string().yellow());
    println!("  FTS5 rebuilt:     beliefs + lessons");
    if dry_run { println!("  {}", "(DRY RUN — no changes written)".yellow().bold()); }

    Ok((decayed, stale_marked, conflict_count))
}

/// Show consolidation run history.
pub fn consolidation_log(conn: &Connection) -> Result<()> {
    let mut stmt = conn.prepare(
        "SELECT ran_at, decayed, stale_marked, conflicts, summary
         FROM consolidation_log ORDER BY ran_at DESC LIMIT 10"
    )?;
    let rows: Vec<(String,i64,i64,i64,String)> = stmt.query_map([], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
    })?.filter_map(|r| r.ok()).collect();

    if rows.is_empty() {
        println!("{}", "No consolidation runs recorded yet.".dimmed());
        return Ok(());
    }
    println!("{}", "Consolidation History (last 10):".bold().underline());
    for (ran_at, decayed, stale, conflicts, summary) in rows {
        println!("  {}  decayed:{} stale:{} needing-verify:{}  ({})",
            ran_at.get(..16).unwrap_or(&ran_at).dimmed(),
            decayed, stale, conflicts, summary.dimmed());
    }
    Ok(())
}

pub fn status(conn: &Connection) -> Result<()> {
    let entities: i64 = conn.query_row("SELECT COUNT(*) FROM entities", [], |r| r.get(0))?;
    let relations: i64 = conn.query_row("SELECT COUNT(*) FROM relations", [], |r| r.get(0))?;
    let beliefs: i64 = conn.query_row("SELECT COUNT(*) FROM beliefs", [], |r| r.get(0))?;
    let stale: i64 = conn.query_row("SELECT COUNT(*) FROM beliefs WHERE stale=1", [], |r| r.get(0))
        .unwrap_or(0);
    if stale > 0 {
        println!("World model: {} entities, {} relations, {} beliefs ({} {})",
            entities, relations, beliefs, stale.to_string().yellow(), "stale".yellow());
    } else {
        println!("World model: {} entities, {} relations, {} beliefs", entities, relations, beliefs);
    }
    Ok(())
}
