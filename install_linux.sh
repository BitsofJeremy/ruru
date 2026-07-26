#!/bin/bash
#
# install_linux.sh — Install ruru as a systemd service (NVIDIA/CUDA backend)
#                    behind an nginx reverse proxy.
#
#   Usage:   sudo bash install_linux.sh
#
# SUDO-SAFE DESIGN
#   Run via `sudo`. Paths are derived from THIS script's location (never $HOME),
#   and every user-level step (`uv sync`, DB init) is re-executed as the real
#   invoking user via SUDO_USER — so the venv stays owned by you. Only the
#   systemd unit and nginx actually need root.
#
#   Intended target is a dedicated production clone, e.g.
#       git clone git@github.com:BitsofJeremy/ruru.git /8TB/bin/ruru
#   NOT a Syncthing-synced development checkout: a macOS .venv landing in a
#   live service directory is exactly what broke the old sprecher service
#   (systemd 203/EXEC on a dangling interpreter symlink).
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
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
[[ -n "$REAL_HOME" ]] || REAL_HOME="$(eval echo "~$REAL_USER")"
REAL_GROUP="$(id -gn "$REAL_USER")"

# ---------------------------------------------------------------------------
# 1. Paths and settings (derived — survive sudo, overridable from the env)
# ---------------------------------------------------------------------------
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$APP_DIR/data"
CACHE_DIR="$WORK_DIR/cache"
LOG_DIR="$WORK_DIR/logs"
PORT="${RURU_PORT:-5151}"
SERVER_NAME="${RURU_SERVER_NAME:-ruru.nexus.home.test}"
BACKEND="${RURU_TTS_BACKEND:-cuda}"
MODEL_SIZE="${RURU_MODEL_SIZE:-large}"
HF_HOME_DIR="${HF_HOME:-$REAL_HOME/.cache/huggingface}"

SERVICE_NAME="ruru-web"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_AVAILABLE="/etc/nginx/sites-available/ruru"
NGINX_ENABLED="/etc/nginx/sites-enabled/ruru"

VENV_PY="$APP_DIR/.venv/bin/python"
VENV_BIN="$APP_DIR/.venv/bin/ruru"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
[[ -n "$UV_BIN" ]] || UV_BIN="$REAL_HOME/.local/bin/uv"

# Run a command AS the real user (preserve PATH + HOME so uv works).
as_user() { sudo -u "$REAL_USER" env "PATH=$PATH" "HOME=$REAL_HOME" "$@"; }

c() { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok() { printf '\033[1;32m%s\033[0m\n' "$*"; }
err() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

c "🚀 ruru Linux install (CUDA)"
echo "   app dir   : $APP_DIR"
echo "   work dir  : $WORK_DIR"
echo "   real user : $REAL_USER (home $REAL_HOME)"
echo "   backend   : $BACKEND   model size: $MODEL_SIZE"
echo "   app port  : $PORT   nginx: http://$SERVER_NAME/"
echo "   running as: $(whoami) (uid $(id -u))"
echo

if [[ $EUID -ne 0 ]]; then
    err "❌ Must be run with sudo (needs root for systemd + nginx)."
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Retire the old sprecher service (ruru is its successor)
# ---------------------------------------------------------------------------
c "🧹 1/7  Retiring the old sprecher service (if present)..."
if systemctl list-unit-files 2>/dev/null | grep -q '^sprecher-web\.service'; then
    systemctl disable --now sprecher-web 2>/dev/null || true
    rm -f /etc/systemd/system/sprecher-web.service
    systemctl daemon-reload
    ok "   removed sprecher-web.service"
else
    echo "   no sprecher-web.service — nothing to do"
fi
for f in /etc/nginx/sites-enabled/sprecher-web /etc/nginx/sites-available/sprecher-web; do
    [[ -e "$f" ]] && rm -f "$f" && ok "   removed $f"
done
echo "   (the sprecher repo files on disk are left untouched)"

# ---------------------------------------------------------------------------
# 3. Pre-flight — a CUDA install with no CUDA is not worth finishing
# ---------------------------------------------------------------------------
c "🔍 2/7  Checking the GPU..."
if [[ "$BACKEND" == "cuda" ]]; then
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        err "❌ nvidia-smi not found but RURU_TTS_BACKEND=cuda."
        echo "   Install the NVIDIA driver, or re-run with RURU_TTS_BACKEND=mlx."
        exit 1
    fi
    nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader \
        | sed 's/^/   GPU: /'
fi

# ---------------------------------------------------------------------------
# 4. Directory structure (owned by the real user)
# ---------------------------------------------------------------------------
c "📂 3/7  Creating cache + log directories..."
mkdir -p "$CACHE_DIR/uploads" "$CACHE_DIR/chunks" "$CACHE_DIR/output" "$CACHE_DIR/voices" "$LOG_DIR"
chown -R "$REAL_USER:$REAL_GROUP" "$WORK_DIR"

# ---------------------------------------------------------------------------
# 5. Python environment (uv sync --extra cuda) — as real user
# ---------------------------------------------------------------------------
c "🐍 4/7  Syncing Python dependencies (uv sync --extra cuda)..."
if [[ -x "$UV_BIN" ]]; then
    as_user "$UV_BIN" sync --project "$APP_DIR" --extra cuda
else
    err "   uv not found ($UV_BIN) — skipping sync. Ensure the venv already exists."
fi
if [[ ! -x "$VENV_BIN" ]]; then
    err "❌ Missing executable: $VENV_BIN"
    echo "   Run as $REAL_USER:  cd \"$APP_DIR\" && uv sync --extra cuda"
    exit 1
fi

# ---------------------------------------------------------------------------
# 6. Database init + migrations (idempotent)
# ---------------------------------------------------------------------------
c "🗄️  5/7  Initialising database..."
as_user env "PYTHONPATH=$APP_DIR" "RURU_WORK_DIR=$WORK_DIR" "$VENV_PY" - <<'PY'
import asyncio
from config import DB_PATH
from models.db import init_db
asyncio.run(init_db(DB_PATH))
print("   init_db OK ->", DB_PATH)
PY

# ---------------------------------------------------------------------------
# 7. systemd unit
# ---------------------------------------------------------------------------
c "⚙️  6/7  Writing systemd unit ($SERVICE_FILE)..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=ruru Audiobook Narration Engine (Qwen3-TTS, CUDA)
After=network.target

[Service]
Type=simple
User=$REAL_USER
Group=$REAL_GROUP
WorkingDirectory=$APP_DIR
Environment="RURU_WORK_DIR=$WORK_DIR"
Environment="RURU_PORT=$PORT"
Environment="RURU_TTS_BACKEND=$BACKEND"
Environment="RURU_MODEL_SIZE=$MODEL_SIZE"
Environment="PYTHONUNBUFFERED=1"
# Reuse the already-downloaded Qwen weights (~11 GB) instead of re-fetching.
Environment="HF_HOME=$HF_HOME_DIR"
ExecStart=$VENV_BIN
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"
ok "   $SERVICE_NAME enabled and started"

# ---------------------------------------------------------------------------
# 8. nginx reverse proxy
# ---------------------------------------------------------------------------
c "🌐 7/7  Configuring nginx..."
cat > "$NGINX_AVAILABLE" <<EOF
server {
    listen 80;
    server_name $SERVER_NAME;

    # Audiobook uploads and downloads are large and slow; don't cap or time out.
    client_max_body_size 0;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    # Serve generated audio and static assets straight off disk.
    location /static/ {
        alias $APP_DIR/static/;
    }
    location /audio/output/ {
        alias $CACHE_DIR/output/;
    }
    location /audio/voices/ {
        alias $CACHE_DIR/voices/;
    }
}
EOF

ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"

if nginx -t; then
    systemctl reload nginx 2>/dev/null || nginx -s reload
    ok "   nginx reloaded"
else
    err "❌ nginx config test failed — leaving nginx running with its old config."
    exit 1
fi

echo
ok "✅ Install complete."
echo
echo "   Web UI     : http://$SERVER_NAME/   (or http://127.0.0.1:$PORT)"
echo "   Status     : systemctl status $SERVICE_NAME"
echo "   Logs       : journalctl -u $SERVICE_NAME -f"
echo "   Manage     : ./reset_service.sh help"
echo
echo "   NOTE: the 1.7B Qwen models need ~3.5 GB of free VRAM each. If another"
echo "         GPU workload (e.g. ComfyUI) is resident, narration jobs will OOM."
