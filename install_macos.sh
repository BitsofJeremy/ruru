#!/bin/bash
#
# install_macos.sh — Install ruru as a macOS background service (launchd)
#                    behind an nginx reverse proxy.
#
#   Usage:   sudo bash install_macos.sh
#
# SUDO-SAFE DESIGN
#   Run via `sudo`. Paths are derived from THIS script's location (never $HOME),
#   and every user-level step (`uv sync`, DB init, the launchd *user* agent) is
#   re-executed as the real invoking user via SUDO_USER — so the venv stays
#   owned by you and the LaunchAgent lands in YOUR ~/Library/LaunchAgents and
#   runs in YOUR GUI login domain. Only nginx (port 80) actually needs root.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Resolve the REAL invoking user (we are root because of `sudo`)
# ---------------------------------------------------------------------------
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_USER="$SUDO_USER"
else
    REAL_USER="${USER:-$(whoami)}"
fi
REAL_UID="$(id -u "$REAL_USER")"
REAL_HOME="$(dscl . -read "/Users/$REAL_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
[[ -n "$REAL_HOME" ]] || REAL_HOME="$(eval echo "~$REAL_USER")"
REAL_GROUP="$(id -gn "$REAL_USER")"

# ---------------------------------------------------------------------------
# 1. Paths (derived — survive sudo)
# ---------------------------------------------------------------------------
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$APP_DIR/data"
CACHE_DIR="$WORK_DIR/cache"
LOG_DIR="$WORK_DIR/logs"
PORT=5151
LABEL="com.jeremy.ruru.web"
OLD_LABEL="com.jeremy.weirding.web"
PLIST_PATH="$REAL_HOME/Library/LaunchAgents/${LABEL}.plist"
OLD_PLIST_PATH="$REAL_HOME/Library/LaunchAgents/${OLD_LABEL}.plist"

VENV_PY="$APP_DIR/.venv/bin/python"
VENV_BIN="$APP_DIR/.venv/bin/ruru"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
[[ -n "$UV_BIN" ]] || UV_BIN="$REAL_HOME/.local/bin/uv"
BREW_BIN="${BREW_BIN:-/opt/homebrew/bin/brew}"

# Run a command AS the real user (preserve PATH + HOME so uv/brew work).
as_user() { sudo -u "$REAL_USER" env "PATH=$PATH" "HOME=$REAL_HOME" "$@"; }

c() { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok() { printf '\033[1;32m%s\033[0m\n' "$*"; }
err() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

c "🚀 ruru macOS install"
echo "   app dir   : $APP_DIR"
echo "   work dir  : $WORK_DIR"
echo "   real user : $REAL_USER (uid $REAL_UID, home $REAL_HOME)"
echo "   app port  : $PORT   nginx port: 80"
echo "   running as: $(whoami) (uid $(id -u))"
echo

# ---------------------------------------------------------------------------
# 2. Directory structure (owned by the real user)
# ---------------------------------------------------------------------------
c "📂 1/6  Creating cache + log directories..."
mkdir -p "$CACHE_DIR/uploads" "$CACHE_DIR/chunks" "$CACHE_DIR/output" "$CACHE_DIR/voices" "$LOG_DIR"
chown -R "$REAL_USER:$REAL_GROUP" "$WORK_DIR"

# ---------------------------------------------------------------------------
# 3. Python environment (uv sync) — as real user so .venv stays user-owned
# ---------------------------------------------------------------------------
c "🐍 2/6  Syncing Python dependencies (uv sync)..."
if [[ -x "$UV_BIN" ]]; then
    as_user "$UV_BIN" sync --project "$APP_DIR"
else
    err "   uv not found ($UV_BIN) — skipping sync. Ensure the venv already exists."
fi
if [[ ! -x "$VENV_BIN" ]]; then
    err "❌ Missing executable: $VENV_BIN"
    echo "   Run as $REAL_USER:  cd \"$APP_DIR\" && uv sync"
    exit 1
fi

# ---------------------------------------------------------------------------
# 4. Database init + migrations (idempotent: create_all + progress column)
# ---------------------------------------------------------------------------
c "🗄️  3/6  Initialising database (Base.metadata.create_all + migrations)..."
as_user env "PYTHONPATH=$APP_DIR" "$VENV_PY" - <<'PY'
import asyncio
from config import DB_PATH, migrate_legacy_db_if_needed
from models.db import init_db
if migrate_legacy_db_if_needed():
    print("   migrated legacy weirding.db -> ruru.db (job history preserved)")
asyncio.run(init_db(DB_PATH))
print("   init_db OK ->", DB_PATH)
PY

# ---------------------------------------------------------------------------
# 5. launchd USER agent — plist + reload. Runs as real user, persists at login.
# ---------------------------------------------------------------------------
# MIGRATION: unload the OLD (pre-rebrand) WeirDing label first. If we skip
# this, the old agent keeps holding port 5151 and the new com.jeremy.ruru.web
# agent fails to bind (see "Port conflict" in CLAUDE.md Common Pitfalls).
if [[ -f "$OLD_PLIST_PATH" ]] || as_user launchctl list "$OLD_LABEL" >/dev/null 2>&1; then
    c "🔀 Migrating from old launchd label ${OLD_LABEL}..."
    as_user launchctl bootout "gui/${REAL_UID}/${OLD_LABEL}" 2>/dev/null || true
    as_user launchctl unload "$OLD_PLIST_PATH" 2>/dev/null || true
    rm -f "$OLD_PLIST_PATH"
    ok "   old agent ${OLD_LABEL} unloaded and removed"
fi

c "⚙️  4/6  Writing launchd agent plist..."
mkdir -p "$(dirname "$PLIST_PATH")"
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_BIN}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>RURU_WORK_DIR</key>
        <string>${WORK_DIR}</string>
        <key>RURU_PORT</key>
        <string>${PORT}</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/ruru.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/ruru.err</string>
    <key>WorkingDirectory</key>
    <string>${APP_DIR}</string>
