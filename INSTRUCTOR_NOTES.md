# Instructor notes — running the Symphony × Thumbtack demo

Brief cheat sheet for running the live session. Two demo variants:

1. **Live demo** (Linear + GitHub) — branch `linear-demo`. Demo this first.
2. **Offline demo** (local JSON + local git) — branch `main`. The team will
   clone and run this themselves; demo it second.

`scripts/clean.sh` wipes all runtime state so switching between branches is
safe.

---

## Pre-flight (do this 30 minutes before)

```sh
# Verify both branches bootstrap cleanly. Pick a branch, clean, bootstrap.
git checkout linear-demo
scripts/clean.sh --yes
scripts/bootstrap.sh --no-prompt          # confirms Linear seeding works

git checkout main
scripts/clean.sh --yes
scripts/bootstrap.sh --no-prompt          # confirms tasks.json seeding works
```

If both produce no errors, you're ready. End on the branch you'll demo
first (`linear-demo`).

You should also have **4 terminal windows or panes tiled side-by-side**
before the audience joins. See "Window layout" below.

---

## Window layout (both demos use the same tiles)

```
┌────────────────────────────┬────────────────────────────┐
│  1. orchestrator log       │  2. queue (tasks list /    │
│     scripts/run.sh         │     Linear board on web)   │
├────────────────────────────┼────────────────────────────┤
│  3. agent activity         │  4. browser                │
│     scripts/watch-agents.sh│     (later: dashboard)     │
└────────────────────────────┴────────────────────────────┘
```

---

## Demo 1 — Live demo (linear-demo branch, ~15 min)

Branch: `linear-demo`. Setup already done in pre-flight.

### Open three terminals

In each:

```sh
cd /Users/michaeltaylor/Every-clients/thumbtack/symphony-thumbtack
source .venv/bin/activate
source config.env
```

### Pane 1 — start the orchestrator

```sh
scripts/run.sh
```

### Pane 2 — open the Linear project page in a browser

This **is** your queue view for this demo. Use the `Promatch Demo` project
page on linear.app.

### Pane 3 — agent activity

```sh
scripts/watch-agents.sh
```

### Talking-track beats

- **Frame** (15s before starting): "Symphony orchestrates Claude Code agents
  against a real codebase. The queue is Linear, the code lives on GitHub.
  We'll watch agents pick up tickets, plan, code, test, and open PRs in
  parallel."
- (Run `scripts/run.sh`.) **Within ~30s**: "Two agents just got dispatched.
  Each one has its own git worktree, its own Claude session, its own
  Linear ticket."
- **When watch-agents shows tool calls**: "Each agent maintains a
  workpad — plan, acceptance criteria, validation strategy. Same artifact
  a human engineer would write. Click the workpad comment in Linear."
- **When a ticket flips to Human Review** (~5-7 min): "Now the human is
  back in the loop. Open the GitHub PR. Read the description. Read the
  diff." Click Approve in GitHub, then drag the Linear ticket to
  `Merging`.
- "Within 10 seconds Symphony re-dispatches the same ticket. The agent
  runs the `land` skill — watches CI, addresses any final review
  comments, squash-merges, and moves the ticket to `Done`."
- (Once the dashboard ticket lands.) Open the deployed dashboard or run
  `cd promatch && git pull && promatch serve`. **"That UI didn't exist 15
  minutes ago."**

### Reset between runs (linear branch)

`scripts/reset-demo.sh` wipes local state but Linear and GitHub state are
**not** automatically reset. Reset Linear by hand (drag tickets back to
Todo). For a true fresh demo, close the GitHub PRs from the prior run too.

---

## Switching from live → offline

```sh
# 1. Stop symphony if running (Ctrl-C in pane 1).
# 2. Wipe all runtime state.
scripts/clean.sh --yes
# 3. Switch branches.
git checkout main
# 4. Bootstrap on the offline branch.
scripts/bootstrap.sh --no-prompt
```

Don't skip step 2. The two branches use different `promatch/` git layouts
(live has a GitHub `origin`, offline has none) and different task stores.
Cross-contamination causes confusing failures.

---

## Demo 2 — Offline demo (main branch, ~15 min)

