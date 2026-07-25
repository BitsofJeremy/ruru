#!/usr/bin/env bash
# WeirDing update script — pulls latest code and restarts services.
# Run from any location; determines repo root from script location.
set -euo pipefail

# ---------------------------------------------------------------------------
# Configurable variables (override via environment)
# ---------------------------------------------------------------------------
PORT="${WEIRDING_PORT:-8400}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${SCRIPT_DIR}"
WEIRDING_DIR="${SCRIPT_DIR}"
RESTART_WORKER=0
RUN_USER="${SUDO_USER:-$(whoami)}"

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
fatal() { fail "$@"; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --restart-worker) RESTART_WORKER=1 ;;
        --help|-h)
            echo "Usage: $0 [--restart-worker]"
            echo ""
            echo "  --restart-worker   Force restart of weirding-worker even if"
            echo "                     no engine/tasks files changed."
            exit 0
            ;;
        *)
            fatal "Unknown argument: ${arg}"
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}"
cat << 'BANNER'
__          __  _      _____  _
\ \        / / (_)    |  __ \(_)
 \ \  /\  / /__ _ _ __| |  | |_ _ __   __ _
  \ \/  \/ / _ \ | '__| |  | | | '_ \ / _` |
   \  /\  /  __/ | |  | |__| | | | | | (_| |
    \/  \/ \___|_|_|  |_____/|_|_| |_|\__, |
                                       __/ |
                                      |___/
BANNER
echo -e "${NC}"
info "WeirDing update — $(date)"
info "Repo:  ${WEIRDING_DIR}"
info "Port:  ${PORT}"
info "User:  ${RUN_USER}"
echo ""

# ---------------------------------------------------------------------------
# 1. Verify repo root
# ---------------------------------------------------------------------------
info "Verifying repo structure..."

if [ ! -f "${WEIRDING_DIR}/app.py" ]; then
    fatal "app.py not found under ${WEIRDING_DIR} — is update.sh in the right place?"
fi
ok "Repo confirmed: ${WEIRDING_DIR}"

# ---------------------------------------------------------------------------
# 2. Check git is available
# ---------------------------------------------------------------------------
if ! command -v git &>/dev/null; then
    fatal "git not found — install with: sudo apt install git"
fi
ok "git found: $(git --version)"

# ---------------------------------------------------------------------------
# 3. Show current SHA
# ---------------------------------------------------------------------------
BEFORE_SHA="$(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" rev-parse HEAD)"
BEFORE_SHORT="$(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" rev-parse --short HEAD)"
info "Current commit: ${BEFORE_SHORT}  ($(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" log -1 --format='%s' HEAD))"
echo ""

# ---------------------------------------------------------------------------
# 4. git pull
# ---------------------------------------------------------------------------
info "Pulling latest code..."

PULL_OUTPUT="$(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" pull 2>&1)"
echo "${PULL_OUTPUT}"

if echo "${PULL_OUTPUT}" | grep -q "Already up to date"; then
    ok "Already up to date — nothing to do."
    exit 0
fi

AFTER_SHA="$(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" rev-parse HEAD)"
AFTER_SHORT="$(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" rev-parse --short HEAD)"
ok "Updated to: ${AFTER_SHORT}  ($(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" log -1 --format='%s' HEAD))"
echo ""

# ---------------------------------------------------------------------------
# 5. Kill any manually-started processes holding port 8400
#    (processes matching 'uv run weirding.py' NOT owned by systemd)
# ---------------------------------------------------------------------------
info "Checking for manually-started processes on port ${PORT}..."

# Find PIDs listening on the port (ss output format: users:(("uv",pid=1234,...)))
PIDS_ON_PORT="$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+' || true)"

if [ -n "${PIDS_ON_PORT}" ]; then
    for PID in ${PIDS_ON_PORT}; do
        # Check parent: if PPID=1 and parent cmdline is systemd, it's managed — skip it.
        PPID="$(ps -o ppid= -p "${PID}" 2>/dev/null | tr -d ' ' || echo '')"
        PARENT_CMD="$(ps -o comm= -p "${PPID}" 2>/dev/null || echo '')"

        if [ "${PARENT_CMD}" = "systemd" ]; then
            info "PID ${PID} is owned by systemd — will be restarted via systemctl (skipping kill)"
        else
            PROC_CMD="$(ps -o args= -p "${PID}" 2>/dev/null || echo 'unknown')"
            warn "Found non-systemd process on port ${PORT}: PID ${PID} — ${PROC_CMD}"
            kill "${PID}" 2>/dev/null && ok "Killed PID ${PID}" || warn "Could not kill PID ${PID} (may have already exited)"
        fi
    done
else
    ok "No processes found on port ${PORT}"
fi
echo ""

# ---------------------------------------------------------------------------
# 6. Restart weirding-web
# ---------------------------------------------------------------------------
info "Restarting weirding-web..."
sudo systemctl restart weirding-web
ok "weirding-web restarted"
echo ""

# ---------------------------------------------------------------------------
# 7. Optionally restart weirding-worker
# ---------------------------------------------------------------------------
CHANGED_FILES="$(sudo -u "${RUN_USER}" git -C "${WEIRDING_DIR}" diff --name-only "${BEFORE_SHA}" "${AFTER_SHA}" 2>/dev/null || true)"

if [ "${RESTART_WORKER}" -eq 1 ]; then
    info "Restarting weirding-worker (--restart-worker flag set)..."
    sudo systemctl restart weirding-worker
    ok "weirding-worker restarted"
elif echo "${CHANGED_FILES}" | grep -qE '^(engine/|tasks/)'; then
    info "Engine or tasks files changed — restarting weirding-worker..."
    echo "${CHANGED_FILES}" | grep -E '^(engine/|tasks/)' | while read -r f; do
        info "  changed: ${f}"
    done
    sudo systemctl restart weirding-worker
    ok "weirding-worker restarted"
else
    info "No engine/tasks changes detected — weirding-worker not restarted"
    if [ -n "${CHANGED_FILES}" ]; then
        info "Changed files:"
        echo "${CHANGED_FILES}" | while read -r f; do info "  ${f}"; done
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 8. Health check — wait 3s then hit /health
# ---------------------------------------------------------------------------
info "Waiting 3 seconds for service to come up..."
sleep 3

HEALTH_URL="http://localhost:${PORT}/health"
info "Checking health at ${HEALTH_URL}..."

HTTP_CODE="$(curl -s -o /tmp/weirding_health_response.txt -w "%{http_code}" \
    --connect-timeout 5 --max-time 10 "${HEALTH_URL}" 2>/dev/null || echo '000')"

if [ "${HTTP_CODE}" = "200" ]; then
    HEALTH_BODY="$(cat /tmp/weirding_health_response.txt 2>/dev/null || echo '')"
    ok "Health check passed (HTTP ${HTTP_CODE}): ${HEALTH_BODY}"
else
    warn "Health check returned HTTP ${HTTP_CODE} — service may still be starting"
    warn "Check logs with: journalctl -u weirding-web -n 30"
fi
rm -f /tmp/weirding_health_response.txt
echo ""

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
echo -e "${GREEN}${BOLD}Update complete!${NC}"
echo ""
echo -e "  ${CYAN}Before:${NC}  ${BEFORE_SHORT}  (${BEFORE_SHA})"
echo -e "  ${CYAN}After: ${NC}  ${AFTER_SHORT}  (${AFTER_SHA})"
echo ""
echo -e "${CYAN}Service status:${NC}"
systemctl is-active --quiet weirding-web \
    && echo -e "  weirding-web    ${GREEN}active${NC}" \
    || echo -e "  weirding-web    ${RED}inactive${NC}"
systemctl is-active --quiet weirding-worker \
    && echo -e "  weirding-worker ${GREEN}active${NC}" \
    || echo -e "  weirding-worker ${RED}inactive${NC}"
echo ""
