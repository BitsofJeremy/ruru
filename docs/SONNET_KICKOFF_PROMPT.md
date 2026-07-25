# Next-session kickoff prompt for Sonnet

Paste the block below as the first message in the next session.

---

You are picking up the WeirDing → **ruru** rebrand and public-release work. The full execution plan is in `TODO.md` at the repo root — **read it first and follow it exactly**. It has three sequential tasks; do them in order: (1) Public GitHub cleanup, (2) Rebrand to ruru, (3) Nvidia feasibility doc.

Key decisions are already locked (do not re-ask):
- **Full rebrand** including internal identifiers (`WEIRDING_`→`RURU_` env prefix, `weirding.db`, console script, `com.jeremy.weirding.web` launchd label, nginx conf) **with migration steps** so the running service + existing SQLite job history survive.
- **Keep `voice_clones/lilith.mp3` + `lilith.txt`** — this is my default voice and ships with the public repo. Remove everything else in `voice_clones/` (the Neil/Ray narrator clones) and the Ephergent character assets. Refactor `models/seed_voices.py` to seed only lilith and tolerate the Ephergent files being gone.
- **You are authorized to stop/restart services** (`launchctl bootout`, kill stray processes, restart under the new label) as needed for the rebrand + migration. Check nothing unrelated is mid-run first.
- **Nvidia task = written feasibility doc only**, no code changes.

Working method:
- Use **TDD** (`superpowers:test-driven-development`) wherever you touch code — failing test first.
- Use the **impeccable** skill for the visual/palette pass, deriving colors from `icon.png` at repo root.
- Do the risky internal-identifier renames one group at a time, running `uv run pytest` after each so a break is easy to isolate.
- Before rewriting git history (Task 1, step 5), confirm the remote state with me and which approach (filter-repo rewrite vs. fresh orphan history) I want.

Start with Task 1. When each task's acceptance/verification block passes, tell me before moving to the next.
