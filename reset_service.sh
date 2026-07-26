#!/usr/bin/env bash
# ruru service reset, restart, and cleanup script
# Usage: ./reset_service.sh [clean-all|reset-db|clean-only|restart-only|help]
#
# Works on both installs:
#   Linux  -> systemd system unit  (ruru-web.service, see install_linux.sh)
#   macOS  -> launchd user agent   (com.jeremy.ruru.web, see install_macos.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
WORK_DIR="${RURU_WORK_DIR:-$APP_DIR/data}"
CACHE_DIR="$WORK_DIR/cache"
LOG_DIR="$WORK_DIR/logs"
LOG_FILE="$LOG_DIR/ruru.log"
ERR_FILE="$LOG_DIR/ruru.err"
DB_FILE="$WORK_DIR/ruru.db"
OUTPUT_DIR="$CACHE_DIR/output"
CHUNK_DIR="$CACHE_DIR/chunks"
UPLOADS_DIR="$CACHE_DIR/uploads"
VOICE_DIR="$CACHE_DIR/voices"

# Platform-specific service handles
SERVICE_NAME="ruru-web"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.jeremy.ruru.web.plist"

case "$(uname -s)" in
    Linux)  PLATFORM="linux" ;;
    Darwin) PLATFORM="macos" ;;
    *)      PLATFORM="unknown" ;;
esac

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}ℹ $*${NC}"; }
log_warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
log_error() { echo -e "${RED}✗ $*${NC}"; }

usage() {
    cat << EOF
ruru Service Control Script  (detected platform: $PLATFORM)

Usage: $0 [COMMAND]

Commands:
  clean-only   Stop service, clear output/chunks/uploads/logs only (DB preserved)
  reset-db     Stop service, clear state, delete the database, restart
  clean-all    Same as reset-db (schema + voices are re-seeded on startup)
  restart-only Stop service, clear state, restart service
  help         Show this help message

Examples:
  $0 clean-only   # Quick cleanup without touching the database
  $0 reset-db     # Wipe everything and start fresh with seeded voices
  $0 restart-only # Quick restart after code changes

EOF
}

stop_service() {
    log_info "Stopping ruru service..."
    if [[ "$PLATFORM" == "linux" ]]; then
        # Capture first, then grep — piping into `grep -q` under `set -o pipefail`
        # reports failure even on a match (SIGPIPE kills the producer, 141).
        unit_files="$(systemctl list-unit-files --no-pager --plain 2>/dev/null || true)"
        if grep -q "^${SERVICE_NAME}\.service" <<<"$unit_files"; then
            sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
            sleep 1
        else
            log_warn "No ${SERVICE_NAME}.service installed — run install_linux.sh first"
        fi
    elif [[ "$PLATFORM" == "macos" ]]; then
        # Same pipefail/SIGPIPE hazard as the Linux branch above.
        agents="$(launchctl list 2>/dev/null || true)"
        if grep -q "com.jeremy.ruru.web" <<<"$agents"; then
            launchctl unload "$LAUNCH_AGENT" 2>/dev/null || true
            sleep 1
        fi
    fi

    # Kill stragglers — a manually started `uv run ruru` will otherwise keep
    # holding the port out from under the service (see CLAUDE.md pitfalls).
    pkill -f "\.venv/bin/ruru" 2>/dev/null || true
    pkill -f "python.*app\.py" 2>/dev/null || true
    sleep 0.5

    log_info "Service stopped"
}

start_service() {
    log_info "Starting ruru service..."
    mkdir -p "$OUTPUT_DIR" "$CHUNK_DIR" "$UPLOADS_DIR" "$VOICE_DIR" "$LOG_DIR"

    if [[ "$PLATFORM" == "linux" ]]; then
        sudo systemctl start "$SERVICE_NAME" || {
            log_error "Failed to start $SERVICE_NAME"
            exit 1
        }
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log_info "Service started successfully"
        else
            log_error "Service failed to start. Check: journalctl -u $SERVICE_NAME -n 50"
            exit 1
        fi
    elif [[ "$PLATFORM" == "macos" ]]; then
        launchctl load -w "$LAUNCH_AGENT" 2>/dev/null || {
            log_error "Failed to load launch agent"
            exit 1
        }
        sleep 2
        if pgrep -f "\.venv/bin/ruru" > /dev/null; then
            log_info "Service started successfully"
        else
            log_error "Service failed to start. Check logs: $LOG_FILE"
            exit 1
        fi
    fi
}

clear_state() {
    log_info "Clearing application state..."

    rm -rf "${OUTPUT_DIR:?}"/* 2>/dev/null || true
    log_info "  - Cleared audio output"

    rm -rf "${CHUNK_DIR:?}"/* 2>/dev/null || true
    log_info "  - Cleared chunk scratch files"

    find "$VOICE_DIR" -name "*_sample.wav" -delete 2>/dev/null || true
    find "$VOICE_DIR" -name "*_ref.wav" -delete 2>/dev/null || true
    log_info "  - Cleared voice samples"

    rm -rf "${UPLOADS_DIR:?}"/* 2>/dev/null || true
    log_info "  - Cleared uploads"

    if [[ "$PLATFORM" == "linux" ]]; then
        log_info "  - Logs go to the journal: journalctl -u $SERVICE_NAME"
    else
        rm -f "$LOG_FILE" "$ERR_FILE"
        log_info "  - Cleared log files"
    fi

    log_info "State cleared"
}

reset_database() {
    log_warn "WARNING: This will delete ALL jobs and voices from the database!"
    read -r -p "Continue? (yes/no): " confirm

    if [[ "$confirm" != "yes" ]]; then
        log_info "Database reset cancelled"
        return
    fi

    rm -f "$DB_FILE" "$DB_FILE-wal" "$DB_FILE-shm"
    log_info "Database reset complete"
    # Schema + seeding are handled by app.py's lifespan on startup
    # (init_db + seed_voices + scan_voice_clones).
}

case "${1:-help}" in
    clean-only)
        stop_service
        clear_state
        ;;
    reset-db|clean-all)
        stop_service
        clear_state
        reset_database
        start_service
        ;;
    restart-only)
        stop_service
        clear_state
        start_service
        ;;
    help|--help|-h|"")
        usage
        ;;
    *)
        log_error "Unknown command: $1"
        usage
        exit 1
        ;;
esac

log_info "Done!"
