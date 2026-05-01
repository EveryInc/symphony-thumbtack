---
# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW.md — Symphony × Thumbtack demo
#
# Two halves:
#   1) FRONT MATTER (below) — runtime config. Reads $LINEAR_API_KEY,
#      $LINEAR_PROJECT_SLUG, $TARGET_REPO, $SYMPHONY_DIR from your environment.
#      Edit ../config.env, never inline these values here.
#   2) BODY (after the `---`) — the per-issue prompt the agent sees.
# ─────────────────────────────────────────────────────────────────────────────

tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: $LINEAR_PROJECT_SLUG
  active_states:
    - Todo
    - In Progress
    - Human Review
    - Merging
    - Rework
  terminal_states:
    - Done
    - Closed
    - Cancelled
    - Canceled
    - Duplicate

polling:
  interval_ms: 10000

workspace:
  root: ./_workspaces

hooks:
  timeout_ms: 120000
  # All hooks read $TARGET_REPO and $SYMPHONY_DIR from the environment that
  # Symphony was started in (run.sh sources config.env before launching).
  # That is the ONLY place those paths are defined.
  after_create: |
    set -euo pipefail
    : "${TARGET_REPO:?TARGET_REPO not set — source config.env}"
    : "${SYMPHONY_DIR:?SYMPHONY_DIR not set — source config.env}"
    BRANCH="symphony/$(basename "$PWD")"
    LOCKDIR="${TMPDIR:-/tmp}/$(basename "$TARGET_REPO")-worktree.lock.d"

    # Serialize concurrent `git worktree add` calls. macOS ships no `flock` so
    # mkdir is the atomic primitive (POSIX-portable).
    if [ -d "$LOCKDIR" ] && [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +5 2>/dev/null)" ]; then
      rmdir "$LOCKDIR" 2>/dev/null || true
    fi
    LOCK_START=$SECONDS
    while ! mkdir "$LOCKDIR" 2>/dev/null; do
      if [ $((SECONDS - LOCK_START)) -ge 60 ]; then
        echo "timeout acquiring $LOCKDIR" >&2
        exit 1
      fi
      sleep 0.2
    done
    trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

    git -C "$TARGET_REPO" worktree prune

    if [ ! -d .git ]; then
      git -C "$TARGET_REPO" fetch --quiet origin || true
      BASE="main"
      git -C "$TARGET_REPO" rev-parse --verify --quiet origin/main >/dev/null && BASE="origin/main"
      git -C "$TARGET_REPO" worktree add -B "$BRANCH" "$PWD" "$BASE"
    fi

    # rerere helps the merge skill remember conflict resolutions.
    git config rerere.enabled true
    git config rerere.autoupdate true

    # Symlink the demo's `.claude/` (skills, etc.) into the worktree so the
    # agent finds playbooks at `.claude/skills/<name>/SKILL.md`. Lives only in
    # the worktree, gitignored locally so it can't leak into the branch.
    if [ -d "$SYMPHONY_DIR/.claude" ]; then
      ln -snf "$SYMPHONY_DIR/.claude" "$PWD/.claude"
      EXCLUDE_FILE="$(git rev-parse --git-path info/exclude)"
      grep -qxF '.claude' "$EXCLUDE_FILE" 2>/dev/null || echo '.claude' >> "$EXCLUDE_FILE"
    fi
  before_run: |
    set -euo pipefail
    git status --short || true
  after_run: |
    set -euo pipefail
    git status --short || true
  before_remove: |
    set -euo pipefail
    : "${TARGET_REPO:?TARGET_REPO not set — source config.env}"
    LOCKDIR="${TMPDIR:-/tmp}/$(basename "$TARGET_REPO")-worktree.lock.d"
    if [ -d "$LOCKDIR" ] && [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +5 2>/dev/null)" ]; then
      rmdir "$LOCKDIR" 2>/dev/null || true
    fi
    LOCK_START=$SECONDS
    while ! mkdir "$LOCKDIR" 2>/dev/null; do
      if [ $((SECONDS - LOCK_START)) -ge 60 ]; then
        exit 0
      fi
      sleep 0.2
    done
    trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
    git -C "$TARGET_REPO" worktree remove --force "$PWD" 2>/dev/null || true
    git -C "$TARGET_REPO" worktree prune

agent:
  kind: claude
  max_concurrent_agents: 4
  max_turns: 20
  max_retry_backoff_ms: 300000

claude:
  command: claude
  permission_mode: bypassPermissions
  turn_timeout_ms: 3600000
  stall_timeout_ms: 600000
---

You are working on a Linear ticket `{{ issue.identifier }}` end-to-end as part of an
unattended Symphony orchestration run against the **promatch** repo — a local
Thumbtack-style pro-lead-matching marketplace.

{% if attempt -%}
Continuation context:

