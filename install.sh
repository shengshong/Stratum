#!/usr/bin/env bash
# install.sh — Stratum installer
# https://github.com/doublegate/stratum
#
# Installs all Stratum modules, seeds workspace templates, and initializes the
# canonical cron set into an existing OpenClaw instance.
#
# Prerequisites: Rust (rustup), Python 3.11+ (uv), OpenClaw, sqlite3
# Usage: ./install.sh [--skip-rust] [--skip-python] [--skip-crons] [--dry-run]

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[stratum]${RESET} $*"; }
success() { echo -e "${GREEN}[stratum]${RESET} ✓ $*"; }
warn()    { echo -e "${YELLOW}[stratum]${RESET} ⚠ $*"; }
error()   { echo -e "${RED}[stratum]${RESET} ✗ $*"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}══ $* ══${RESET}"; }

DRY_RUN=false
SKIP_RUST=false
SKIP_PYTHON=false
SKIP_CRONS=false

for arg in "$@"; do
  case $arg in
    --dry-run)    DRY_RUN=true ;;
    --skip-rust)  SKIP_RUST=true ;;
    --skip-python) SKIP_PYTHON=true ;;
    --skip-crons) SKIP_CRONS=true ;;
  esac
done

run() {
  if $DRY_RUN; then echo -e "  ${YELLOW}[dry-run]${RESET} $*"; else eval "$@"; fi
}

STRATUM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
cat << 'BANNER'
  ███████╗████████╗██████╗  █████╗ ████████╗██╗   ██╗███╗   ███╗
  ██╔════╝╚══██╔══╝██╔══██╗██╔══██╗╚══██╔══╝██║   ██║████╗ ████║
  ███████╗   ██║   ██████╔╝███████║   ██║   ██║   ██║██╔████╔██║
  ╚════██║   ██║   ██╔══██╗██╔══██║   ██║   ██║   ██║██║╚██╔╝██║
  ███████║   ██║   ██║  ██║██║  ██║   ██║   ╚██████╔╝██║ ╚═╝ ██║
  ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝
BANNER
echo -e "${RESET}${CYAN}  The self-optimization layer for AI agents.${RESET}"
echo -e "  https://github.com/doublegate/stratum\n"

# ── Preflight checks ──────────────────────────────────────────────────────────
header "Preflight"

command -v openclaw >/dev/null 2>&1 || error "OpenClaw not found. Install it first: https://github.com/openclaw/openclaw"
command -v sqlite3  >/dev/null 2>&1 || error "sqlite3 not found. Install via your package manager."

if ! $SKIP_RUST; then
  command -v cargo >/dev/null 2>&1 || error "Rust/cargo not found. Install via: https://rustup.rs"
  success "Rust $(rustc --version | awk '{print $2}')"
fi

if ! $SKIP_PYTHON; then
  command -v uv >/dev/null 2>&1 || error "uv not found. Install via: https://github.com/astral-sh/uv"
  success "uv $(uv --version | awk '{print $2}')"
fi

success "OpenClaw $(openclaw --version 2>/dev/null | head -1 || echo '(version unknown)')"

# ── User configuration ────────────────────────────────────────────────────────
header "Configuration"

CONFIG_DIR="$HOME/.stratum"
CONFIG_FILE="$CONFIG_DIR/config.json"

