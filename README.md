# Symphony × Thumbtack demo

A self-contained demo showing **Symphony orchestrating Claude Code agents to
build software**, framed for the Thumbtack engineering org.

The product the agents are working on is **promatch** — a small CLI that
simulates a Thumbtack-style pro-lead-matching marketplace. It ships with the
CLI working out of the box; the Linear tickets in this project task the agents
with **building a live web dashboard on top of it**, slice by slice. The
audience watches the dashboard appear in real time as Symphony churns.

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│ Linear (the queue)           │         │ Symphony (the orchestrator)  │
│  • Todo  → In Progress       │ ◄─────► │  picks issues, dispatches    │
│  • Human Review → Merging    │         │  per-issue Claude sessions   │
└──────────────────────────────┘         └──────────────────┬───────────┘
                                                            │
                                                            ▼
                                          ┌──────────────────────────────┐
                                          │ promatch repo (git worktree) │
                                          │  • CLI: ships working        │
                                          │  • Dashboard: agents build   │
                                          │    it, ticket by ticket      │
                                          └──────────────────────────────┘
```

## What's in here

```
symphony-thumbtack/
├── config.env.example     # ★ THE config file. Copy → config.env, fill in.
├── WORKFLOW.md            # Symphony front matter + per-issue prompt.
│                          # Reads $LINEAR_API_KEY, $LINEAR_PROJECT_SLUG,
│                          # $TARGET_REPO, $SYMPHONY_DIR — no hardcoded paths.
├── SETUP.md               # First-time walkthrough (Linear + GitHub + MCP).
├── scripts/
│   ├── bootstrap.sh       # One-shot: validate config, init git, seed Linear.
│   ├── run.sh             # Start Symphony with demo-friendly logs.
│   ├── reset-demo.sh      # Wipe local state between demo runs.
│   ├── tail.sh            # Live tail symphony.log in a second pane.
│   └── seed-linear.py     # Push the demo's 7 issues into Linear (idempotent).
├── pyproject.toml         # Top-level: makes Symphony pip-installable from
│                          # this folder (bootstrap.sh runs `pip install -e .`).
├── symphony/              # Symphony orchestrator source.
├── promatch/              # The target repo Symphony's agents work in.
│   ├── promatch/          #   CLI source (Click + Rich).
│   ├── tests/             #   Pytest tests.
│   └── README.md
├── .claude/skills/        # Playbooks the agent reads (linear/commit/push/…).
└── _workspaces/           # Per-issue git worktrees (auto-created).
```

## Quickstart

```sh
# 1. Configure
cp config.env.example config.env
$EDITOR config.env                    # fill in 4 values

# 2. One-shot bootstrap (also installs the bundled Symphony orchestrator)
scripts/bootstrap.sh                  # installs symphony, validates config,
                                      # inits promatch git, offers to create
                                      # GitHub remote + seed Linear

# 3. Run
scripts/run.sh
```

For the full walkthrough — Linear project creation, custom workflow states,
MCP authorization, GitHub setup — see [SETUP.md](./SETUP.md).

## How config flows

> The single file you ever edit is `config.env`. It's the source of truth.
> Everything downstream reads from it.

```
config.env                          ←  you edit this
   │
   ├─→ LINEAR_API_KEY      ─→  WORKFLOW.md  (tracker.api_key)
   ├─→ LINEAR_PROJECT_SLUG ─→  WORKFLOW.md  (tracker.project_slug)
   ├─→ TARGET_REPO         ─→  WORKFLOW.md  (hooks: after_create, before_remove)
   ├─→ SYMPHONY_DIR        ─→  WORKFLOW.md  (hooks: skill symlink target)
   ├─→ LINEAR_TEAM_KEY     ─→  scripts/seed-linear.py
   └─→ TARGET_REPO         ─→  scripts/reset-demo.sh
```

No path or token is duplicated across files. Change a value once → restart
Symphony → propagated everywhere.

## What you need

- **Linear** workspace + a personal API key + an empty project for this demo.
- **GitHub** account + `gh` CLI authenticated (`gh auth login`).
- **Claude Code** CLI on `$PATH` and the Linear MCP installed
  (`claude mcp add --transport sse --scope user linear https://mcp.linear.app/sse`).
- **Python 3.9+** (Symphony itself is bundled — `bootstrap.sh` installs it).

That's it. promatch itself uses only SQLite — no other API keys.
