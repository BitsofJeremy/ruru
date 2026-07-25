#!/usr/bin/env bash
# ruru service reset, restart, and cleanup script
# Usage: ./reset_service.sh [clean-all|reset-db|clean-only|restart-only|help]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.jeremy.ruru.web.plist"
OLD_LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.jeremy.weirding.web.plist"
LOG_FILE="$APP_DIR/data/logs/ruru.log"
ERR_FILE="$APP_DIR/data/logs/ruru.err"
DB_FILE="$APP_DIR/data/ruru.db"
LEGACY_DB_FILE="$APP_DIR/data/weirding.db"
OUTPUT_DIR="$APP_DIR/data/cache/output"
UPLOADS_DIR="$APP_DIR/uploads"
VOICE_DIR="$APP_DIR/data/cache/voices"
_REPO="$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}ℹ $*${NC}"; }
log_warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
log_error() { echo -e "${RED}✗ $*${NC}"; }

usage() {
    cat << EOF
ruru Service Control Script

Usage: $0 [COMMAND]

Commands:
  clean-only   Stop service, clear output/uploads/log only (DB preserved)
  reset-db     Stop service, clear output/uploads/log, reset database and seed voices
  clean-all    Stop service, clear everything (including database and voices)
  restart-only Stop service, clear output/uploads/log, restart service
  help         Show this help message

Examples:
  $0 clean-only   # Quick cleanup without touching database
  $0 reset-db     # Wipe everything and start fresh with seeded voices
  $0 restart-only # Quick restart after code changes

EOF
}

# Stop the service gracefully
stop_service() {
    log_info "Stopping ruru service..."

    # MIGRATION: unload the OLD (pre-rebrand) WeirDing label too, if present,
    # so it doesn't keep holding the port out from under the new agent (see
    # "Port conflict" in CLAUDE.md Common Pitfalls).
    if launchctl list | grep -q "com.jeremy.weirding.web"; then
        log_warn "Found old WeirDing launchd agent — unloading it as part of the rebrand migration"
        launchctl unload "$OLD_LAUNCH_AGENT" 2>/dev/null || true
        rm -f "$OLD_LAUNCH_AGENT"
        sleep 1
    fi

    # Try unload first (graceful)
    if launchctl list | grep -q "com.jeremy.ruru.web"; then
        launchctl unload "$LAUNCH_AGENT" 2>/dev/null || true
        sleep 1
    fi

    # Kill any remaining processes
    pkill -f "ruru" 2>/dev/null || true
    pkill -f "weirding" 2>/dev/null || true
    pkill -f "python.*app.py" 2>/dev/null || true
    sleep 0.5

    log_info "Service stopped"
}

# Seed voices from _voices directory
seed_voices() {
    if [[ ! -d "$_REPO" ]]; then
        log_warn " voices directory not found: $_REPO"
        log_info "Skipping voice seeding"
        return
    fi

    log_info "Seeding voices from $_REPO..."

    # Import seed function
    export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
    python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, str(Path('$SCRIPT_DIR')))
from models.seed_voices import seed_voices

voice_dir = Path('$VOICE_DIR')
db_path = Path('$DB_FILE')
_repo = Path('$_REPO')

if not _repo.exists():
    print(' voices directory not found, skipping seeding')
    sys.exit(0)

seed_voices(db_path, _repo, voice_dir)
print(f'Seeded voices from {_repo}')
    " || {
        log_error "Failed to seed voices"
        return 1
    }

    log_info "Voice seeding complete"
}

# Clear application state
clear_state() {
    log_info "Clearing application state..."

    # Clear generated audio files
    rm -rf "$OUTPUT_DIR"/*
    log_info "  - Cleared audio output"

    # Clear voice sample files
    find "$VOICE_DIR" -name "*_sample.wav" -delete 2>/dev/null || true
    find "$VOICE_DIR" -name "*_ref.wav" -delete 2>/dev/null || true
    log_info "  - Cleared voice samples"

    # Clear uploads
    rm -rf "$UPLOADS_DIR"/*
    log_info "  - Cleared uploads"

    # Clear log files
    rm -f "$LOG_FILE" "$ERR_FILE"
    log_info "  - Cleared log files"

    log_info "State cleared"
}

# Reset database (WARNING: deletes all data)
reset_database() {
    log_warn "WARNING: This will delete ALL jobs and voices from the database!"
    read -p "Continue? (yes/no): " confirm

    if [[ "$confirm" != "yes" ]]; then
        log_info "Database reset cancelled"
        return
    fi

    rm -f "$DB_FILE"
    # Also drop the legacy pre-rebrand DB file if present -- a full reset
    # means a full reset, don't leave it around to get silently migrated in
    # on the next start.
    rm -f "$LEGACY_DB_FILE"
    log_info "Database reset complete"

    # Schema + seeding handled by app.py lifespan on startup (init_db + seed_voices + scan_voice_clones)
}

# Start the service
start_service() {
    log_info "Starting ruru service..."

    # Ensure directories exist
    mkdir -p "$OUTPUT_DIR" "$UPLOADS_DIR" "$VOICE_DIR" "$APP_DIR/data/logs"

    # Load the service
    launchctl load -w "$LAUNCH_AGENT" 2>/dev/null || {
        log_error "Failed to load launch agent"
        exit 1
    }

    sleep 2

    # Verify it's running
    if pgrep -f "ruru" > /dev/null; then
        log_info "Service started successfully"
    else
        log_error "Service failed to start. Check logs: $LOG_FILE"
        exit 1
    fi
}

# Main dispatcher
case "${1:-help}" in
    clean-only)
        stop_service
        clear_state
        ;;
    reset-db)
        stop_service
        clear_state
        reset_database
        start_service
        ;;
    clean-all)
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
