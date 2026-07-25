# ruru Uninstall Guide

## Overview

This guide removes ruru from a macOS system where it was installed with
`install_macos.sh` (a launchd user agent behind a Homebrew nginx reverse proxy).

If your install predates the WeirDing -> ruru rebrand and you never ran the
migration in `install_macos.sh`/`reset_service.sh`, also check for the legacy
`com.jeremy.weirding.web` label and `weirding.conf` nginx fragment — see
[docs/migration.md](migration.md).

## Step 1: Stop and Remove the launchd Agent

```bash
launchctl bootout gui/$(id -u)/com.jeremy.ruru.web 2>/dev/null || \
  launchctl unload "$HOME/Library/LaunchAgents/com.jeremy.ruru.web.plist"
rm -f "$HOME/Library/LaunchAgents/com.jeremy.ruru.web.plist"

# Legacy pre-rebrand agent, if it's still around:
launchctl bootout gui/$(id -u)/com.jeremy.weirding.web 2>/dev/null || \
  launchctl unload "$HOME/Library/LaunchAgents/com.jeremy.weirding.web.plist" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.jeremy.weirding.web.plist"
```

## Step 2: Remove the nginx Site

Homebrew nginx keeps per-site fragments under `$(brew --prefix)/etc/nginx/servers/`:

```bash
rm -f "$(brew --prefix)/etc/nginx/servers/ruru.conf"
rm -f "$(brew --prefix)/etc/nginx/servers/weirding.conf"  # legacy, if present
nginx -s reload 2>/dev/null || true
# Optional — stop nginx entirely if it was only used for ruru:
# brew services stop nginx
```

## Step 3: Remove Working Data (Optional)

This removes the SQLite DB, uploaded files, generated audio, and voice samples.
Skip if you want to keep your data.

```bash
rm -rf <repo>/data
```

## Step 4: Remove the Repository (Optional)

Only if you want to fully remove the application code and its virtualenv.

```bash
rm -rf <repo>
```

## What Was Installed

| Component | Location |
|-----------|----------|
| launchd user agent | `~/Library/LaunchAgents/com.jeremy.ruru.web.plist` |
| nginx site config | `$(brew --prefix)/etc/nginx/servers/ruru.conf` |
| Working data | `<repo>/data/` (DB `ruru.db`, `cache/`, `logs/`) |
| Virtualenv | `<repo>/.venv/` (managed by `uv sync`) |

The app listens on port 5151 directly; nginx proxies port 80 → 5151.
