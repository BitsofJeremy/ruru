#!/bin/bash
set -e

# WeirDing idempotent install script
# Usage: sudo ./install.sh

WORK_DIR="/8TB/bin/weirding"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== WeirDing Install ==="

# Create working directory
mkdir -p "$WORK_DIR/data/uploads"
mkdir -p "$WORK_DIR/data/chunks"
mkdir -p "$WORK_DIR/data/output"
mkdir -p "$WORK_DIR/data/voices"

# Symlink static files if not present
if [ ! -e "$WORK_DIR/static" ]; then
    ln -s "$REPO_DIR/static" "$WORK_DIR/static"
fi

# Create systemd service (idempotent - skip if exists)
SERVICE_FILE="/etc/systemd/system/weirding-web.service"
if [ ! -f "$SERVICE_FILE" ]; then
    cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=WeirDing Audiobook Narration Engine
After=network.target

[Service]
Type=simple
User=jeremy
WorkingDirectory=/8TB/bin/weirding
Environment="WEIRDING_WORK_DIR=/8TB/bin/weirding"
ExecStart=/8TB/bin/weirding/.venv/bin/weirding
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    echo "Created $SERVICE_FILE"
else
    echo "Service file exists, skipping"
fi

# Create nginx config (idempotent - skip if exists)
NGINX_CONFIG="/etc/nginx/sites-available/weirding"
if [ ! -f "$NGINX_CONFIG" ]; then
    cat > "$NGINX_CONFIG" << 'EOF'
server {
    listen 80;
    server_name weirding.nexus.home.test;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:8400;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }

    location /audio/ {
        alias /8TB/bin/weirding/data/output/;
    }
}
EOF
    echo "Created $NGINX_CONFIG"
else
    echo "Nginx config exists, skipping"
fi

# Enable nginx site if not linked
if [ ! -e /etc/nginx/sites-enabled/weirding ]; then
    ln -s "$NGINX_CONFIG" /etc/nginx/sites-enabled/weirding
    nginx -s reload
fi

echo "=== Install complete ==="
echo "Run: sudo systemctl start weirding-web"
echo "Run: sudo systemctl enable weirding-web"