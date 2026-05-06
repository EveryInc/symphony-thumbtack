# Symphony × Thumbtack demo (OFFLINE / backup variant)

A self-contained demo showing **Symphony orchestrating Claude Code agents to
build software**, framed for the Thumbtack engineering org — with **zero
network dependencies on Linear or GitHub**. This is the backup variant that
exists in case the live demo can't reach those services.

The product the agents are working on is **promatch** — a small CLI that
simulates a Thumbtack-style pro-lead-matching marketplace. It ships with the
CLI working out of the box; the tickets in this project task the agents with
**building a live web dashboard on top of it**, slice by slice. The audience
watches the dashboard appear in real time as Symphony churns.

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│ tasks.json (the queue)       │         │ Symphony (the orchestrator)  │
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
                                          │  • Branches stay LOCAL —     │
                                          │    no remote, no `gh`        │
                                          └──────────────────────────────┘
```

## What's different from the online demo

| Online demo | Offline demo (this) |
|---|---|
| Linear queue, accessed via Linear MCP | Local `tasks.json`, accessed via the bundled `tasks` CLI |
| GitHub `origin` remote, `gh pr create` | Local-only branches; "PR" is a record on the issue in `tasks.json` |
| `land` skill calls `gh pr merge` | `land` skill squash-merges into local `main` |
| Manual Linear OAuth + `gh auth login` | Nothing to authenticate |

Everything else — the orchestrator code, the workflow shape (Todo → In
Progress → Human Review → Merging → Done), the per-issue worktree, the demo
content, the `commit` / `pull` skills — is identical in both branches.

## What's in here

```
symphony-thumbtack/
├── config.env.example     # ★ THE config file. Copy → config.env. Two paths,
│                          #   no secrets to fill in.
├── WORKFLOW.md            # Symphony front matter + per-issue prompt.
│                          # Reads $TARGET_REPO, $SYMPHONY_DIR,
│                          # $SYMPHONY_TASKS_FILE — no hardcoded paths.
├── SETUP.md               # First-time walkthrough.
├── scripts/
│   ├── bootstrap.sh       # One-shot: validate config, init local git, seed
│   │                      #   tasks.json with the 13 demo issues.
│   ├── run.sh             # Start Symphony with demo-friendly logs.
│   ├── reset-demo.sh      # Wipe local state between demo runs.
│   ├── tail.sh            # Live tail symphony.log in a second pane.
│   └── seed-local.py      # Write the demo's 13 issues into tasks.json
│                          #   (idempotent; --force overwrites).
├── pyproject.toml         # Top-level: makes Symphony pip-installable from
│                          # this folder. Also installs the `tasks` CLI.
├── symphony/              # Symphony orchestrator source.
│   ├── tracker.py         #   LinearTracker + JsonTracker
│   ├── tasks_store.py     #   Locked JSON file I/O
│   └── tasks_cli.py       #   `tasks` CLI for agents (Linear MCP stand-in)
├── promatch.template/     # ★ COMMITTED source for the demo target repo.
│                          # bootstrap.sh copies it to ./promatch and `git init`s.
├── promatch/              # Runtime copy created by bootstrap. GIT-IGNORED in
│                          # this outer repo so there's no nested-git foot-gun.
│                          # This is where agents actually push work — locally.
├── tasks.json             # ★ Created by bootstrap. Single source of truth for
│                          #   issues, comments, and local PR records.
│                          # GIT-IGNORED; resets are idempotent.
├── .claude/skills/        # Playbooks the agent reads (tasks/commit/push/…).
│   ├── tasks/             #   Replaces the old `linear` skill.
│   ├── commit/
│   ├── pull/              #   Merges from local `main` (no `origin`).
│   ├── push/              #   Local-only; creates PR record in tasks.json.
│   └── land/              #   Squash-merges into local `main`.
└── _workspaces/           # Per-issue git worktrees (auto-created).
```

## Quickstart

```sh
# 1. Configure (nothing required — defaults work)
cp config.env.example config.env

# 2. One-shot bootstrap
scripts/bootstrap.sh                  # installs symphony + tasks CLI,
                                      # inits promatch git, seeds tasks.json.

# 3. Run
scripts/run.sh
```

For the full walkthrough see [SETUP.md](./SETUP.md).

## How config flows

> The single file you ever edit is `config.env`. It's the source of truth.
> Everything downstream reads from it.

```
config.env                          ←  you edit this (or just keep defaults)
   │
   ├─→ TARGET_REPO          ─→  WORKFLOW.md  (hooks: after_create, before_remove)
   │                            scripts/reset-demo.sh
   ├─→ SYMPHONY_DIR         ─→  WORKFLOW.md  (hooks: skill symlink target)
   └─→ SYMPHONY_TASKS_FILE  ─→  WORKFLOW.md  (tracker.tasks_file)
                                tasks CLI (default file)
                                scripts/seed-local.py
```

No path is duplicated across files. Change a value once → restart Symphony →
propagated everywhere.

## What you need

- **Claude Code** CLI on `$PATH`.
- **Python 3.9+** (Symphony itself is bundled — `bootstrap.sh` installs it).
- **git**.

That's it. promatch itself uses only SQLite. There is no Linear, no GitHub,
no `gh`, and no MCP server — anything an agent does to the issue tracker
goes through `tasks` (a local Python CLI that owns `tasks.json`).

## Demo flow

When an agent finishes a ticket, the issue moves to `Human Review`. To
approve it:

1. Read the local PR record: `tasks pr-view ENG-3` (or whatever id).
2. Read the comments / workpad: `tasks comment-list ENG-3`.
3. Inspect the branch: `git -C promatch log symphony/eng-3 --oneline`.
4. Drag the issue to `Merging`: `tasks update-state ENG-3 --state Merging`.
5. Within the next poll cycle, Symphony dispatches the agent again. The agent
   runs the `land` skill, squash-merges into local `main`, and moves the
   issue to `Done`.

There's no GitHub UI, but every artifact (branch + PR record + workpad +
comments) is on disk and inspectable.
