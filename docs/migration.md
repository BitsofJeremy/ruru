# Migrating an existing WeirDing install to ruru

This app was renamed from **WeirDing** to **ruru**. The rename touches
internal identifiers, not just the display name, so an in-place upgrade needs
a few explicit migration steps. `install_macos.sh` and `reset_service.sh`
already automate all of this — this doc exists so a human (or a future
Claude session) can do it by hand if needed, and so the reasoning is
recorded somewhere other than shell-script comments.

If you're running the automated installer, you can stop reading and just run:

```bash
sudo bash install_macos.sh
```

It is idempotent and safe to re-run against an existing WeirDing install.

## What changed, and why each needs a migration step

| Identifier | Old (WeirDing) | New (ruru) | Migration required? |
|---|---|---|---|
| Env var prefix | `WEIRDING_*` | `RURU_*` | Dual-read fallback in code (no action needed) |
| Console script | `weirding` | `ruru` | `uv sync` reinstalls the entry point |
| launchd label | `com.jeremy.weirding.web` | `com.jeremy.ruru.web` | **Yes — unload old before loading new** |
| SQLite DB file | `weirding.db` | `ruru.db` | **Yes — rename file or lose job history** |
| Log files | `weirding.log` / `.err` | `ruru.log` / `.err` | No — new files just start fresh |
| nginx site config | `weirding.conf` | `ruru.conf` | Yes — remove old fragment to avoid duplicate `server_name localhost` blocks |

### 1. Env var prefix (`WEIRDING_` → `RURU_`)

No forced action: `config.py`'s `_env()` helper (and the mirrored copy in
`jobs/tasks.py`) reads `RURU_X` first and falls back to the deprecated
`WEIRDING_X` with a logged deprecation warning if only the old one is set.
`RURU_X` always wins if both are set. This fallback is temporary — plan to
update your `.env`/launchd plist env block to the `RURU_` prefix and drop the
old one.

### 2. Console script (`weirding` → `ruru`)

`pyproject.toml`'s `[project.scripts]` entry changed name. Just re-run
`uv sync` — it removes the old `.venv/bin/weirding` entry point and installs
`.venv/bin/ruru`. Any script/cron/plist invoking `weirding` directly must be
updated to call `ruru` instead.

### 3. launchd label (`com.jeremy.weirding.web` → `com.jeremy.ruru.web`)

**This step is mandatory, not cosmetic.** If the old agent is left running
under its old label while a new one is loaded under the new label, both will
try to bind port 5151 and the new agent will fail to start (this is the
"Port conflict" pitfall documented in `CLAUDE.md`).

Manual steps (automated by `install_macos.sh`):

```bash
launchctl bootout gui/$(id -u)/com.jeremy.weirding.web 2>/dev/null || \
  launchctl unload "$HOME/Library/LaunchAgents/com.jeremy.weirding.web.plist"
rm -f "$HOME/Library/LaunchAgents/com.jeremy.weirding.web.plist"

# ... then install/load the new com.jeremy.ruru.web.plist as usual.
```

### 4. SQLite DB filename (`weirding.db` → `ruru.db`)

**Mandatory to preserve job/voice history.** A plain rename in code with no
data migration would silently orphan existing history: the app would look
for `ruru.db`, not find it, and `init_db()` would create a fresh empty one
next to the old `weirding.db`.

This is handled automatically: `config.migrate_legacy_db_if_needed()` runs at
the start of `app.py`'s lifespan handler (and again, redundantly, in
`install_macos.sh`'s DB-init step) — if `ruru.db` doesn't exist yet but
`weirding.db` does, it renames the legacy file in place and logs what
happened. It never touches `weirding.db` if `ruru.db` already exists, and
never deletes anything.

To do it by hand instead:

```bash
cd <repo>/data
[ -f ruru.db ] || mv weirding.db ruru.db
```

### 5. Log filenames

`weirding.log`/`weirding.err` become `ruru.log`/`ruru.err`. No migration
needed — the new launchd plist just points `StandardOutPath`/
`StandardErrorPath` at the new filenames, and logging starts fresh. Old log
files are left in `data/logs/` untouched; delete them manually if you want.

### 6. nginx site config (`weirding.conf` → `ruru.conf`)

`install_macos.sh` writes the new `ruru.conf` and explicitly deletes the old
`weirding.conf` fragment (if present) before reloading nginx, so you don't
end up with two `server { listen 80; server_name localhost; }` blocks
competing for the same request. If migrating by hand:

```bash
rm -f "$(brew --prefix)/etc/nginx/servers/weirding.conf"
nginx -s reload
```

## Verifying the migration worked

```bash
launchctl list | grep com.jeremy.ruru.web   # new agent running
launchctl list | grep com.jeremy.weirding   # should return nothing
ls <repo>/data/ruru.db                      # DB migrated, not recreated empty
curl -s http://localhost:5151/health        # {"status": "ok", ...}
# Then check the dashboard in a browser and confirm prior job history is visible.
```
