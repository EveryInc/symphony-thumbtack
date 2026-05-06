"""`tasks` CLI — agent-facing replacement for the Linear MCP.

The agent reads/writes the same tasks.json the orchestrator polls. Every
mutation goes through `tasks_store.transaction()` so the file lock keeps
concurrent agents and the orchestrator from clobbering each other.

Subcommands (mirrors what the linear skill used to do):

  tasks list [--state STATE]
  tasks get IDENTIFIER
  tasks update-state IDENTIFIER --state STATE
  tasks comment-list IDENTIFIER
  tasks comment-create IDENTIFIER --body-file PATH
  tasks comment-update IDENTIFIER COMMENT_ID --body-file PATH
  tasks pr-create IDENTIFIER --branch BRANCH --title TITLE --body-file PATH
  tasks pr-update IDENTIFIER --title TITLE --body-file PATH
  tasks pr-view IDENTIFIER
  tasks pr-merge IDENTIFIER --merge-commit SHA

`--json` on every subcommand prints structured output. Without it you get
a short human-readable line.

The file path comes from $SYMPHONY_TASKS_FILE (set by run.sh) or `--file PATH`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import tasks_store


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _resolve_file(arg: Optional[str]) -> str:
    if arg:
        return os.path.abspath(os.path.expanduser(arg))
    env = os.environ.get("SYMPHONY_TASKS_FILE")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    # Fall back to ./tasks.json relative to cwd (mirrors the orchestrator's
    # default of placing tasks.json next to WORKFLOW.md).
    return os.path.abspath("tasks.json")


def _print(obj: Any, *, as_json: bool, fallback: str) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=False))
    else:
        print(fallback)


def _read_body(args) -> str:
    if args.body_file:
        with open(args.body_file, "r", encoding="utf-8") as f:
            return f.read()
    if args.body is not None:
        return args.body
    # Fall back to stdin so agents can heredoc directly.
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


# ─── subcommand handlers ─────────────────────────────────────────────────────


def cmd_list(args) -> int:
    path = _resolve_file(args.file)
    doc = tasks_store.load(path)
    issues = doc.get("issues") or []
    if args.state:
        wanted = args.state.lower()
        issues = [i for i in issues if (i.get("state") or "").lower() == wanted]
    if args.json:
        out = [
            {
                "identifier": i.get("identifier"),
                "title": i.get("title"),
                "state": i.get("state"),
                "blocked_by": list(i.get("blocked_by") or []),
                "url": i.get("url"),
            }
            for i in issues
        ]
        print(json.dumps(out, indent=2))
    else:
        for i in issues:
            blockers = ",".join(i.get("blocked_by") or []) or "-"
            print(f"{i.get('identifier'):<10} {i.get('state'):<14} blocked_by={blockers}  {i.get('title')}")
    return 0


def cmd_get(args) -> int:
    path = _resolve_file(args.file)
    doc = tasks_store.load(path)
    issue = tasks_store.find_issue(doc, args.identifier)
    if not issue:
        sys.exit(f"tasks: no issue {args.identifier!r}")
    if args.json:
        print(json.dumps(issue, indent=2))
    else:
        print(f"{issue['identifier']}  [{issue['state']}]  {issue['title']}")
        print(f"url: {issue.get('url')}")
        print(f"branch: {issue.get('branch_name')}")
        if issue.get("blocked_by"):
            print(f"blocked_by: {', '.join(issue['blocked_by'])}")
        if issue.get("description"):
            print()
            print(issue["description"])
    return 0


def cmd_update_state(args) -> int:
    path = _resolve_file(args.file)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        old = issue.get("state")
        issue["state"] = args.state
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "old_state": old, "new_state": args.state},
        as_json=args.json,
        fallback=f"{args.identifier}: {old} → {args.state}",
    )
    return 0


def cmd_comment_list(args) -> int:
    path = _resolve_file(args.file)
    doc = tasks_store.load(path)
    issue = tasks_store.find_issue(doc, args.identifier)
    if not issue:
        sys.exit(f"tasks: no issue {args.identifier!r}")
    comments = issue.get("comments") or []
    if args.json:
        print(json.dumps(comments, indent=2))
    else:
        if not comments:
            print("(no comments)")
        for c in comments:
            print(f"--- {c['id']}  ({c.get('created_at')}) ---")
            print(c.get("body", ""))
            print()
    return 0


def cmd_comment_create(args) -> int:
    path = _resolve_file(args.file)
    body = _read_body(args)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        nxt = int(doc.get("next_comment_id") or 1)
        cid = f"c{nxt}"
        doc["next_comment_id"] = nxt + 1
        comments = issue.setdefault("comments", [])
        comment = {"id": cid, "body": body, "created_at": _now_iso()}
        comments.append(comment)
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "comment_id": cid},
        as_json=args.json,
        fallback=f"{args.identifier}: created comment {cid}",
    )
    return 0


def cmd_comment_update(args) -> int:
    path = _resolve_file(args.file)
    body = _read_body(args)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        for c in issue.get("comments") or []:
            if c.get("id") == args.comment_id:
                c["body"] = body
                c["updated_at"] = _now_iso()
                issue["updated_at"] = _now_iso()
                _print(
                    {"identifier": args.identifier, "comment_id": args.comment_id},
                    as_json=args.json,
                    fallback=f"{args.identifier}: updated comment {args.comment_id}",
                )
                return 0
        sys.exit(f"tasks: no comment {args.comment_id!r} on {args.identifier!r}")


def cmd_comment_delete(args) -> int:
    path = _resolve_file(args.file)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        comments = issue.get("comments") or []
        new = [c for c in comments if c.get("id") != args.comment_id]
        if len(new) == len(comments):
            sys.exit(f"tasks: no comment {args.comment_id!r} on {args.identifier!r}")
        issue["comments"] = new
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "comment_id": args.comment_id, "deleted": True},
        as_json=args.json,
        fallback=f"{args.identifier}: deleted comment {args.comment_id}",
    )
    return 0


def cmd_pr_create(args) -> int:
    path = _resolve_file(args.file)
    body = _read_body(args)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        existing = issue.get("pr") or {}
        if existing and existing.get("state") == "OPEN":
            sys.exit(
                f"tasks: {args.identifier} already has an OPEN PR #{existing.get('number')}"
            )
        nxt = int(doc.get("next_pr_number") or 1)
        pr = {
            "number": nxt,
            "title": args.title,
            "body": body,
            "branch": args.branch,
            "state": "OPEN",
            "created_at": _now_iso(),
            "merged_at": None,
            "merge_commit": None,
            "labels": list(args.label or []),
        }
        doc["next_pr_number"] = nxt + 1
        issue["pr"] = pr
        issue["branch_name"] = args.branch
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "pr": pr},
        as_json=args.json,
        fallback=f"{args.identifier}: created local PR #{pr['number']} on branch {pr['branch']}",
    )
    return 0


def cmd_pr_update(args) -> int:
    path = _resolve_file(args.file)
    body = _read_body(args) if (args.body or args.body_file or not sys.stdin.isatty()) else None
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        pr = issue.get("pr")
        if not pr:
            sys.exit(f"tasks: no PR on {args.identifier!r}; create one with pr-create")
        if args.title:
            pr["title"] = args.title
        if body is not None and body != "":
            pr["body"] = body
        if args.label:
            labels = set(pr.get("labels") or [])
            labels.update(args.label)
            pr["labels"] = sorted(labels)
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "pr": pr},
        as_json=args.json,
        fallback=f"{args.identifier}: updated local PR #{pr['number']}",
    )
    return 0


def cmd_pr_view(args) -> int:
    path = _resolve_file(args.file)
    doc = tasks_store.load(path)
    issue = tasks_store.find_issue(doc, args.identifier)
    if not issue:
        sys.exit(f"tasks: no issue {args.identifier!r}")
    pr = issue.get("pr")
    if not pr:
        if args.json:
            print("null")
        else:
            print("(no PR)")
        return 0
    if args.json:
        print(json.dumps(pr, indent=2))
    else:
        print(f"PR #{pr['number']}  [{pr['state']}]  branch={pr['branch']}")
        print(f"title: {pr['title']}")
        print()
        print(pr.get("body", ""))
    return 0


def cmd_pr_merge(args) -> int:
    path = _resolve_file(args.file)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        pr = issue.get("pr")
        if not pr:
            sys.exit(f"tasks: no PR on {args.identifier!r}")
        if pr.get("state") != "OPEN":
            sys.exit(f"tasks: PR #{pr['number']} is {pr['state']}, not OPEN")
        pr["state"] = "MERGED"
        pr["merged_at"] = _now_iso()
        pr["merge_commit"] = args.merge_commit
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "pr": pr},
        as_json=args.json,
        fallback=f"{args.identifier}: merged local PR #{pr['number']} ({args.merge_commit[:8]})",
    )
    return 0


def cmd_pr_close(args) -> int:
    path = _resolve_file(args.file)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        pr = issue.get("pr")
        if not pr:
            sys.exit(f"tasks: no PR on {args.identifier!r}")
        pr["state"] = "CLOSED"
        pr["closed_at"] = _now_iso()
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "pr": pr},
        as_json=args.json,
        fallback=f"{args.identifier}: closed local PR #{pr['number']}",
    )
    return 0


def cmd_attach_branch(args) -> int:
    path = _resolve_file(args.file)
    with tasks_store.transaction(path) as doc:
        issue = tasks_store.find_issue(doc, args.identifier)
        if not issue:
            sys.exit(f"tasks: no issue {args.identifier!r}")
        issue["branch_name"] = args.branch
        issue["updated_at"] = _now_iso()
    _print(
        {"identifier": args.identifier, "branch": args.branch},
        as_json=args.json,
        fallback=f"{args.identifier}: branch={args.branch}",
    )
    return 0


def cmd_create_issue(args) -> int:
    """Create a NEW issue (used for follow-up tickets)."""
    path = _resolve_file(args.file)
    body = _read_body(args)
    with tasks_store.transaction(path) as doc:
        # Identifier scheme: <team>-<n>. Compute next n.
        team = doc.get("team") or "ENG"
        existing_nums: List[int] = []
        for n in doc.get("issues") or []:
            ident = n.get("identifier") or ""
            if ident.startswith(team + "-"):
                try:
                    existing_nums.append(int(ident.split("-", 1)[1]))
                except ValueError:
                    pass
        nxt = max(existing_nums, default=0) + 1
        new_ident = f"{team}-{nxt}"
        new_issue = {
            "id": new_ident,
            "identifier": new_ident,
            "title": args.title,
            "description": body,
            "state": args.state,
            "priority": None,
            "branch_name": None,
            "url": f"tasks://{new_ident}",
            "labels": list(args.label or []),
            "blocked_by": list(args.blocked_by or []),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "comments": [],
            "pr": None,
        }
        doc.setdefault("issues", []).append(new_issue)
    _print(
        {"identifier": new_ident, "title": args.title, "state": args.state},
        as_json=args.json,
        fallback=f"created {new_ident}: {args.title}",
    )
    return 0


def cmd_states(args) -> int:
    """List the canonical workflow states (advisory; the file accepts any)."""
    states = [
        "Backlog",
        "Todo",
        "In Progress",
        "Human Review",
        "Merging",
        "Rework",
        "Done",
    ]
    if args.json:
        print(json.dumps(states))
    else:
        for s in states:
            print(s)
    return 0


# ─── argparse wiring ─────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tasks",
        description="Local JSON task store CLI (Linear stand-in for offline-demo mode).",
    )
    p.add_argument("--file", help="Path to tasks.json (default: $SYMPHONY_TASKS_FILE)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human prose")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="List issues, optionally filtered by state")
    s.add_argument("--state")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("get", help="Show one issue's full record")
    s.add_argument("identifier")
    s.set_defaults(func=cmd_get)

    s = sub.add_parser("update-state", help="Transition one issue's workflow state")
    s.add_argument("identifier")
    s.add_argument("--state", required=True)
    s.set_defaults(func=cmd_update_state)

    s = sub.add_parser("states", help="List the canonical workflow state names")
    s.set_defaults(func=cmd_states)

    s = sub.add_parser("comment-list", help="List all comments on an issue")
    s.add_argument("identifier")
    s.set_defaults(func=cmd_comment_list)

    s = sub.add_parser("comment-create", help="Create a new comment")
    s.add_argument("identifier")
    body_grp = s.add_mutually_exclusive_group()
    body_grp.add_argument("--body")
    body_grp.add_argument("--body-file")
    s.set_defaults(func=cmd_comment_create)

    s = sub.add_parser("comment-update", help="Replace a comment's body in place")
    s.add_argument("identifier")
    s.add_argument("comment_id")
    body_grp = s.add_mutually_exclusive_group()
    body_grp.add_argument("--body")
    body_grp.add_argument("--body-file")
    s.set_defaults(func=cmd_comment_update)

    s = sub.add_parser("comment-delete", help="Delete a comment")
    s.add_argument("identifier")
    s.add_argument("comment_id")
    s.set_defaults(func=cmd_comment_delete)

    s = sub.add_parser("pr-create", help="Create a local PR record on the issue")
    s.add_argument("identifier")
    s.add_argument("--branch", required=True)
    s.add_argument("--title", required=True)
    body_grp = s.add_mutually_exclusive_group()
    body_grp.add_argument("--body")
    body_grp.add_argument("--body-file")
    s.add_argument("--label", action="append", default=[])
    s.set_defaults(func=cmd_pr_create)

    s = sub.add_parser("pr-update", help="Update title/body/labels of an existing local PR")
    s.add_argument("identifier")
    s.add_argument("--title")
    body_grp = s.add_mutually_exclusive_group()
    body_grp.add_argument("--body")
    body_grp.add_argument("--body-file")
    s.add_argument("--label", action="append", default=[])
    s.set_defaults(func=cmd_pr_update)

    s = sub.add_parser("pr-view", help="Show the local PR for an issue")
    s.add_argument("identifier")
    s.set_defaults(func=cmd_pr_view)

    s = sub.add_parser("pr-merge", help="Mark the local PR as merged")
    s.add_argument("identifier")
    s.add_argument("--merge-commit", required=True)
    s.set_defaults(func=cmd_pr_merge)

    s = sub.add_parser("pr-close", help="Mark the local PR as closed (without merging)")
    s.add_argument("identifier")
    s.set_defaults(func=cmd_pr_close)

    s = sub.add_parser("attach-branch", help="Record the branch name on the issue")
    s.add_argument("identifier")
    s.add_argument("--branch", required=True)
    s.set_defaults(func=cmd_attach_branch)

    s = sub.add_parser("create-issue", help="Create a new follow-up issue")
    s.add_argument("--title", required=True)
    s.add_argument("--state", default="Backlog")
    s.add_argument("--label", action="append", default=[])
    s.add_argument("--blocked-by", action="append", default=[])
    body_grp = s.add_mutually_exclusive_group()
    body_grp.add_argument("--body")
    body_grp.add_argument("--body-file")
    s.set_defaults(func=cmd_create_issue)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
