#!/usr/bin/env python3
"""Seed the local tasks.json with the promatch demo issues.

Mirrors the structure of seed-linear.py from the online demo (same titles,
same descriptions, same blocked_by relationships, same Stage-1 / Stage-2
split) so the demo content is byte-identical between the two variants.

Reads SYMPHONY_TASKS_FILE from the environment (loaded from config.env via
bootstrap.sh, or sourced manually). Idempotent:

  - First run on an empty/missing file: creates the project and all issues.
  - Subsequent runs: adds any issues that don't already exist (matched by
    title), skips the rest. Existing comments and PRs are preserved.
  - With --force: REPLACES the file. Comments + PR records are wiped.

Stdlib only — no pip install needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TEAM = "ENG"
PROJECT_NAME = "Promatch Demo"


# ─────────────────────────────────────────────────────────────────────────────
# The demo issues. Each one is a buildable, testable slice of the dashboard.
# Audience watches them go from Todo → ... → Done and the dashboard appear.
#
# `blocked_by` lists titles of upstream issues. Symphony's
# `todo_blockers_resolved()` keeps a Todo issue ineligible for dispatch while
# any blocker is still in a non-terminal state — so the demo unfolds in order.
# ─────────────────────────────────────────────────────────────────────────────

ISSUES = [
    {
        "title": "Stand up a basic FastAPI dashboard server",
        "blocked_by": [],
        "body": textwrap.dedent("""\
            We need a web dashboard the agent can share with its human owner.
            Start with the smallest viable thing: a FastAPI server that lists all
            requests at `/`.

            ## Acceptance criteria

            - Add `fastapi` and `uvicorn` to `pyproject.toml` dependencies.
            - New module `promatch/server.py` exposing a FastAPI `app` with a `/`
              route that renders an HTML page listing every request (id, status,
              category, zip, budget, description).
            - New CLI command `promatch serve` that runs uvicorn on `localhost:5050`
              by default. Take an optional `--port` flag.
            - Use Jinja2 templates in `promatch/templates/` — keep the markup small
              and clean. No external CSS frameworks; a few inlined CSS rules in a
              `<style>` block is fine.
            - When the DB is empty, the page should say so clearly rather than
              showing an empty table.

            ## Validation

            ```sh
            promatch seed
            promatch request "Mount a TV" -c handyman -z 94110 -b 200
            promatch serve --port 5050 &
            curl -s http://localhost:5050/ | grep -i "Mount a TV"
            ```
        """),
    },
    {
        "title": "Refactor booking logic into promatch/services.py",
        "blocked_by": [],
        "body": textwrap.dedent("""\
            The CLI's `cmd_accept` does the booking transaction inline (mark
            quote accepted, decline siblings, mark request booked). The web
            dashboard will need the same logic, and we don't want two copies.

            Pull this out into a service module *now*, before the dashboard
            tickets land, so the accept-quote endpoint can share it cleanly.

            ## Acceptance criteria

            - New module `promatch/services.py` exposing a `book_quote(quote_id)`
              function that performs the full transaction (accept the quote,
              decline siblings, mark request booked) and returns the updated
              request payload.
            - `cli.cmd_accept` calls `services.book_quote(...)` instead of
              issuing SQL itself. CLI behavior is identical.
            - Add a `BookingError` exception type for "quote not found" /
              "already booked" cases. CLI translates it to a `ClickException`.
            - Add a unit test `tests/test_services.py` covering: success,
              already-booked rejection, unknown-quote rejection.

            ## Validation

            ```sh
            pytest -q                           # all tests pass, including new ones
            promatch reset --yes && promatch seed
            promatch request "x" -c handyman -z 94110 -b 100
            promatch accept 1                    # still works, status -> booked
            ```
        """),
    },
    {
        "title": "Show quotes per request on the dashboard",
        "blocked_by": ["Stand up a basic FastAPI dashboard server"],
        "body": textwrap.dedent("""\
            Each request row on `/` should expand to show the quotes received for
            it: pro name, rating, price, ETA, message, status.

            ## Acceptance criteria

            - On `/`, each request renders below it a small inner table of pending
              quotes, sorted by price ascending.
            - If no quotes yet, show "Awaiting quotes…".
            - Declined and accepted quotes are hidden by default. Add a query
              param `?show_all=1` that includes them.
            - Add basic styling so the visual hierarchy is clear (request as
              card, quotes as nested list).

            ## Validation

            ```sh
            promatch seed
            promatch request "Fix leaky kitchen faucet" -c plumbing -z 94103 -b 250
            promatch serve --port 5050 &
            curl -s http://localhost:5050/ | grep -E "Mike|Reliable|South Bay"
            ```
        """),
    },
    {
        "title": "Add accept-quote action from the dashboard",
        "blocked_by": [
            "Show quotes per request on the dashboard",
            "Refactor booking logic into promatch/services.py",
        ],
        "body": textwrap.dedent("""\
            Right now you can only accept a quote via the CLI. Add a button on
            each pending quote that books the pro.

            ## Acceptance criteria

            - Each pending quote on `/` has an "Accept" button.
            - Button submits a `POST /quotes/<id>/accept` form (HTML form, no JS
              required for the demo).
            - On success, redirect back to `/` (303 See Other).
            - The endpoint MUST call `services.book_quote(...)` (the function
              extracted in the refactor ticket). Do NOT duplicate the booking
              transaction inline in the route handler.
            - After accepting, that request shows status "booked" and only the
              accepted quote remains visible in the default view.

            ## Validation

            ```sh
            pytest                       # existing tests still pass
            promatch reset --yes && promatch seed
            promatch request "Assemble Pax wardrobe" -c furniture-assembly -z 94103 -b 200
            promatch serve --port 5050 &
            # In a browser, click Accept on a quote. Verify status changes.
            ```
        """),
    },
    {
        "title": "Auto-refresh the dashboard so quotes appear live",
        "blocked_by": ["Stand up a basic FastAPI dashboard server"],
        "body": textwrap.dedent("""\
            The dashboard should update without manual refresh so the human owner
            actually sees activity in real time.

            ## Acceptance criteria

            - The `/` page polls `GET /api/state` every 2 seconds and re-renders
              just the request list (vanilla `fetch` + `setInterval` is fine —
              no React/HTMX).
            - `GET /api/state` returns JSON: a list of requests, each with its
              quotes, suitable for client-side rendering.
            - A small "live" indicator shows when polling is active.
            - The first paint is server-rendered (no blank flash) — JS only takes
              over after that.

            ## Validation

            Run `promatch serve`, open browser, run a `promatch request …` in
            another terminal, watch the page update within ~2s without refresh.
        """),
    },
    {
        "title": "Add a status filter and per-request detail page",
        "blocked_by": ["Stand up a basic FastAPI dashboard server"],
        "body": textwrap.dedent("""\
            Make the dashboard navigable: filter by status and drill into a single
            request.

            ## Acceptance criteria

            - `GET /` accepts a `?status=open|matched|booked|cancelled` query
              filter. Add a small filter bar UI.
            - New route `GET /requests/<id>` shows the full detail for one
              request: description, all quotes (pending/accepted/declined),
              created-at timestamp, status timeline.
            - Both pages share a base template (`base.html`) with consistent
              header + nav.

            ## Validation

            ```sh
            curl -s "http://localhost:5050/?status=booked"
            curl -s "http://localhost:5050/requests/1"
            ```
        """),
    },
    {
        "title": "Add tests for the dashboard endpoints",
        "blocked_by": [
            "Stand up a basic FastAPI dashboard server",
            "Show quotes per request on the dashboard",
            "Add accept-quote action from the dashboard",
            "Add a status filter and per-request detail page",
        ],
        "body": textwrap.dedent("""\
            We have CLI tests but nothing for the new server. Lock in the dashboard
            behavior so future tickets don't regress it.

            ## Acceptance criteria

            - New `tests/test_server.py` using `fastapi.testclient.TestClient`.
            - Cover: GET `/` empty state, GET `/` with one request + quotes, the
              `?status=` filter, GET `/requests/<id>` happy path + 404, the
              accept-quote POST endpoint.
            - Tests use the same `PROMATCH_DB` fixture pattern as `test_cli.py`
              (isolated tmp DB per test).
            - All existing tests still pass.

            ## Validation

            ```sh
            pytest -q
            # 8+ tests, all green
            ```
        """),
    },
    {
        "title": "Document the dashboard in the README",
        "blocked_by": [
            "Stand up a basic FastAPI dashboard server",
            "Show quotes per request on the dashboard",
            "Add accept-quote action from the dashboard",
            "Auto-refresh the dashboard so quotes appear live",
            "Add a status filter and per-request detail page",
        ],
        "body": textwrap.dedent("""\
            With the dashboard shipped, the README's "what's NOT here yet" line
            is wrong. Replace it with a real Dashboard section.

            ## Acceptance criteria

            - New `## Dashboard` section in `README.md` covering: how to start
              the server (`promatch serve`), what the page shows, the
              auto-refresh, the accept flow, the filter bar.
            - Include a one-line description near the top of the README so a
              reader knows promatch has a UI.
            - Remove the "What's NOT here yet" section.
            - No screenshots needed — keep it text-only for now.

            ## Validation

            Read the local README. Fresh eyes should be able to spin up the
            dashboard from the README alone.
        """),
    },

    # ── Stage 2: Backlog. Surface these to Todo after stage 1 is Done. ──────
    {
        "title": "Add a pro-facing dashboard page",
        "state": "Backlog",
        "blocked_by": ["Stand up a basic FastAPI dashboard server"],
        "body": textwrap.dedent("""\
            The dashboard today is the customer's view. Add a page from the
            pro's perspective: what jobs they could quote on right now.

            ## Acceptance criteria

            - New route `GET /pro/<id>` that lists open requests in the same
              category as that pro, in their zip area, that they haven't already
              quoted on.
            - Each row has an inline form to submit a quote (price + ETA + message)
              that POSTs to `/pro/<id>/quote/<request_id>`.
            - Reuses the same base template as the customer dashboard.

            ## Validation

            ```sh
            promatch reset --yes && promatch seed
            promatch request "Mount a TV" -c handyman -z 94110 -b 200
            # In a browser, /pro/6 (Bay Area Fix-It) should list that request.
            # Submit a quote from the form. Verify it shows on / for the customer.
            ```
        """),
    },
    {
        "title": "Replace dashboard polling with Server-Sent Events",
        "state": "Backlog",
        "blocked_by": [
            "Auto-refresh the dashboard so quotes appear live",
        ],
        "body": textwrap.dedent("""\
            Polling every 2s is wasteful and laggy. Switch to SSE so updates
            arrive the instant they happen.

            ## Acceptance criteria

            - New endpoint `GET /api/events` (text/event-stream) that emits a
              JSON event whenever a request or quote changes (created, status
              change). Use a simple in-process broadcaster — no Redis.
            - The dashboard subscribes via `EventSource` and re-renders on each
              event instead of polling `/api/state`.
            - Keep `/api/state` working for first paint and as a fallback.
            - The "live" indicator now reflects the open SSE connection.

            ## Validation

            Open browser devtools → Network → confirm one long-running
            `text/event-stream` connection instead of recurring `/api/state` calls.
            Run `promatch request …` in another terminal — the dashboard updates
            within 200ms.
        """),
    },
    {
        "title": "Auto-generate OpenAPI docs at /api/docs",
        "state": "Backlog",
        "blocked_by": ["Stand up a basic FastAPI dashboard server"],
        "body": textwrap.dedent("""\
            Agents that consume the promatch API need a contract. FastAPI gives
            us OpenAPI for free — wire it up properly.

            ## Acceptance criteria

            - Every JSON route (`/api/state`, `/api/events`, `/quotes/<id>/accept`,
              and any new ones) has a `response_model` and a docstring.
            - Mount Swagger UI at `/api/docs` and ReDoc at `/api/redoc`.
            - Add a small "API" link in the dashboard header to `/api/docs`.

            ## Validation

            ```sh
            curl -s http://localhost:5050/openapi.json | jq '.info.title'
            # opens in browser:
            open http://localhost:5050/api/docs
            ```
        """),
    },
    {
        "title": "Bug: cancelled requests still appear on the default dashboard",
        "state": "Backlog",
        "blocked_by": ["Add a status filter and per-request detail page"],
        "body": textwrap.dedent("""\
            The default `/` view shows *every* request, including cancelled
            ones. That's noise. The customer-facing dashboard should hide
            cancelled by default but keep them reachable via `?status=cancelled`.

            ## Acceptance criteria

            - On `/`, the default request list excludes `status=cancelled`.
            - `/?status=cancelled` still works and shows only cancelled.
            - `/?status=all` (new) shows everything.
            - Filter UI updated to reflect the new default.

            ## Validation

            ```sh
            promatch request "x" -c handyman -z 94110 -b 100
            promatch cancel 1
            curl -s http://localhost:5050/ | grep -c 'request-row'   # 0
            curl -s http://localhost:5050/?status=cancelled | grep -c 'request-row'  # 1
            ```
        """),
    },
    {
        "title": "Add `promatch agent-book` for autonomous booking",
        "state": "Backlog",
        "blocked_by": [],
        "body": textwrap.dedent("""\
            Demonstrate agentic-first: a single command an AI agent could run to
            go from natural-language description to booked pro, end-to-end.

            ## Acceptance criteria

            - New CLI command:
              `promatch agent-book "Assemble Pax wardrobe" --zip 94103 --budget 200`
            - It infers the category from a small keyword map in the source
              (no external API). E.g. "assemble" → `furniture-assembly`,
              "leak"/"faucet" → `plumbing`. Document the map in the function
              docstring.
            - It posts the request, waits for quotes (already synchronous in
              the simulator), picks the best quote (lowest price among
              pros with rating ≥ 4.6, falling back to lowest price overall),
              accepts it, and prints the booked pro + price.
            - Exit non-zero with a clear message if no quotes arrive or no
              category can be inferred.
            - `--json` flag prints the structured result instead of prose.

            ## Validation

            ```sh
            promatch agent-book "Assemble Pax wardrobe" --zip 94103 --budget 200
            # → "Booked IKEA Assembly Pros at $72.50 (4.9★, ETA 6h)"
            promatch agent-book "definitely-not-a-real-job" --zip 94103 --budget 50
            # → exits 1 with "could not infer category from description"
            ```
        """),
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _slug_branch(identifier: str) -> str:
    return f"symphony/{identifier.lower().replace('-', '-')}"


def build_doc(team: str) -> dict:
    """Convert ISSUES into the on-disk schema."""
    issues_out = []
    for n, spec in enumerate(ISSUES, start=1):
        ident = f"{team}-{n}"
        state = spec.get("state", "Todo")
        # Resolve blocked_by titles → identifiers using the order above.
        blocker_idents = []
        for blocker_title in spec.get("blocked_by") or []:
            for m, other in enumerate(ISSUES, start=1):
                if other["title"] == blocker_title:
                    blocker_idents.append(f"{team}-{m}")
                    break
            else:
                # silently skip unknown reference; matches seed-linear.py's
                # warn-then-continue behavior.
                pass
        issues_out.append({
            "id": ident,
            "identifier": ident,
            "title": spec["title"],
            "description": spec["body"],
            "state": state,
            "priority": None,
            "branch_name": _slug_branch(ident),
            "url": f"tasks://{ident}",
            "labels": [],
            "blocked_by": blocker_idents,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "comments": [],
            "pr": None,
        })
    return {
        "version": 1,
        "team": team,
        "project": PROJECT_NAME,
        "next_comment_id": 1,
        "next_pr_number": 1,
        "issues": issues_out,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Seed local tasks.json with demo issues.")
    p.add_argument("--file", default=os.environ.get("SYMPHONY_TASKS_FILE"),
                   help="Path to tasks.json (default: $SYMPHONY_TASKS_FILE)")
    p.add_argument("--team", default=os.environ.get("SYMPHONY_TEAM_KEY", DEFAULT_TEAM),
                   help=f"Team key prefix for issue ids (default: {DEFAULT_TEAM})")
    p.add_argument("--force", action="store_true",
                   help="Overwrite an existing file (wipes existing comments and PRs)")
    args = p.parse_args()

    if not args.file:
        sys.exit("seed-local: no path resolved. Pass --file or set SYMPHONY_TASKS_FILE.")
    target = Path(args.file).expanduser().resolve()

    if target.exists() and target.stat().st_size > 0 and not args.force:
        print(f"==> {target} already exists; merging in any new issues by title.")
        existing = json.loads(target.read_text())
        existing.setdefault("issues", [])
        existing.setdefault("version", 1)
        existing.setdefault("team", args.team)
        existing.setdefault("project", PROJECT_NAME)
        existing.setdefault("next_comment_id", 1)
        existing.setdefault("next_pr_number", 1)
        by_title = {i["title"]: i for i in existing["issues"]}
        new_doc = build_doc(args.team)
        added = 0
        for issue in new_doc["issues"]:
            if issue["title"] in by_title:
                continue
            existing["issues"].append(issue)
            added += 1
            print(f"  ✓ added {issue['identifier']} [{issue['state']}]: {issue['title']}")
        target.write_text(json.dumps(existing, indent=2) + "\n")
        print(f"\nDone. {added} new issue(s) added; {len(by_title)} kept as-is.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = build_doc(args.team)
        target.write_text(json.dumps(doc, indent=2) + "\n")
        for issue in doc["issues"]:
            print(f"  ✓ created {issue['identifier']} [{issue['state']}]: {issue['title']}")
        print(f"\nDone. Wrote {len(doc['issues'])} issue(s) to {target}.")

    print()
    print("Stage 1 (Todo): Symphony picks up unblocked issues on its next tick.")
    print("Stage 2 (Backlog): drag these to Todo when stage 1 is Done — they're")
    print("the second act of the demo (pro view, SSE, OpenAPI, agent-book, etc).")
    print()
    print(f"  tasks list                    # see issue states")
    print(f"  tasks update-state ENG-9 --state Todo   # promote a Backlog issue")


if __name__ == "__main__":
    main()
