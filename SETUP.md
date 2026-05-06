# Setup (offline-demo)

End-to-end first-time setup for the Symphony × Thumbtack demo, **offline
variant**. No Linear, no GitHub, no `gh` CLI, no MCP. Roughly 2 minutes from
a fresh machine to a running orchestration. Once done, the per-demo loop is
just `scripts/reset-demo.sh && scripts/run.sh`.

This is the backup variant of the demo, intended for use when the live
networked variant (Linear + GitHub) can't reach those services.

---

## 0. Prerequisites

Confirm you have these on your `$PATH`. Install anything missing.

```sh
python3 --version          # 3.9+
git --version
claude --version           # https://docs.anthropic.com/claude-code
```

Notably **NOT** required: `gh`, a Linear account, a GitHub account, network
access to either service. Once you've installed the above tools and Claude
Code, you can run the entire demo on an airplane.

---

## 1. Create your config file

```sh
cp config.env.example config.env
```

There is nothing you need to fill in — every value defaults to a path next to
this folder. Open it if you want to override `TARGET_REPO` or
`SYMPHONY_TASKS_FILE`, otherwise leave it alone.

---

## 2. Bootstrap the demo

```sh
scripts/bootstrap.sh
```

What it does:

1. Validates `config.env`.
2. Creates a local virtualenv at `.venv/` and installs the bundled Symphony
   orchestrator (and the `tasks` CLI).
3. Materializes `promatch/` from the committed `promatch.template/`.
4. Initializes `promatch/` as a local git repo on `main`. **No remote** —
   agents push branches here only.
5. Installs `promatch` editable so `promatch serve` resolves.
6. Seeds `tasks.json` with the 13 demo issues (8 in `Todo`, 5 in `Backlog`).

If something fails, fix it and re-run — the script is idempotent.

---

## 3. Run the demo

```sh
scripts/run.sh
```

Within 30 seconds you should see:

```
==> Logs are also being written to symphony.log (full unfiltered copy).
==> Press Ctrl-C to shut down.

ts=… msg=tick candidates=8 running=0 ...
ts=… msg=dispatched issue_identifier=ENG-1 ...
ts=… msg=hook=after_create ...
```

Symphony picks up the eight `Todo` issues and dispatches Claude agents
concurrently (capped at 4 at a time per `agent.max_concurrent_agents`).

In a second terminal pane, watch the issue queue:

```sh
# Status of every issue
tasks list

# Or a single issue, including its workpad
tasks get ENG-1
tasks comment-list ENG-1
```

You can also tail the orchestrator log:

```sh
scripts/tail.sh
```

When an agent finishes a ticket and moves it to `Human Review`, you can:

1. Read the local PR record: `tasks pr-view ENG-3`.
2. Read the workpad: `tasks comment-list ENG-3`.
3. Inspect the branch: `git -C promatch log symphony/eng-3 --oneline -20`.
4. Drag the issue to `Merging`:
   `tasks update-state ENG-3 --state Merging`.
5. Within ~10s, Symphony's next reconcile picks it up. The agent runs the
   `land` skill: squash-merges into local `main`, marks the local PR as
   `MERGED`, and moves the issue to `Done`.

After all 8 stage-1 issues are `Done`, the promatch repo has a working web
dashboard. Run it:

```sh
cd promatch
source ../.venv/bin/activate
git checkout main           # the agents' merges all landed here
promatch serve
# → http://localhost:5050
```

---

## Per-demo reset

To wipe local state between runs:

```sh
scripts/reset-demo.sh                 # default: kills symphony, removes
                                       # worktrees, symphony/* branches,
                                       # _workspaces/, *.log, AND resets
                                       # tasks.json to starting demo state.
scripts/reset-demo.sh --keep-tasks    # don't reset tasks.json (keeps
                                       # comments + PR records around).
scripts/reset-demo.sh --db            # also drops the promatch SQLite DB.
scripts/reset-demo.sh --sessions      # also wipes Claude session transcripts
                                       # from ~/.claude/projects/ for this demo.
scripts/reset-demo.sh --all           # everything.
```

---

## Troubleshooting

**`config.env missing`** — copy `config.env.example` first.

**`tracker.tasks_file is required for kind=json`** — your
`SYMPHONY_TASKS_FILE` is empty. The default in `config.env.example` puts it
next to that file; if you blanked it out, restore it.

**`tasks: no tasks file resolved`** — your shell hasn't sourced `config.env`.
Run `source config.env` in the same shell, or invoke `tasks --file
/path/to/tasks.json …` explicitly. (Symphony itself sources `config.env`
through `run.sh`.)

**Agent runs but tasks.json never updates** — confirm the agent has the
`tasks` CLI on its `$PATH`. The bootstrap installs it via the venv; the
`run.sh` activation propagates `$PATH` into hook subprocesses, which in turn
propagate it to Claude.

**Issue stays in `Human Review` forever** — that's the design. It's waiting
for you to drag it to `Merging` (to land) or `Rework` (to redo) via
`tasks update-state ENG-N --state ...`.

**`hook=after_create exited with non-zero`** — usually `TARGET_REPO` doesn't
exist or isn't a git repo. Re-run `scripts/bootstrap.sh`.

**`fatal: 'main' is not a commit`** in the `after_create` hook — the
`promatch/` repo wasn't initialized. Re-run `scripts/bootstrap.sh` and watch
for "✓ initialized git repo on 'main'" in the output.
