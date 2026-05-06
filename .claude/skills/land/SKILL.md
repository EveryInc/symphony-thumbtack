---
name: land
description:
  Land a local PR by resolving conflicts and squash-merging the feature
  branch into local `main`. Use ONLY when the human reviewer has moved the
  issue to "Merging" state. Never call `git merge` or `git checkout main`
  outside this skill — go through this flow so conflicts and the local PR
  record stay in sync. (offline-demo: there is no remote, no `gh`, no CI.)
---

# Land (offline-demo)

There is no remote, no GitHub PR, and no CI in this demo. "Land" means:

1. Make sure the feature branch is conflict-free against local `main`.
2. Squash-merge the feature branch into local `main`.
3. Mark the local PR record as `MERGED` and move the issue to `Done`.

## Goals

- Branch is conflict-free with `main`.
- Local validation has been run on this branch (you should have done this
  during the run that opened the PR; re-run if anything changed).
- The branch is squash-merged into local `main`.
- The local PR record (`tasks pr-merge`) is marked `MERGED`.
- The issue is moved to `Done`.

## Preconditions

- You are on the feature branch (`symphony/<identifier>`) with a clean
  working tree.
- The issue is in `Merging` state (the human approved it).
- The local PR record is `OPEN`.

## Steps

1. Resolve PR context:
   ```sh
   branch=$(git branch --show-current)
   identifier=$(echo "$branch" | sed 's|^symphony/||' | tr '[:lower:]' '[:upper:]')
   tasks pr-view "$identifier"
   ```
2. Make sure the branch is up to date against `main`:
   ```sh
   git fetch . main:main 2>/dev/null || true
   ```
   If the branch hasn't ingested recent `main` commits, run the `pull` skill
   first. The `pull` skill is configured for this offline mode and merges
   from local `main` rather than `origin/main`.
3. Address any review feedback. In offline mode, reviewer comments live on
   the issue itself (not on the PR), so:
   ```sh
   tasks comment-list "$identifier"
   ```
   - For each non-`[claude]` comment that's actionable, choose: **accept**
     (implement), **clarify** (ask), or **push back** (justified disagreement).
   - Reply by appending a `[claude]`-prefixed comment with your decision:
     ```sh
     cat > /tmp/reply.md <<'EOF'
     [claude] Re: "<paraphrase of the reviewer's point>"

     <decision and rationale>
     EOF
     tasks comment-create "$identifier" --body-file /tmp/reply.md
     ```
   - For accepted feedback, make the code change, run the `commit` skill, and
     re-run the `push` skill to update the PR body before merging.
4. Re-run validation if any code changed:
   ```sh
   # Whatever the issue's Validation section specifies, e.g.:
   pytest -q
   ```
5. Squash-merge into local `main`. From the feature branch:
   ```sh
   pr_title=$(tasks pr-view "$identifier" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["title"])')
   pr_body=$(tasks pr-view "$identifier" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["body"])')
   # Stash any in-flight uncommitted changes — there should be none at this point.
   git switch main
   git -c merge.ff=false merge --squash "$branch"
   commit_msg=$(printf '%s\n\n%s' "$pr_title" "$pr_body")
   git commit -m "$commit_msg"
   merge_sha=$(git rev-parse HEAD)
   ```
   Note: this is happening **inside the agent's git worktree**. The squashed
   commit lands on the worktree's local `main`. The orchestrator's
   `before_remove` hook will clean up the worktree afterwards. The squashed
   commit is the deliverable for this issue.
6. Mark the local PR merged and move the issue to `Done`:
   ```sh
   tasks pr-merge "$identifier" --merge-commit "$merge_sha"
   tasks update-state "$identifier" --state "Done"
   ```
7. Switch back to the feature branch so any post-skill housekeeping the
   orchestrator runs has the expected HEAD:
   ```sh
   git switch "$branch"
   ```

## Failure handling

- **Merge conflicts**: run the `pull` skill, resolve, run the `commit` skill,
  re-run the `push` skill, then start this skill from the top.
- **Validation regressions after a feedback-driven change**: fix → commit →
  push → re-validate before re-attempting the merge.
- **Unexpected branch state** (you're not on the feature branch, or `main`
  diverged in unexpected ways): stop and surface the exact state in the
  workpad. Don't try to recover with destructive commands.

## Guardrails

- Never `git push`, `git fetch origin`, or `gh ...`. There is no remote.
- Never `--force` anything; nothing forces in offline mode.
- Never call `git merge` or `git switch main` outside this skill flow.
- All bot-authored issue/PR comments must start with `[claude]`.
- Do not yield until the squash-merge has landed and the issue is `Done`,
  unless you hit a true blocker.
