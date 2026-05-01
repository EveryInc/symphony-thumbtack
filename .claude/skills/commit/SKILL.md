---
name: commit
description:
  Create well-formed git commits from current changes. Use when staging and
  committing work-in-progress on the issue branch.
---

# Commit

## Goals

- Produce commits that reflect actual code changes and session intent.
- Follow conventional-commit style (type prefix, ≤72-char subject, wrapped body).
- Include both summary and rationale in the body.

## Steps

1. Inspect: `git status`, `git diff`, `git diff --staged`.
2. Stage intended changes: `git add -A` (after confirming scope).
3. Sanity-check newly added files. Skip build artifacts, logs, temp files. If
   anything looks accidentally staged, fix the index before committing.
4. Choose conventional type: `feat(scope): ...`, `fix(scope): ...`,
   `refactor(scope): ...`, `chore(scope): ...`, `docs(scope): ...`,
   `test(scope): ...`.
5. Subject in imperative mood, ≤72 chars, no trailing period.
6. Body sections (wrapped at 72):
   - **Summary** — what changed
   - **Rationale** — why
   - **Tests** — what you ran (or "not run (reason)")
7. Use a heredoc with `git commit -F` so newlines are literal.

## Template

```
<type>(<scope>): <short summary>

Summary:
- <what changed>

Rationale:
- <why>

Tests:
- <command or "not run (reason)">

Co-authored-by: Claude <noreply@anthropic.com>
```

## Commands

```sh
git status
git diff --staged
git add -A
git commit -F /tmp/commit_message.md
```

## Guardrails

- Don't commit if the staged diff disagrees with the message — fix one or the other.
- Don't run `git commit --amend` on commits already pushed to a shared branch.
- Don't `git reset --hard` without explicit confirmation.
