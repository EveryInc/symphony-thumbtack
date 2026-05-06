---
name: pull
description:
  Merge local `main` into the current branch and resolve merge conflicts
  (merge-style, not rebase). Use when the branch is stale or the land skill
  reports an out-of-date branch. (offline-demo: there is no `origin`; this
  pulls from the worktree's local `main` ref.)
---

# Pull (offline-demo)

There is no remote in this demo. "Pull" here means: bring local `main`
commits into the current feature branch.

## Workflow

1. Verify a clean working tree. Commit or stash before merging.
2. Enable rerere so conflict resolutions are remembered:
   ```sh
   git config rerere.enabled true
   git config rerere.autoupdate true
   ```
3. Confirm you're on the feature branch (`symphony/<identifier>`), not on
   `main`:
   ```sh
   git branch --show-current
   ```
4. Merge `main` into the current branch:
   ```sh
   git -c merge.conflictstyle=zdiff3 merge main
   ```
5. If conflicts: resolve, `git add <files>`, `git commit`
   (or `git merge --continue`).
6. Verify nothing was missed: `git diff --check`.

Note: `git pull` and `git fetch origin` will both fail in this demo because
there is no `origin`. Use `git merge main` directly.

## Conflict resolution

- Inspect intent: `git status`, `git diff --merge`, and
  `git diff :1:path :2:path` (base vs ours), `git diff :1:path :3:path`
  (base vs theirs) for file-level views.
- With `merge.conflictstyle=zdiff3`, conflict markers show: `<<<<<<<` ours,
  `|||||||` base, `=======` split, `>>>>>>>` theirs. Matching context near
  the edges is trimmed automatically.
- Decide the **final intended behavior** first; only then craft the code.
- Prefer minimal, intention-preserving edits.
- Resolve one file at a time and rerun any local tests after each batch.
- Use `ours`/`theirs` only when one side should clearly win entirely.
- Generated files: resolve source first, then regenerate, then stage.
- Import conflicts: keep both temporarily, then prune via lint/typecheck.

## When to ask the user

Default: don't. Make a best-effort decision, document the rationale in the
merge commit, and proceed. Ask only when:

- Resolution depends on product intent that isn't inferable from code/tests.
- A user-visible API contract is at stake.
- The merge introduces irreversible side effects (data loss, schema drops).
- The branch or remote isn't what you expected.
