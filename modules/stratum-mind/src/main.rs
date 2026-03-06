// stratum-mind — Stratum unified knowledge store
// Replaces: clawd-stash, clawd-lesson, clawd-world, clawd-goals, clawd-memory
// Database: ~/.local/share/stratum/mind.db (single SQLite, WAL mode)
//
// Usage:
//   stratum-mind stash "note" [--priority urgent|high|normal|low] [--tags tag1,tag2]
//   stratum-mind stash list [--all] [--priority <p>]
//   stratum-mind stash done <id>
//   stratum-mind lesson learn "content" [--category <c>] [--severity <s>] [--source <s>]
//   stratum-mind lesson list [--severity <s>] [--category <c>] [--resolved] [--limit N]
//   stratum-mind lesson resolve <id> [--note "why"]
//   stratum-mind lesson stats
//   stratum-mind world add entity <name> [--type <t>] [--desc "..."]
//   stratum-mind world add relation <subject> <predicate> <object>
//   stratum-mind world add belief <entity> <attribute> <value> [--confidence 0.9] [--evidence "..."]
//   stratum-mind world query <term>
//   stratum-mind world status
//   stratum-mind goals add "title" [--desc "..."] [--parent <id>] [--priority <p>]
//   stratum-mind goals list [--tree] [--status active|complete|blocked]
//   stratum-mind goals eval <id> "note"
//   stratum-mind goals complete <id> ["note"]
//   stratum-mind goals status
//   stratum-mind memory status
//   stratum-mind memory weekly    — rebalance check: budget, tier scan, demotion suggestions
//   stratum-mind memory track <key> <tier>
//   stratum-mind memory access <key>
//   stratum-mind status  (all-in-one dashboard)

use anyhow::Result;
use clap::{Parser, Subcommand};
use colored::Colorize;
#[allow(unused_imports)]
use rusqlite::params;

mod db;
mod goals;
mod lesson;
mod memory;
mod stash;
mod world;

#[derive(Parser)]
#[command(
    name = "stratum-mind",
    about = "Stratum unified knowledge store",
    version = "0.1.0"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Persistent scratch-pad
    Stash {
        #[command(subcommand)]
        action: StashAction,
    },
    /// Lesson capture and retrieval
    Lesson {
        #[command(subcommand)]
        action: LessonAction,
    },
    /// Knowledge graph (entities, relations, beliefs)
    World {
        #[command(subcommand)]
        action: WorldAction,
    },
    /// Persistent goal tree
    Goals {
        #[command(subcommand)]
        action: GoalsAction,
    },
    /// Memory tier governance
    Memory {
        #[command(subcommand)]
        action: MemoryAction,
    },
    /// Full dashboard status
    Status,
}

#[derive(Subcommand)]
enum StashAction {
    /// Add a note to the stash
    Add {
        content: String,
        #[arg(long, default_value = "normal")]
        priority: String,
        #[arg(long)]
        tags: Option<String>,
    },
    /// List stash items
    List {
        #[arg(long)]
        all: bool,
        #[arg(long)]
        priority: Option<String>,
    },
    /// Mark a stash item done
    Done { id: i64 },
    /// Remove a stash item
    Remove { id: i64 },
}

#[derive(Subcommand)]
enum LessonAction {
    /// Record a new lesson
    Learn {
        content: String,
        #[arg(long, default_value = "correction")]
        category: String,
        #[arg(long, default_value = "medium")]
        severity: String,
        #[arg(long)]
        source: Option<String>,
    },
    /// List lessons
    List {
        #[arg(long)]
        severity: Option<String>,
        #[arg(long)]
        category: Option<String>,
        #[arg(long)]
        resolved: bool,
        #[arg(long, default_value = "20")]
        limit: usize,
    },
    /// Mark a lesson resolved
    Resolve {
        id: i64,
        #[arg(long)]
        note: Option<String>,
    },
    /// Show lesson statistics
    Stats,
}

