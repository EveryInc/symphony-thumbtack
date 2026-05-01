---
name: linear
description: |
  Use the Linear MCP server to interact with Linear: read issues, post and edit
  comments, transition issue state, attach PR links, and create follow-up
  issues. Use when working on a Linear ticket through Symphony.
---

# Linear

Interact with Linear via the configured Linear MCP server.

## Tools you have

The Linear MCP exposes a set of `linear_*` tools. The exact tool names depend
on the MCP server build, but typical tools include:

- `linear_get_issue` / `linear_search_issues`
- `linear_update_issue`              (state transitions, branchName, etc.)
- `linear_create_issue`              (used for follow-up tickets)
- `linear_create_comment`            (post the workpad and updates)
- `linear_update_comment`            (edit the workpad in place)
- `linear_list_comments`
- `linear_create_attachment` / `linear_create_issue_attachment` (link a PR URL)
- `linear_get_workflow_states`       (look up the right state ID for a name)

If a specific tool is not available in your session, use the introspective
tools the MCP provides (or ask the user).

## Workpad protocol

Use exactly **one** persistent workpad comment per issue. Marker header:

```
## Workpad
```

1. List the issue's comments with `linear_list_comments`.
2. If a comment exists whose body starts with `## Workpad`, reuse its ID for
   every progress update — never create a second one.
3. If none exists, create one with `linear_create_comment` and remember the ID.
4. All progress updates use `linear_update_comment` on that same ID.

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

Symphony uses these workflow states (configured in Linear team settings):

- `Backlog` — out of scope; do not touch.
- `Todo` — queued; transition to `In Progress` on first turn.
- `In Progress` — actively implementing.
- `Human Review` — PR validated, waiting for human approval.
- `Merging` — human approved; run the `land` skill until merged.
- `Rework` — reviewer requested changes; full reset and re-attempt.
- `Done` — terminal.

To move an issue, look up the state ID then call `linear_update_issue`:

1. Call `linear_get_workflow_states` for the team, find the state by `name`.
2. Call `linear_update_issue(id=<issue_id>, stateId=<state_id>)`.

## Out-of-scope work

When you discover meaningful follow-up work that's NOT part of the current
issue:

- Create a NEW Linear issue with `linear_create_issue` in `Backlog` state.
- Add `relations: [{ relatedIssueId: <current>, type: "related" }]`.
- Use `type: "blockedBy"` only if the follow-up depends on the current issue.
- Note the new issue ID in the workpad `Notes` section.

Do NOT expand the current issue's scope.

## Attaching the PR

Once the PR is created, attach its URL to the issue:

```
linear_create_issue_attachment(
  issueId=<id>,
  url=<pr_url>,
  title="GitHub PR"
)
```

Or set `branchName` on the issue if the MCP supports it. Don't paste the URL
into the workpad — keep PR linkage on the issue's attachment field.