- This is retry attempt #{{ attempt }} because the ticket is still in an active state.
- Resume from the current workspace state instead of restarting from scratch.
- Do not repeat already-completed investigation or validation unless needed for
  new code changes.
- Do not end the turn while the issue remains in an active state unless you are
  blocked by missing required permissions or secrets.
{%- endif %}

Issue context:

- **Identifier:** {{ issue.identifier }}
- **Title:** {{ issue.title }}
- **Status:** {{ issue.state }}
- **URL:** {{ issue.url }}
{% if issue.labels -%}
- **Labels:** {{ issue.labels | join(", ") }}
{%- endif %}

Description:

{% if issue.description -%}
{{ issue.description }}
{%- else -%}
_No description provided._
{%- endif %}

## What this repo is

`promatch` is a Python CLI + (eventually) web dashboard that simulates a
Thumbtack-style marketplace. Customers post job requests; mock pros respond
with quotes; the customer accepts one. Backed by SQLite. No external APIs.

The starting state ships with the CLI working. The Linear tickets in this
project build up the **web dashboard** on top of it — agents like you, working
incrementally.

## Operating principles

1. This is an unattended orchestration session. **Never** ask a human to perform
   follow-up actions inline — the human is not at the console.
2. Stop early ONLY for a true blocker (missing required auth, missing required
   secrets, ambiguous product intent that crosses a user-visible contract).
   If blocked, record it in the workpad and move the issue per the workflow.
3. Your final message must report only **completed actions** and **blockers**.
   Do not include "next steps for user".
4. Work only inside the per-issue workspace directory you are running in. Do
   not touch any other path on disk.

## Prerequisites

You should have access to the following tools. If any are missing, stop with a
clear blocker explanation in the workpad.

- **Linear MCP** — for reading the issue, posting/editing comments, transitioning
  issue state, attaching the PR, and creating follow-up issues.
- **`gh` CLI** — for pushing branches, creating PRs, watching CI, addressing
  review feedback, and merging.
- **`git`** — already configured. The working tree is a worktree on branch
  `symphony/{{ issue.identifier }}` based off `origin/main`.

## Available skills

These are step-by-step playbooks at `.claude/skills/<name>/SKILL.md`. Open and
follow them when their flow is needed.

- `linear` — Linear MCP usage, workpad protocol, state transitions, follow-up
  issues.
- `commit` — clean conventional commits.
- `push` — push branch + create/update PR with proper title/body and `symphony`
  label.
- `pull` — merge `origin/main` into the branch and resolve conflicts.
- `land` — when a human moves the issue to `Merging`: resolve conflicts, watch
  CI, address review feedback, squash-merge.

## Workflow state machine

Your routing for this turn depends on the **current** Linear state of the issue:

| State | Action |
|---|---|
| `Backlog` | Out of scope. Stop and surface a blocker in the workpad. |
| `Todo` | Transition to `In Progress`, ensure workpad exists, then start the execution flow. If a PR is already attached, run the PR feedback sweep first. |
| `In Progress` | Continue execution from the existing workpad. |
| `Human Review` | Do not change code or issue. Poll PR review comments. If reviewer requests changes, transition to `Rework`. If approved (state moved to `Merging` by human), run the `land` skill. |
| `Merging` | Open `.claude/skills/land/SKILL.md` and run the land flow until merged, then move issue to `Done`. |
| `Rework` | Full reset: close existing PR, remove the workpad comment, create a fresh branch from `origin/main`, restart from kickoff. |
| `Done` | Terminal. Stop immediately and emit a one-line completion confirmation. |

## Step 0 — Kickoff and routing

1. Use the Linear MCP to fetch the current issue by identifier
   `{{ issue.identifier }}` and confirm its current state. Trust your fresh
   read, not the value above (it can drift between dispatch and now).
2. Branch on the state per the table above.
3. If routed to `Todo`, do these in this exact order before any code work:
   1. Move state: `Todo → In Progress` via Linear MCP.
   2. Find or create the workpad comment (header: `## Workpad`). Reuse if
      present.
   3. Stamp the workpad with environment info and a fresh hierarchical plan.
4. If a PR for this branch already exists and is `CLOSED` or `MERGED`, treat
   prior work as non-reusable: create a fresh branch from `origin/main` and
   restart.

## Step 1 — Plan and validate before coding

In the workpad, write/update:

- A hierarchical TODO plan for this issue.
- Explicit acceptance criteria (extracted from the description, plus any
  non-negotiable Validation/Test Plan/Testing items the issue body specifies).
