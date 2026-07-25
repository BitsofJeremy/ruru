#!/usr/bin/env bash
# WeirDing uninstall script — reverses setup.sh.
# Removes systemd services and Nginx config.
# Does NOT remove Redis, Nginx, or ffmpeg (system packages).
# Pass --purge to also delete the work directory (all data, voices, and DB).
set -euo pipefail

# ---------------------------------------------------------------------------
# Configurable variables (override via environment)
# ---------------------------------------------------------------------------
WORK_DIR="${WEIRDING_WORK_DIR:-${HOME}/weirding}"
PORT="${WEIRDING_PORT:-8400}"
PURGE=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --purge) PURGE=1 ;;
        --help|-h)
            echo "Usage: $0 [--purge]"
            echo ""
            echo "  --purge   Also delete the work directory (${WORK_DIR})."
            echo "            WARNING: permanently removes all generated audio, voices, and the database."
            exit 0
            ;;
        *)
            echo "[FAIL]  Unknown argument: ${arg}" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
fatal() { fail "$@"; exit 1; }

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo -e "${BOLD}${RED}"
cat << 'BANNER'
 _    _  _      ____    _
| |  | |(_)    |  _ \  (_)
| |/\| | _  ___| |_) |  _  _ __   __ _
| /  \ || |/ _ \    /  | || '_ \ / _` |
\  /\  /| |  __/ |\ \  | || | | | (_| |
 \/  \/ |_|\___|_| \_\ |_||_| |_|\__, |
                                   __/ |
                                  |___/
BANNER
echo -e "${NC}"
info "WeirDing uninstall — $(date)"
info "Work dir:  ${WORK_DIR}"
if [ "${PURGE}" -eq 1 ]; then
    warn "Mode:      PURGE (work directory will be deleted)"
else
    info "Mode:      Services only (work directory preserved)"
fi
echo ""

# ---------------------------------------------------------------------------
# Require sudo
# ---------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    fatal "This script must be run with sudo: sudo bash $0 $*"
fi

# ---------------------------------------------------------------------------
# --purge confirmation
# ---------------------------------------------------------------------------
if [ "${PURGE}" -eq 1 ]; then
    echo -e "${RED}${BOLD}WARNING: --purge will permanently delete:${NC}"
    echo -e "  ${WORK_DIR}/data/output   (all generated audiobooks)"
    echo -e "  ${WORK_DIR}/data/voices   (all cloned voice audio)"
    echo -e "  ${WORK_DIR}/data/uploads  (all uploaded documents)"
    echo -e "  ${WORK_DIR}/data/weirding.db  (the database)"
    echo ""
    read -r -p "Type YES to confirm: " CONFIRM
    if [ "${CONFIRM}" != "YES" ]; then
        info "Aborted."
        exit 0
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# 1. Stop and disable systemd services
# ---------------------------------------------------------------------------
info "Stopping and disabling services..."

for svc in weirding-web weirding-worker; do
    if systemctl is-active --quiet "${svc}" 2>/dev/null; then
        systemctl stop "${svc}"
        ok "Stopped ${svc}"
    else
        info "${svc} is not running — skipping stop"
    fi

    if systemctl is-enabled --quiet "${svc}" 2>/dev/null; then
        systemctl disable "${svc}"
        ok "Disabled ${svc}"
    else
        info "${svc} is not enabled — skipping disable"
    fi
done

echo ""

# ---------------------------------------------------------------------------
# 2. Kill any remaining processes on the port
# ---------------------------------------------------------------------------
info "Checking for lingering processes on port ${PORT}..."

PIDS_ON_PORT="$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+' || true)"

if [ -n "${PIDS_ON_PORT}" ]; then
    for PID in ${PIDS_ON_PORT}; do
        PROC_CMD="$(ps -o args= -p "${PID}" 2>/dev/null || echo 'unknown')"
        warn "Killing PID ${PID}: ${PROC_CMD}"
        kill "${PID}" 2>/dev/null && ok "Killed PID ${PID}" || warn "Could not kill PID ${PID}"
    done
else
    ok "No processes on port ${PORT}"
fi

echo ""

# ---------------------------------------------------------------------------
# 3. Remove systemd service files
# ---------------------------------------------------------------------------
info "Removing systemd service files..."

for svc_file in /etc/systemd/system/weirding-web.service \
                /etc/systemd/system/weirding-worker.service; do
    if [ -f "${svc_file}" ]; then
        rm -f "${svc_file}"
        ok "Removed ${svc_file}"
    else
        info "${svc_file} not found — skipping"
    fi
done

systemctl daemon-reload
ok "Systemd daemon reloaded"

echo ""

# ---------------------------------------------------------------------------
# 4. Remove Nginx site config
# ---------------------------------------------------------------------------
info "Removing Nginx config..."

NGINX_LINK="/etc/nginx/sites-enabled/weirding"
NGINX_CONF="/etc/nginx/sites-available/weirding"

if [ -L "${NGINX_LINK}" ] || [ -f "${NGINX_LINK}" ]; then
    rm -f "${NGINX_LINK}"
    ok "Removed ${NGINX_LINK}"
else
    info "${NGINX_LINK} not found — skipping"
fi

if [ -f "${NGINX_CONF}" ]; then
    rm -f "${NGINX_CONF}"
    ok "Removed ${NGINX_CONF}"
else
    info "${NGINX_CONF} not found — skipping"
fi

# Reload Nginx if it's running
if command -v nginx &>/dev/null && systemctl is-active --quiet nginx 2>/dev/null; then
    if nginx -t 2>/dev/null; then
        systemctl reload nginx
        ok "Nginx reloaded"
    else
        warn "Nginx config test failed after removal — check manually"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 5. Optionally purge work directory
# ---------------------------------------------------------------------------
if [ "${PURGE}" -eq 1 ]; then
    info "Purging work directory: ${WORK_DIR}..."
    if [ -d "${WORK_DIR}" ]; then
        rm -rf "${WORK_DIR}"
        ok "Deleted ${WORK_DIR}"
    else
        info "${WORK_DIR} not found — nothing to delete"
    fi
    echo ""
else
    info "Work directory preserved: ${WORK_DIR}"
    info "To delete it manually: rm -rf ${WORK_DIR}"
    echo ""
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo -e "${GREEN}${BOLD}Uninstall complete.${NC}"
echo ""
echo -e "  Redis and Nginx remain installed (system packages — not touched)."
echo -e "  The repo directory is untouched."
if [ "${PURGE}" -eq 0 ]; then
    echo -e "  Your data is still at: ${WORK_DIR}"
    echo -e "  Remove it manually if no longer needed: rm -rf ${WORK_DIR}"
fi
echo ""
