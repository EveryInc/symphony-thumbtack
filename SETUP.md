# Setup

End-to-end first-time setup for the Symphony × Thumbtack demo. Roughly 15
minutes from a fresh machine to a running orchestration. Once done, the
per-demo loop is just `scripts/reset-demo.sh && scripts/run.sh`.

---

## 0. Prerequisites

Confirm you have these on your `$PATH`. Install anything missing.

```sh
python3 --version          # 3.9+
git --version
gh --version               # brew install gh
claude --version           # https://docs.anthropic.com/claude-code
```

Authenticate `gh`:

```sh
gh auth login              # GitHub.com → HTTPS → login with browser
gh auth status             # confirm ✓
```

Install Symphony (from its source repo, one time):

```sh
cd /path/to/symphony
pip install -e .
which symphony             # confirm it's on PATH
```

---

## 1. Get a Linear API key

1. Open Linear → click your avatar (top left) → **Settings**.
2. Go to **Account → Security & access → API**.
3. Click **Create new API key**, name it "symphony-demo", copy the value
   (starts with `lin_api_…`).
4. Save it somewhere — you'll paste it into `config.env` in step 4.

> The key inherits **your** permissions. Symphony will read/write the project
> as you. That's fine for a demo.

---

## 2. Create the demo Linear project

1. In your Linear workspace, pick the team you want to demo against (or
   create a fresh one called e.g. "ENG" or "DEMO"). Note the team's **key**
   (the 2-4 letter prefix on issue IDs, e.g. `ENG-1`).
2. **Projects** → **+ New project**.
3. Name it something obvious — `Promatch Demo` works.
4. Description (optional but useful): "Symphony orchestrates Claude agents
   building a live dashboard on top of the promatch CLI."
5. Once the project exists, open it. Look at the URL:

   ```
   https://linear.app/your-team/project/promatch-demo-1a2b3c4d5e6f
                                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                       this whole tail is the slugId
   ```

   Copy that trailing slug — you'll paste it into `config.env`.

---

## 3. Add the custom workflow states

Symphony's WORKFLOW.md expects a few states beyond Linear's defaults. Add
the missing ones once per team.

In Linear: **Team Settings → Workflow** for the team that owns your project.

| State | Type | Purpose |
|---|---|---|
| `Backlog` | Backlog | Default. Out-of-scope work; not picked up by Symphony. |
| `Todo` | Unstarted | Default. **Symphony picks these up.** |
| `In Progress` | Started | Default. The agent is actively working. |
| `Human Review` | Started | **Add this.** PR validated, waiting on a human. |
| `Merging` | Started | **Add this.** Approved by human; agent runs `land`. |
| `Rework` | Started | **Add this.** Reviewer wants changes; full reset. |
| `Done` | Completed | Default. Terminal. |

Click "+ Add state" under the **Started** group for each missing one. Match
the names exactly — `WORKFLOW.md` matches by name string.

---

## 4. Configure this demo

```sh
cd /Users/michaeltaylor/Every-clients/thumbtack/symphony-thumbtack
cp config.env.example config.env
$EDITOR config.env
```

Fill in the four values:

```sh
LINEAR_API_KEY=lin_api_...               # from step 1
LINEAR_PROJECT_SLUG=promatch-demo-1a2b3c # from step 2 (URL tail)
LINEAR_TEAM_KEY=ENG                      # from step 2 (team key)
TARGET_REPO=...                          # leave default unless you moved promatch
```

Save. **Don't commit `config.env`** — it has your secret. It's already in
`.gitignore`.

---

## 5. Install the Linear MCP for Claude

Claude Code talks to Linear via MCP. Install it once at user scope so every
Claude session — including the ones Symphony spawns — picks it up.

```sh
claude mcp add --transport sse --scope user linear https://mcp.linear.app/sse
```

The first time Claude runs, a browser window opens for Linear OAuth. Authorize
it. The token persists in your Claude config.