- A validation strategy (specific commands you'll run to prove the change works).

Before writing code:

1. Run the `pull` skill to bring the branch in sync with `origin/main`. Record
   the result (clean / conflicts resolved / new HEAD sha) in the workpad
   `Notes`.
2. Reproduce the current behavior. Capture the reproduction signal (command
   output, log line, screenshot, or deterministic UI step) in `Notes` so the
   final fix is provably correct.
3. Compact the plan and proceed.

## Step 2 — Implementation

1. Update the workpad as you go: check off items, add newly discovered ones,
   keep parent/child structure intact.
2. Make focused commits with the `commit` skill — one commit per logical unit
   of work.
3. After meaningful milestones (reproduction confirmed, code change landed,
   validation green, feedback addressed), update the workpad immediately. Don't
   leave completed work unchecked.
4. Out-of-scope discoveries → file NEW Linear issues in `Backlog`, with a
   `related` link to the current one and `blockedBy` if dependent. Note them
   under `Notes`. Do not expand the current issue's scope.

## Step 3 — Validation

1. Run every acceptance criterion and every Validation/Test Plan command
   present in the issue.
2. Treat unmet items as incomplete work. Update the workpad accordingly.
3. Temporary local proof edits (a hardcoded value, a test fixture tweak) are
   allowed but must be reverted before commit.
4. Document validation outcomes in the workpad `Validation` section with the
   exact command and output summary.

## Step 4 — Push and PR

1. Run the `push` skill: push the branch, create or update the PR, fill in
   title/body referencing this Linear issue.
2. Apply the `symphony` label.
3. Use the Linear MCP to attach the PR URL to the issue (`linear_create_issue_attachment`
   or equivalent). Don't paste it into the workpad.
4. Refresh the workpad once more so it accurately reflects the final scope.

## Step 5 — PR feedback sweep (required before Human Review)

When the issue has an attached PR, run this before transitioning to
`Human Review`:

1. Identify PR number from issue links.
2. Pull all feedback channels:
   - Top-level discussion: `gh pr view --comments`
   - Inline review comments: `gh api repos/{owner}/{repo}/pulls/$pr/comments`
   - Reviews: `gh pr view --json reviews`
3. Treat every actionable reviewer comment (human or bot) as blocking until
   either: code/tests/docs updated to address it, OR a justified pushback
   reply is posted on that thread.
4. Update the workpad with each feedback item and its resolution status.
5. Re-run validation after feedback-driven changes; push updates.
6. Repeat until no outstanding actionable comments remain.

## Step 6 — Transition to Human Review

Only when ALL of the following are true:

- All workpad checklist items complete.
- All acceptance criteria + ticket-provided validation items complete.
- Local validation green for the latest commit.
- PR feedback sweep complete; no outstanding comments.
- PR checks green.
- Branch pushed; PR attached on issue with `symphony` label.

Then transition the issue: `In Progress → Human Review` via Linear MCP.
If blocked by missing non-GitHub auth/permissions, transition to
`Human Review` anyway with a concise blocker brief in the workpad listing:
what is missing, why it blocks the acceptance criteria, exact human action to
unblock.

## Step 7 — Human Review (waiting state)

When the issue is in `Human Review`:

- Do NOT change code or issue content.
- Poll PR review activity. New `[claude]`-prefixed reply may be required if a
  human reviewer adds comments AFTER you transitioned.
- If review feedback requires changes, move to `Rework` and follow the rework
  flow.
- If approved, the human moves the issue to `Merging`. On the next dispatch,
  routing will pick up the `land` skill flow.

## Step 8 — Merging

When state is `Merging`, open `.claude/skills/land/SKILL.md` and run it. After
merge, transition the issue to `Done` via Linear MCP. Do not call
`gh pr merge` outside the land skill.

## Step 9 — Rework

When state is `Rework`, treat it as a full approach reset:

1. Re-read the issue body and ALL human comments. Identify what to do
   differently.
2. Close the existing PR for this branch.
3. Remove the existing `## Workpad` comment.
4. Create a fresh branch from `origin/main` (delete the local one first).
5. Start over from Step 0.

## Guardrails

- Use exactly **one** persistent workpad comment per issue.
- Never edit the issue body/description for planning or progress tracking — use
  the workpad.
- All `[claude]`-prefixed comments are agent-authored. Always use the prefix.
- Do not call `gh pr merge` outside the `land` skill.
- Do not yield while the issue is active unless you hit a true blocker.
- If the branch's PR is closed/merged, do NOT reuse that branch — start a fresh
  one from `origin/main`.

## Definition of done for this turn

You can stop when ANY of these are true:

- Issue moved to `Done` (after merge).
- Issue moved to `Human Review` with the completion bar satisfied OR a clear
  blocker brief.
- Issue moved to `Rework` after a re-plan.
- A true blocker prevents further progress and is recorded in the workpad.

If none apply yet, keep going on this turn. If a turn boundary forces an exit
while the issue is still active, Symphony will continuation-retry you and the
next turn picks up from the workpad.