</dict>
</plist>
EOF
chown "$REAL_USER:$REAL_GROUP" "$PLIST_PATH"

c "♻️   Reloading launchd agent (as $REAL_USER)..."
# A USER LaunchAgent must be loaded by its owning user. From this sudo'd (root)
# script we re-exec launchctl as the real user via the legacy `load -w` API:
# the modern `bootstrap` API throws "Input/output error" under `sudo -u`, and
# root CANNOT load into a user GUI domain (a root bootout even silently
# removes the service with no way to reload it). We verify and fall back to a
# one-liner the user runs themselves if the auto-load doesn't take.
as_user launchctl unload "$PLIST_PATH" 2>/dev/null || true
as_user launchctl load -w "$PLIST_PATH" 2>/dev/null || true
sleep 2
if as_user launchctl list "$LABEL" >/dev/null 2>&1; then
    ok "   service loaded: $LABEL"
else
    err "   ⚠️  Auto-load from the sudo context did not take (this is normal on macOS)."
    echo "      Run this ONE command as yourself (NOT sudo):"
    echo "        launchctl load -w \"$PLIST_PATH\""
fi

# ---------------------------------------------------------------------------
# 6. nginx reverse proxy (port 80 -> app). Genuinely needs root.
# ---------------------------------------------------------------------------
c "🌐 5/6  Configuring nginx reverse proxy (port 80 -> 127.0.0.1:${PORT})..."

# Install nginx as the REAL user (Homebrew refuses to run `install` as root).
if ! command -v nginx >/dev/null 2>&1 && [[ -x "$BREW_BIN" ]]; then
    as_user "$BREW_BIN" install nginx
fi
NGINX_BIN="$(command -v nginx || echo "/opt/homebrew/bin/nginx")"
# Homebrew keeps nginx config at the GLOBAL etc — $(brew --prefix)/etc/nginx.
# ($(brew --prefix nginx)/etc/nginx does NOT exist; the keg prefix is wrong here.)
NGINX_ETC="$("$BREW_BIN" --prefix 2>/dev/null || echo /opt/homebrew)/etc/nginx"
NGINX_CONF_DIR="$NGINX_ETC/servers"
NGINX_SITE="$NGINX_CONF_DIR/ruru.conf"
OLD_NGINX_SITE="$NGINX_CONF_DIR/weirding.conf"
mkdir -p "$NGINX_CONF_DIR"

# MIGRATION: remove the old pre-rebrand nginx site fragment so we don't end
# up with two server{} blocks both matching `server_name localhost;`.
if [[ -f "$OLD_NGINX_SITE" ]]; then
    c "🔀 Removing old nginx site fragment ${OLD_NGINX_SITE}..."
    rm -f "$OLD_NGINX_SITE"
fi

# Ensure nginx.conf loads per-site fragments from servers/. Stock Homebrew
# nginx.conf already has `include servers/*;` — only patch if missing.
if ! grep -Eq 'include[[:space:]]+servers/\*' "$NGINX_ETC/nginx.conf" 2>/dev/null; then
    c "   adding 'include servers/*.conf;' to nginx.conf http{} block..."
    "$VENV_PY" - "$NGINX_ETC/nginx.conf" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
if 'include servers/' not in s:
    s = re.sub(r'(http\s*\{)', r'\1\n    include servers/*.conf;', s, count=1)
    open(p, 'w').write(s)
    print("   patched nginx.conf")
PY
fi

cat > "$NGINX_SITE" <<EOF
# ruru reverse proxy — managed by install_macos.sh
server {
    listen 80;
    server_name localhost;

    client_max_body_size 0;        # no upload limit (epubs / audio)
    proxy_read_timeout 86400s;     # long-running TTS jobs

    # Serve static assets + generated audio straight from disk (fast, and
    # avoids streaming large files through the Python process).
    location /static/        { alias ${APP_DIR}/static/; }
    location /audio/output/  { alias ${CACHE_DIR}/output/; }
    location /audio/voices/  { alias ${CACHE_DIR}/voices/; }

    # Everything else -> FastAPI app
    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
    }
}
EOF

# Validate the full nginx config before touching the running service.
if ! "$NGINX_BIN" -t; then
    err "❌ nginx config test failed — NOT reloading. Inspect $NGINX_SITE."
    exit 1
fi

# Restart nginx as root (port 80 is privileged). `brew services` is root-safe.
if [[ -x "$BREW_BIN" ]]; then
    env "HOME=$REAL_HOME" "$BREW_BIN" services restart nginx
else
    "$NGINX_BIN" -s reload 2>/dev/null || "$NGINX_BIN"
fi

# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------
ok "✅ Installation complete"
echo "--------------------------------------------------------------------------------"
echo " Service : $LABEL"
echo " App URL : http://localhost:${PORT}"
echo " Proxy   : http://localhost   (nginx:80 -> app:${PORT})"
echo " Logs    : ${LOG_DIR}/ruru.log  (+ .err)"
echo " Status  : launchctl list | grep ruru"
echo " Stop    : launchctl bootout gui/${REAL_UID}/${LABEL}   (as $REAL_USER, no sudo needed)"
echo " Restart : launchctl kickstart -k gui/${REAL_UID}/${LABEL}"
echo "--------------------------------------------------------------------------------"
