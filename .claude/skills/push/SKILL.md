---
name: push
description:
  Push the current branch to origin and create or update the corresponding PR.
  Use after committing on the issue branch.
---

# Push

## Prerequisites

- `gh auth status` succeeds.
- Repo has an `origin` remote on GitHub.

## Steps

1. Identify branch: `branch=$(git branch --show-current)`.
2. Push with upstream tracking if not yet set:
   ```sh
   git push -u origin HEAD
   ```
3. If push is rejected for non-fast-forward / sync reasons, run the `pull`
   skill to merge `origin/main` and resolve conflicts, then push again.
4. Use `--force-with-lease` only when local history was rewritten. Never `--force`.
5. If the rejection is auth/permissions/workflow restriction — surface the
   exact error. Do not rewrite remotes or switch protocols as a workaround.
6. Ensure a PR exists for the branch:
   - `gh pr view --json state -q .state` (returns nothing if no PR)
   - If state is `OPEN`, update that PR with `gh pr edit`.
   - If state is `MERGED`, `CLOSED`, or empty, create a new PR with
     `gh pr create`. GitHub allows multiple PRs over the lifetime of the
     same branch name as long as no two are open simultaneously; closed PRs
     remain as historical record, which is expected.
7. Write a clear PR title that describes the **outcome** of the change.
8. Fill the PR body. If `.github/pull_request_template.md` exists, follow it
   exactly. Otherwise:
   - **What** — short summary of the change
   - **Why** — link to the Linear issue
   - **How** — implementation notes
   - **Validation** — how you verified it works
9. Apply the `symphony` label so this PR is identifiable as agent-authored:
   ```sh
   gh pr edit --add-label symphony 2>/dev/null || true
   ```
10. Report the PR URL: `gh pr view --json url -q .url`.

## Commands

```sh
branch=$(git branch --show-current)

# Initial push
git push -u origin HEAD

# If rejected for sync reasons, use the `pull` skill, then re-push.
# Only use force-with-lease after local history rewrite:
# git push --force-with-lease origin HEAD

# Check PR state. We only treat OPEN as "edit"; MERGED/CLOSED/empty all
# mean "create a fresh PR for the current branch".
pr_state=$(gh pr view --json state -q .state 2>/dev/null || true)

if [ "$pr_state" = "OPEN" ]; then
  gh pr edit --title "<clear PR title>" --body-file /tmp/pr_body.md
else
  gh pr create --title "<clear PR title>" --body-file /tmp/pr_body.md
fi

gh pr edit --add-label symphony 2>/dev/null || true
gh pr view --json url -q .url
```
