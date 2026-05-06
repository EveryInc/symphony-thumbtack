---
name: tasks
description: |
  Use the local `tasks` CLI to read/write `tasks.json` — the offline-demo
  replacement for Linear. Covers reading issues, posting/editing comments,
  transitioning issue state, attaching local PRs, and creating follow-up
  issues. Use when working a ticket through Symphony in offline mode.
---

# tasks (offline-demo)

Interact with the local task store via the bundled `tasks` CLI. The store is
a single JSON file; the path is in `$SYMPHONY_TASKS_FILE` and `tasks` reads it
automatically. There is no Linear MCP and no GitHub.

## CLI surface

Run `tasks --help` for the live list. The relevant subcommands:

| Action | Command |
|---|---|
| List issues | `tasks list [--state STATE]` |
| Show one issue | `tasks get IDENT` |
| Transition state | `tasks update-state IDENT --state "In Progress"` |
| List comments | `tasks comment-list IDENT` |
| Create comment | `tasks comment-create IDENT --body-file /tmp/workpad.md` |
| Update comment | `tasks comment-update IDENT COMMENT_ID --body-file /tmp/workpad.md` |
| Delete comment | `tasks comment-delete IDENT COMMENT_ID` |
| Create local PR | `tasks pr-create IDENT --branch BRANCH --title T --body-file /tmp/pr.md` |
| Update local PR | `tasks pr-update IDENT --title T --body-file /tmp/pr.md --label symphony` |
| View local PR | `tasks pr-view IDENT` |
| Mark PR merged | `tasks pr-merge IDENT --merge-commit SHA` |
| Close PR | `tasks pr-close IDENT` |
| File follow-up | `tasks create-issue --title T --state Backlog --blocked-by IDENT --body-file /tmp/body.md` |

Add `--json` to any command for structured output.

## Workpad protocol

Use exactly **one** persistent workpad comment per issue. Marker header:

```
## Workpad
```

1. Run `tasks comment-list IDENT --json` and look for a comment whose body
   starts with `## Workpad`.
2. If one exists, capture its `id` and use `tasks comment-update IDENT <id>`
   for every progress update — never create a second one.
3. If none exists, write the body to a file (e.g. `/tmp/workpad.md`) and run
   `tasks comment-create IDENT --body-file /tmp/workpad.md`. Capture the new
   comment id from the output.
4. All progress updates use `tasks comment-update IDENT <id>` against that
   same id, replacing the body in place.

Workpad template:

````md
## Workpad

```text
<hostname>:<abs-path>@<short-sha>
```

### Plan

- [ ] 1\. Parent task
  - [ ] 1.1 Child task
- [ ] 2\. Parent task

### Acceptance Criteria

- [ ] Criterion 1

### Validation

- [ ] targeted tests: `<command>`

### Notes

- <short progress note>
````

Keep checkboxes accurate. Do NOT post separate "done" / summary comments — keep
everything in the workpad.

## State transitions

The canonical states (configured in WORKFLOW.md):

- `Backlog` — out of scope; do not touch.
- `Todo` — queued; transition to `In Progress` on first turn.
- `In Progress` — actively implementing.
- `Human Review` — local PR validated, waiting for human approval.
- `Merging` — human approved; run the `land` skill until merged.
- `Rework` — reviewer requested changes; full reset and re-attempt.
- `Done` — terminal.

Move an issue:

```sh
tasks update-state IDENT --state "In Progress"
```

The names are matched case-insensitively but use the exact case shown above
for stylistic consistency.

## Out-of-scope work

When you discover meaningful follow-up work that's NOT part of the current
issue:

```sh
cat > /tmp/follow.md <<'EOF'
Description of the follow-up work…
EOF
tasks create-issue \
  --title "Short follow-up title" \
  --state Backlog \
  --blocked-by IDENT_OF_CURRENT \
  --body-file /tmp/follow.md
```

Note the new identifier in the workpad `Notes` section. Do NOT expand the
current issue's scope.

## Attaching the local PR

The `push` skill calls `tasks pr-create` once and `tasks pr-update` on
subsequent runs. You generally don't need to call these directly from this
skill — but if you do, the relevant call is:

```sh
tasks pr-create IDENT \
  --branch "$(git branch --show-current)" \
  --title "<clear PR title>" \
  --body-file /tmp/pr_body.md \
  --label symphony
```

## Comment authorship convention

All comments authored by you start with `[claude]`. Anything missing that
prefix is from a human reviewer (or the seeder) and should be treated as
external feedback during the PR feedback sweep.