#[derive(Subcommand)]
enum WorldAction {
    /// Add an entity, relation, or belief
    Add {
        #[command(subcommand)]
        kind: WorldAddKind,
    },
    /// Query the world model (LIKE search)
    Query { term: String },
    /// BFS graph traversal from an entity
    Traverse {
        entity: String,
        #[arg(long, default_value_t = 2)]
        hops: u32,
    },
    /// Hybrid FTS5 + LIKE keyword search across beliefs and lessons
    Search { query: String },
    /// Nightly consolidation: decay unverified beliefs, detect conflicts, rebuild FTS5
    Consolidate {
        /// Days before a belief starts decaying (default: 30)
        #[arg(long, default_value_t = 30)]
        decay_days: i64,
        /// Confidence below which a belief is marked stale (default: 0.3)
        #[arg(long, default_value_t = 0.3)]
        stale_threshold: f64,
        /// Preview changes without writing to DB
        #[arg(long)]
        dry_run: bool,
    },
    /// Show consolidation run history
    ConsolidateLog,
    /// Mark a belief as verified (resets decay clock, confidence back to 1.0)
    Verify {
        entity: String,
        attribute: String,
        #[arg(long, default_value_t = 1.0)]
        confidence: f64,
    },
    /// Show world model statistics
    Status,
}

#[derive(Subcommand)]
enum WorldAddKind {
    Entity {
        name: String,
        #[arg(long, default_value = "concept")]
        r#type: String,
        #[arg(long)]
        desc: Option<String>,
    },
    Relation {
        subject: String,
        predicate: String,
        object: String,
    },
    Belief {
        entity: String,
        attribute: String,
        value: String,
        #[arg(long, default_value_t = 1.0)]
        confidence: f64,
        #[arg(long)]
        evidence: Option<String>,
    },
}

#[derive(Subcommand)]
enum GoalsAction {
    /// Add a goal
    Add {
        title: String,
        #[arg(long)]
        desc: Option<String>,
        #[arg(long)]
        parent: Option<i64>,
        #[arg(long, default_value = "medium")]
        priority: String,
    },
    /// List goals
    List {
        #[arg(long)]
        tree: bool,
        #[arg(long)]
        status: Option<String>,
    },
    /// Add evaluation note to a goal
    Eval { id: i64, note: String },
    /// Mark a goal complete
    Complete { id: i64, note: Option<String> },
    /// Goal statistics
    Status,
}

#[derive(Subcommand)]
enum MemoryAction {
    /// Show memory tier status and hot-tier word count
    Status,
    /// Weekly rebalance: check budget, scan all tiers, suggest demotions
    Weekly,
    /// Track a file key in a specific tier
    Track { key: String, tier: String },
    /// Record a file access (increments access_count for promotion tracking)
    Access { key: String },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let conn = db::open()?;