Branch: `main`. Same shape as the live demo, just without Linear or
GitHub. Highlight: **the audience will be running this themselves on their
own laptops afterwards.**

### Open three terminals

In each:

```sh
cd /Users/michaeltaylor/Every-clients/thumbtack/symphony-thumbtack
source .venv/bin/activate
source config.env
```

### Pane 1 — start the orchestrator

```sh
scripts/run.sh
```

### Pane 2 — the queue (replaces the Linear board)

```sh
while true; do clear; tasks list; sleep 2; done
```

### Pane 3 — agent activity

```sh
scripts/watch-agents.sh
```

### Talking-track beats (offline-specific)

- **Frame**: "Same demo, zero network dependencies. No Linear, no GitHub,
  no `gh` CLI. The queue is a JSON file. The 'PR' is a record on the issue
  in that JSON file. The 'merge' is a local squash into local `main`. You
  could run this on an airplane."
- (Run `scripts/run.sh`.) **Within ~30s** the same dispatch pattern fires.
- **When a ticket flips to Human Review** (~5-7 min in pane 2):
  ```sh
  tasks pr-view ENG-1            # the local PR record
  tasks comment-list ENG-1       # the workpad
  git -C promatch diff main..symphony/ENG-1   # the actual diff
  ```
  "Same artifacts as the live demo. Just on disk instead of on
  Linear/GitHub."
- **Approve it live**:
  ```sh
  tasks update-state ENG-1 --state Merging
  ```
  Within ~10s the agent runs the `land` skill, squash-merges into local
  `main`, marks the local PR `MERGED`, and moves the issue to `Done`.
- **Show the deliverable** (when ENG-1 is Done):
  ```sh
  cd promatch && git checkout main && promatch serve
  # → http://localhost:5050
  ```
  "That dashboard didn't exist 5 minutes ago. Built from a 200-word
  ticket, by an agent, locally, no network."
- **Hand off to the audience**: "Clone this repo, run `scripts/bootstrap.sh`,
  run `scripts/run.sh`. That's the entire setup."

### Reset between runs (offline branch)

```sh
scripts/reset-demo.sh           # wipes _workspaces, branches, log,
                                # AND re-seeds tasks.json from scratch.
scripts/reset-demo.sh --keep-tasks   # keep existing comments + PR records.
scripts/reset-demo.sh --all          # also drop SQLite DB + Claude transcripts.
```

`reset-demo.sh` does NOT wipe `promatch/main` — any merges from the
previous run persist there. To go all the way back to the pristine
"CLI-only, no dashboard yet" state:

```sh
scripts/clean.sh --yes
scripts/bootstrap.sh --no-prompt
```

---

## Common mishaps and recoveries

| Symptom | Fix |
|---|---|
| `tasks: command not found` | `source .venv/bin/activate` |
| `tracker.tasks_file is required` | `source config.env` (offline branch only) |
| Agent stuck in `In Progress` for 10+ min | check `scripts/watch-agents.sh` — likely `red` (>5m). Cancel via Ctrl-C in pane 1 and `scripts/reset-demo.sh`. |
| `hook=after_create` failed | `TARGET_REPO` doesn't exist or isn't a git repo. `scripts/clean.sh && scripts/bootstrap.sh`. |
| Wrong branch's leftovers visible | `scripts/clean.sh && scripts/bootstrap.sh` on the branch you want. |

---

## Cheat-sheet commands

```sh
# State of the queue
tasks list                                     # offline
# (or use the Linear web UI on the live branch)

# Promote a Backlog issue to Todo (offline)
tasks update-state ENG-9 --state Todo

# Approve a Human Review ticket (offline)
tasks update-state ENG-3 --state Merging

# Reject and reset a ticket
tasks update-state ENG-3 --state Rework

# See what an agent has actually been doing
tasks comment-list ENG-3                       # workpad
tasks pr-view ENG-3                            # local PR
git -C promatch log symphony/ENG-3 --oneline   # commits
git -C promatch diff main..symphony/ENG-3     # the diff

# Full reset between demo runs
scripts/clean.sh --yes && scripts/bootstrap.sh --no-prompt
```
