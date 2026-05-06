---
name: push
description:
  Finalize the local feature branch and create or update a local PR record on
  the issue. Use after committing on the issue branch. (offline-demo: there is
  no remote to push to and no `gh` CLI; "push" here means "package the work
  for human review locally".)
---

# Push (offline-demo)

There is no `origin` remote in this demo. "Push" means: make sure the branch
is in a clean reviewable state and record a local PR entry on the issue via
the `tasks` CLI so the human reviewer can drag the issue to `Merging` when
ready.

## Prerequisites

- Working tree is clean (everything committed via the `commit` skill).
- You know the issue identifier (it's in the prompt as `{{ issue.identifier }}`
  for the agent that invoked you, but you can also re-read it from the
  workpad).
- The branch you're on is `symphony/<identifier>` — that's what the
  `after_create` hook set up.

## Steps

1. Identify branch: `branch=$(git branch --show-current)`.
2. Verify the working tree is clean and committed:
   ```sh
   git status --porcelain
   ```
   If anything is unstaged, commit it first via the `commit` skill.
3. Resolve the issue identifier. If your branch is named `symphony/eng-3`,
   the identifier is the suffix uppercased: `ENG-3`. If you're unsure, check
   the workpad header you stamped earlier.
4. Build the PR body. If the workpad has a clean summary, reuse it; otherwise
   write a concise outcome-focused note covering:
   - **What** — short summary of the change
   - **Why** — link back to the issue (`tasks://{{ issue.identifier }}`)
   - **How** — implementation notes
   - **Validation** — how you verified it works

   Write it to a file:
   ```sh
   cat > /tmp/pr_body.md <<'EOF'
   ## What
   ...

   ## Why
   tasks://IDENT — see issue body.

   ## How
   ...

   ## Validation
   ...
   EOF
   ```
5. Check whether a local PR already exists for this issue:
   ```sh
   tasks pr-view IDENT --json
   ```
   - If output is `null` or `(no PR)` — create one:
     ```sh
     tasks pr-create IDENT \
       --branch "$branch" \
       --title "<clear PR title>" \
       --body-file /tmp/pr_body.md \
       --label symphony
     ```
   - If state is `OPEN` — update it in place:
     ```sh
     tasks pr-update IDENT \
       --title "<clear PR title>" \
       --body-file /tmp/pr_body.md \
       --label symphony
     ```
   - If state is `MERGED` or `CLOSED` — that PR is final. Create a fresh one
     (the `tasks` CLI will refuse to create over an OPEN PR but accepts a new
     one over a closed/merged one):
     ```sh
     tasks pr-create IDENT \
       --branch "$branch" \
       --title "<clear PR title>" \
       --body-file /tmp/pr_body.md \
       --label symphony
     ```
6. Confirm the local PR is recorded:
   ```sh
   tasks pr-view IDENT
   ```
   Capture the PR number — it's `pr.number` in the JSON output.
7. The branch lives only on this machine. Do NOT call `git push` and do NOT
   call `gh` — neither will work. The branch is the artifact; the local PR
   record points at it.

## Guardrails

- Never call `git push`, `gh pr ...`, or any remote-touching command. There
  is no remote.
- Never `--force` anything; there's nothing to force against.
- Never paste the local PR record into the workpad — the issue's `pr` field
  is the canonical view, accessible via `tasks pr-view IDENT`.