    match cli.command {
        Command::Stash { action } => match action {
            StashAction::Add {
                content,
                priority,
                tags,
            } => {
                let id = stash::add(&conn, &content, &priority, tags.as_deref())?;
                println!("Stash [{}] added.", id);
            }
            StashAction::List { all, priority } => {
                stash::list(&conn, all, priority.as_deref())?;
            }
            StashAction::Done { id } => {
                if stash::done(&conn, id)? {
                    println!("Stash [{}] marked done.", id);
                } else {
                    println!("Not found: {}", id);
                }
            }
            StashAction::Remove { id } => {
                if stash::remove(&conn, id)? {
                    println!("Stash [{}] removed.", id);
                } else {
                    println!("Not found: {}", id);
                }
            }
        },

        Command::Lesson { action } => match action {
            LessonAction::Learn {
                content,
                category,
                severity,
                source,
            } => {
                let id = lesson::learn(&conn, &content, &category, &severity, source.as_deref())?;
                println!(
                    "✓ Lesson [{}] recorded ({}/{}) {}",
                    id,
                    severity,
                    category,
                    &content[..content.len().min(60)]
                );
            }
            LessonAction::List {
                severity,
                category,
                resolved,
                limit,
            } => {
                lesson::list(
                    &conn,
                    severity.as_deref(),
                    category.as_deref(),
                    resolved,
                    limit,
                )?;
            }
            LessonAction::Resolve { id, note } => {
                if lesson::resolve(&conn, id, note.as_deref())? {
                    println!("✓ Lesson [{}] marked as resolved.", id);
                } else {
                    println!("Not found: {}", id);
                }
            }
            LessonAction::Stats => lesson::stats(&conn)?,
        },

        Command::World { action } => match action {
            WorldAction::Add { kind } => match kind {
                WorldAddKind::Entity { name, r#type, desc } => {
                    world::add_entity(&conn, &name, &r#type, desc.as_deref())?;
                }
                WorldAddKind::Relation {
                    subject,
                    predicate,
                    object,
                } => {
                    world::add_relation(&conn, &subject, &predicate, &object)?;
                }
                WorldAddKind::Belief {
                    entity,
                    attribute,
                    value,
                    confidence,
                    evidence,
                } => {
                    world::add_belief(
                        &conn,
                        &entity,
                        &attribute,
                        &value,
                        confidence,
                        evidence.as_deref(),
                    )?;
                }
            },
            WorldAction::Query { term } => world::query(&conn, &term)?,
            WorldAction::Traverse { entity, hops } => world::traverse(&conn, &entity, hops)?,
            WorldAction::Search { query } => world::search(&conn, &query)?,
            WorldAction::Consolidate {
                decay_days,
                stale_threshold,
                dry_run,
            } => {
                world::consolidate(&conn, decay_days, stale_threshold, dry_run)?;
            }
            WorldAction::ConsolidateLog => world::consolidation_log(&conn)?,
            WorldAction::Verify {
                entity,
                attribute,
                confidence,
            } => {
                conn.execute(
                    "UPDATE beliefs SET confidence=?1, last_verified=datetime('now'), stale=0, updated_at=datetime('now') WHERE entity=?2 AND attribute=?3",
                    params![confidence, entity, attribute],
                )?;
                println!(
                    "✓ Belief {}[{}] verified (conf={:.1}).",
                    entity.bold(),
                    attribute.cyan(),
                    confidence
                );
            }
            WorldAction::Status => world::status(&conn)?,
        },

        Command::Goals { action } => match action {
            GoalsAction::Add {
                title,
                desc,
                parent,
                priority,
            } => {
                goals::add(&conn, &title, desc.as_deref(), parent, &priority)?;
            }
            GoalsAction::List { tree, status } => {
                goals::list(&conn, tree, status.as_deref())?;
            }
            GoalsAction::Eval { id, note } => {
                if goals::eval(&conn, id, &note)? {
                    println!("Goal [{}] evaluated.", id);
                } else {
                    println!("Not found: {}", id);
                }
            }
            GoalsAction::Complete { id, note } => {
                if goals::complete(&conn, id, note.as_deref())? {
                    println!("Goal [{}] complete.", id);
                } else {
                    println!("Not found: {}", id);
                }
            }
            GoalsAction::Status => goals::status_cmd(&conn)?,
        },

        Command::Memory { action } => match action {
            MemoryAction::Status => memory::status(&conn)?,
            MemoryAction::Weekly => memory::weekly(&conn)?,
            MemoryAction::Track { key, tier } => memory::track(&conn, &key, &tier)?,
            MemoryAction::Access { key } => memory::access(&conn, &key)?,
        },

        Command::Status => {
            println!("{}", "=== stratum-mind status ===".bold());
            lesson::stats(&conn)?;
            goals::status_cmd(&conn)?;
            world::status(&conn)?;
            memory::status(&conn)?;
        }
    }

    Ok(())
}
