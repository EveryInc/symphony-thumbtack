---
name: land
description:
  Land a PR by resolving conflicts, watching CI, and squash-merging when green.
  Use ONLY when the human reviewer has moved the issue to "Merging" state.
  Never call gh pr merge directly — go through this skill so checks pass first.
---

# Land

## Goals

- PR is conflict-free with `main`.
- All CI checks are green.
- PR is squash-merged.
- Issue is moved to `Done` after the merge.

## Preconditions

- `gh` CLI authenticated.
- You are on the PR branch with a clean working tree.
- The Linear issue is in `Merging` state (the human approved it).

## Steps

1. Resolve PR context:
   ```sh
   branch=$(git branch --show-current)
   pr_number=$(gh pr view --json number -q .number)
   pr_title=$(gh pr view --json title -q .title)
   pr_body=$(gh pr view --json body -q .body)
   ```
2. Check mergeability:
   ```sh
   mergeable=$(gh pr view --json mergeable -q .mergeable)
   ```
   - If `CONFLICTING`, run the `pull` skill, then run the `push` skill.
   - If `UNKNOWN`, sleep 10s and retry.
3. Watch CI checks until they finish:
   ```sh
   gh pr checks --watch
   ```
   - If checks fail: pull failure logs (`gh pr checks`, `gh run view <run-id> --log`),
     fix locally, run `commit` then `push`, and re-watch.
   - Distinguish flakes from real failures. A flake on a single platform may be
     ignored; a deterministic failure must be fixed.
4. Address any review feedback:
   - List inline review comments:
     `gh api repos/{owner}/{repo}/pulls/$pr_number/comments`
   - List top-level discussion: `gh pr view --comments`
   - Reply to inline review comments with `in_reply_to`:
     ```sh
     gh api -X POST /repos/{owner}/{repo}/pulls/$pr_number/comments \
       -f body='[claude] <response>' -F in_reply_to=<comment_id>
     ```
   - Prefix all your comments with `[claude]`.
   - For each comment, choose: **accept** (implement), **clarify** (ask), or
     **push back** (justified disagreement). Reply BEFORE making code changes.
5. Squash-merge once everything is green and review is resolved:
   ```sh
   gh pr merge --squash --subject "$pr_title" --body "$pr_body"
   ```
6. Move the Linear issue to `Done` via the Linear MCP (see `linear` skill).

## Failure handling

- CI flake: noted but not necessarily a blocker — use judgment, document in workpad.
- CI hard fail: fix → commit → push → re-watch.
- Mergeability `UNKNOWN`: wait, retry.
- Auth/permission errors on `gh`: surface the exact error. Don't rewrite remotes.
- Review feedback that conflicts with the user's stated intent: reply inline
  with rationale and ask the user before changing code.

## Guardrails

- Never `gh pr merge --auto` — this repo may not have required checks gating that.
- Never call `gh pr merge` outside this skill flow.
- Never force-push without `--force-with-lease`.
- All bot-authored PR/issue comments must start with `[claude]`.
- Do not yield until the PR is merged or you hit a true blocker.