if [[ -f "$CONFIG_FILE" ]]; then
  warn "Config already exists at $CONFIG_FILE — skipping prompts."
  source <(python3 -c "
import json, os
c = json.load(open('$CONFIG_FILE'))
print('USER_NAME=' + repr(c.get('user',{}).get('name','User')))
print('WORKSPACE=' + repr(os.path.expanduser(c.get('paths',{}).get('workspace','~/clawd'))))
print('TIMEZONE='  + repr(c.get('user',{}).get('timezone','UTC')))
print('TELEGRAM_ID=' + repr(str(c.get('user',{}).get('telegram_id',''))))
")
else
  echo ""
  read -rp "  Your name (for persona templates): " USER_NAME
  read -rp "  OpenClaw workspace path [~/clawd]: " WORKSPACE
  WORKSPACE="${WORKSPACE:-$HOME/clawd}"
  WORKSPACE="${WORKSPACE/#\~/$HOME}"
  read -rp "  Your timezone [America/New_York]: " TIMEZONE
  TIMEZONE="${TIMEZONE:-America/New_York}"
  read -rp "  Telegram chat ID (optional, for notifications): " TELEGRAM_ID

  run mkdir -p "$CONFIG_DIR"
  run chmod 700 "$CONFIG_DIR"

  python3 -c "
import json
config = {
  'user': {
    'name': '$USER_NAME',
    'timezone': '$TIMEZONE',
    'telegram_id': '$TELEGRAM_ID'
  },
  'paths': {
    'workspace': '$WORKSPACE',
    'data': '$HOME/.local/share/stratum',
    'bin': '$HOME/.local/bin'
  },
  'modules': {
    'lens': {'auto_scale_threshold': 0.85},
    'brain': {'consolidation_hour': 3},
    'continuity': {'snapshot_interval_hours': 2}
  }
}
json.dump(config, open('$CONFIG_DIR/config.json', 'w'), indent=2)
print('Config written.')
"
fi

DATA_DIR="$HOME/.local/share/stratum"
BIN_DIR="$HOME/.local/bin"
run mkdir -p "$DATA_DIR" "$BIN_DIR"
success "Config at $CONFIG_FILE"

# ── Build Rust modules ────────────────────────────────────────────────────────
if ! $SKIP_RUST; then
  header "Building Rust Modules"

  RUST_MODULES=(stratum-mind stratum-watch stratum-ops stratum-agent-monitor stratum-boot-health)

  for module in "${RUST_MODULES[@]}"; do
    module_dir="$STRATUM_DIR/modules/$module"
    if [[ -f "$module_dir/Cargo.toml" ]]; then
      info "Building $module..."
      run "cd '$module_dir' && cargo build --release 2>&1 | tail -3"
      run "cp '$module_dir/target/release/$module' '$BIN_DIR/'"
      success "$module → $BIN_DIR/$module"
    else
      warn "No Cargo.toml found for $module — skipping"
    fi
  done
fi

# ── Install Python modules ────────────────────────────────────────────────────
if ! $SKIP_PYTHON; then
  header "Installing Python Modules"

  PYTHON_MODULES=(stratum-brain stratum-lens stratum-continuity stratum-reports)

  for module in "${PYTHON_MODULES[@]}"; do
    module_dir="$STRATUM_DIR/modules/$module"
    if [[ -f "$module_dir/pyproject.toml" ]]; then
      info "Installing $module..."
      run "cd '$module_dir' && uv tool install . --force"
      success "$module installed"
    else
      warn "No pyproject.toml for $module — skipping"
    fi
  done
fi

# ── Initialize databases ──────────────────────────────────────────────────────
header "Initializing Databases"

run "sqlite3 '$DATA_DIR/mind.db' < '$STRATUM_DIR/scripts/init-mind-db.sql'"
run "sqlite3 '$DATA_DIR/watch.db' < '$STRATUM_DIR/scripts/init-watch-db.sql'"
run "sqlite3 '$DATA_DIR/ops.db' < '$STRATUM_DIR/scripts/init-ops-db.sql'"
success "Databases initialized at $DATA_DIR/"

# ── Copy workspace templates ──────────────────────────────────────────────────
header "Workspace Templates"

WORKSPACE="${WORKSPACE:-$HOME/clawd}"

if [[ ! -d "$WORKSPACE" ]]; then
  warn "Workspace $WORKSPACE does not exist — creating it"
  run mkdir -p "$WORKSPACE"
fi

TEMPLATES_DIR="$STRATUM_DIR/templates"

for template in SOUL.md AGENTS.md HEARTBEAT.md MEMORY.md; do
  dest="$WORKSPACE/$template"
  if [[ -f "$dest" ]]; then
    warn "$template already exists in workspace — skipping (see templates/$template for reference)"
  else
    run "sed 's/{{YOUR_NAME}}/$USER_NAME/g; s/{{TIMEZONE}}/$TIMEZONE/g' '$TEMPLATES_DIR/$template' > '$dest'"
    success "Installed $template → $workspace/$template"
  fi
done

info "USER.md requires personal info — copy from templates/USER.md and fill it in manually."

# ── Seed cron jobs ────────────────────────────────────────────────────────────
if ! $SKIP_CRONS; then
  header "Seeding Cron Jobs"
  run "bash '$STRATUM_DIR/crons/seed-crons.sh'"
  success "Canonical cron set installed into OpenClaw"
fi

# ── Final health check ────────────────────────────────────────────────────────
header "Health Check"
if command -v stratum-brain >/dev/null 2>&1; then
  run "stratum-brain status"
else
  warn "stratum-brain not in PATH yet — open a new shell, then run: stratum-brain status"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}══ Stratum installed successfully ══${RESET}"
echo ""
echo -e "  Next steps:"
echo -e "  1. Edit ${CYAN}$WORKSPACE/SOUL.md${RESET} — define your agent's character"
echo -e "  2. Edit ${CYAN}$WORKSPACE/USER.md${RESET} — tell your agent who you are"
echo -e "  3. Run ${CYAN}stratum-brain heartbeat${RESET} to activate the integration loop"
echo -e "  4. Read ${CYAN}docs/quickstart.md${RESET} for a guided first-session walkthrough"
echo ""
echo -e "  Documentation: https://github.com/doublegate/stratum/docs"
echo ""