To prime the OAuth flow now (so the demo doesn't pause on the first dispatch):

```sh
claude
# Inside Claude:  ask it "list my Linear teams" — it'll trigger the OAuth window
# Authorize, then exit Claude.
```

Verify:

```sh
claude mcp list | grep linear
# linear  https://mcp.linear.app/sse  (sse)
```

---

## 6. Bootstrap the demo

Run the all-in-one bootstrap. It:

- Validates `config.env`
- Inits `promatch/` as a git repo on `main` if needed
- Offers to create a GitHub remote via `gh repo create`
- Offers to seed your Linear project with the 7 demo issues

```sh
scripts/bootstrap.sh
```

When prompted:

- **"Create a private GitHub repo via gh now?"** → say `y`. Pick a repo
  name (default: `promatch`). It'll create it under your `gh` account and
  push `main`. The agents will push their feature branches here too.
- **"Seed Linear with the dashboard-buildout demo issues?"** → say `y`.
  Seven issues land in the project's `Todo` column.

If something fails, fix it and re-run — the script is idempotent.

---

## 7. Run the demo

```sh
scripts/run.sh
```

Within 30 seconds you should see:

```
==> Logs are also being written to symphony.log (full unfiltered copy).
==> Press Ctrl-C to shut down.

ts=… msg=tick candidates=7 running=0 ...
ts=… msg=dispatched issue_identifier=ENG-1 ...
ts=… msg=hook=after_create ...
```

Symphony picks up the seven `Todo` issues and dispatches Claude agents
concurrently (capped at 4 at a time per `agent.max_concurrent_agents`).
Watch your Linear project: each issue moves `Todo → In Progress`, gets a
`## Workpad` comment, then later moves to `Human Review` once the agent
opens a PR.

In a second terminal pane, tail the log:

```sh
scripts/tail.sh
```

In your browser, keep these tabs open during the demo:

- **Linear project** — live state changes
- **GitHub PRs** — `https://github.com/<you>/promatch/pulls`

When an agent finishes a ticket and moves it to `Human Review`, you can:

1. Open the PR, review it, click **Approve** in GitHub.
2. Drag the Linear issue from `Human Review` → `Merging`.
3. Within 30s, Symphony's next reconcile picks it up. The agent runs the
   `land` skill: watches CI, addresses any final review comments, squashes,
   merges. The issue moves to `Done`.

After all 7 issues complete, the promatch repo has a working web dashboard.
Run it:

```sh
cd promatch
source .venv/bin/activate          # if you created one earlier
git pull
promatch serve
# → http://localhost:5050
```

---

## Per-demo reset

To wipe local state between runs without touching Linear or GitHub:

```sh
scripts/reset-demo.sh              # default: kills symphony, removes worktrees,
                                   # symphony/* branches, _workspaces/, *.log
scripts/reset-demo.sh --db         # also drops the promatch SQLite DB
scripts/reset-demo.sh --sessions   # also wipes Claude session transcripts
                                   # from ~/.claude/projects/ for this demo
scripts/reset-demo.sh --all        # everything above
```

Then in Linear, drag the issues you want for the next demo back to `Todo`,
or close any open PRs from the previous run.

---

## Troubleshooting

**`config.env missing`** — copy `config.env.example` first.

**`tracker.api_key is required`** — your `LINEAR_API_KEY` is empty or has the
placeholder. Re-source: `source config.env && echo $LINEAR_API_KEY`.

**`No team with key 'ENG'`** when running seed-linear — the team key in
`config.env` doesn't exist in Linear. It's the 2-4 letter prefix on issue
IDs, not the team name. Check **Team Settings → General**.

**`No Linear project matching slug`** — copy the *full* trailing slug from
the project URL after `/project/`. It looks like
`promatch-demo-1a2b3c4d5e6f` (the `-1a2b3c…` suffix is required).

**Agent runs but Linear never updates** — Linear MCP is missing or not
authorized. Run `claude mcp list`. If it's listed, run `claude` once
interactively and trigger the OAuth flow.

**`gh repo create` fails with "already exists"** — fine. Add the remote by
hand: `git -C promatch remote add origin <url> && git -C promatch push -u origin main`.

**Issue stays in `Human Review` forever** — that's the design. It's waiting
for you to drag it to `Merging` (to land) or `Rework` (to redo).

**`hook=after_create exited with non-zero`** — usually `TARGET_REPO` doesn't
exist or isn't a git repo. Re-run `scripts/bootstrap.sh`.
