#!/usr/bin/env python3
"""Seed the configured Linear project with the promatch demo issues.

Reads LINEAR_API_KEY, LINEAR_PROJECT_SLUG, LINEAR_TEAM_KEY from the environment
(loaded from config.env via bootstrap.sh, or sourced manually). Idempotent:
issues are matched by title before creation, so re-running won't duplicate.

Stdlib only — no pip install needed.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import urllib.error
import urllib.request

API = "https://api.linear.app/graphql"


# ─────────────────────────────────────────────────────────────────────────────
# The demo issues. Each one is a buildable, testable slice of the dashboard.
# Audience watches them go from Todo → ... → Done and the dashboard appear.
# ─────────────────────────────────────────────────────────────────────────────

ISSUES = [
    {
        "title": "Stand up a basic FastAPI dashboard server",
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
        "title": "Show quotes per request on the dashboard",
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
        "body": textwrap.dedent("""\
            Right now you can only accept a quote via the CLI. Add a button on
            each pending quote that books the pro.

            ## Acceptance criteria

            - Each pending quote on `/` has an "Accept" button.
            - Button submits a `POST /quotes/<id>/accept` form (HTML form, no JS
              required for the demo).
            - On success, redirect back to `/` (303 See Other).
            - The endpoint must reuse the existing `cli.cmd_accept` logic — do
              NOT duplicate the booking transaction. Refactor the booking logic
              out of `cli.py` into a new `promatch/services.py` module that
              both the CLI command and the route call into.
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

            Read the rendered README on GitHub. Fresh eyes should be able to
            spin up the dashboard from the README alone.
        """),
    },
]


# ─────────────────────────────────────────────────────────────────────────────


def gql(api_key: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        API,
        data=body,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Linear API HTTP {e.code}: {e.read().decode()}")
    if "errors" in payload:
        sys.exit(f"Linear API errors: {json.dumps(payload['errors'], indent=2)}")
    return payload["data"]


def find_team(api_key: str, key: str) -> dict:
    data = gql(
        api_key,
        "query($k: String!) { teams(filter: {key: {eq: $k}}) { nodes { id key name } } }",
        {"k": key},
    )
    nodes = data["teams"]["nodes"]
    if not nodes:
        sys.exit(f"No team with key {key!r} — check LINEAR_TEAM_KEY in config.env")
    return nodes[0]


def find_project(api_key: str, slug: str) -> dict:
    """Look up a project by its slug (the random suffix in the URL)."""
    # Linear lets us filter by slugId (the URL slug).
    data = gql(
        api_key,
        """
        query($s: String!) {
          projects(filter: { slugId: { eq: $s } }) {
            nodes { id name slugId }
          }
        }
        """,
        {"s": slug},
    )
    nodes = data["projects"]["nodes"]
    if nodes:
        return nodes[0]

    # Fall back to substring match (helps if the user pasted the full URL tail).
    data = gql(
        api_key,
        """
        query($s: String!) {
          projects(filter: { slugId: { contains: $s } }) {
            nodes { id name slugId }
          }
        }
        """,
        {"s": slug.split("-")[-1]},
    )
    nodes = data["projects"]["nodes"]
    if not nodes:
        sys.exit(
            f"No Linear project matching slug {slug!r}. "
            "Open the project in your browser and copy the trailing slug from the URL."
        )
    if len(nodes) > 1:
        sys.exit(
            "Multiple projects matched. Use the full slugId from the project URL "
            "in LINEAR_PROJECT_SLUG."
        )
    return nodes[0]


def find_state(api_key: str, team_id: str, name: str) -> dict | None:
    data = gql(
        api_key,
        """
        query($t: ID!) {
          workflowStates(filter: { team: { id: { eq: $t } } }) {
            nodes { id name type }
          }
        }
        """,
        {"t": team_id},
    )
    for s in data["workflowStates"]["nodes"]:
        if s["name"].lower() == name.lower():
            return s
    return None


def list_existing_titles(api_key: str, project_id: str) -> set[str]:
    data = gql(
        api_key,
        """
        query($p: ID!) {
          issues(filter: { project: { id: { eq: $p } } }, first: 100) {
            nodes { id title }
          }
        }
        """,
        {"p": project_id},
    )
    return {n["title"] for n in data["issues"]["nodes"]}


def create_issue(api_key: str, team_id: str, project_id: str, state_id: str | None,
                 title: str, body: str) -> dict:
    payload = {
        "title": title,
        "description": body,
        "teamId": team_id,
        "projectId": project_id,
    }
    if state_id:
        payload["stateId"] = state_id
    data = gql(
        api_key,
        """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier title url state { name } }
          }
        }
        """,
        {"input": payload},
    )
    if not data["issueCreate"]["success"]:
        sys.exit(f"Failed to create issue: {title}")
    return data["issueCreate"]["issue"]


def main() -> None:
    api_key = os.environ.get("LINEAR_API_KEY", "")
    project_slug = os.environ.get("LINEAR_PROJECT_SLUG", "")
    team_key = os.environ.get("LINEAR_TEAM_KEY", "")

    missing = [n for n, v in [
        ("LINEAR_API_KEY", api_key),
        ("LINEAR_PROJECT_SLUG", project_slug),
        ("LINEAR_TEAM_KEY", team_key),
    ] if not v or "REPLACE_ME" in v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}. Source config.env first.")

    print(f"Looking up team {team_key!r}...")
    team = find_team(api_key, team_key)
    print(f"  → {team['name']} ({team['id']})")

    print(f"Looking up project (slug {project_slug!r})...")
    project = find_project(api_key, project_slug)
    print(f"  → {project['name']} ({project['id']})")

    print("Looking up the 'Todo' workflow state...")
    todo = find_state(api_key, team["id"], "Todo")
    if todo is None:
        print("  ⚠ no 'Todo' state on this team — issues will land in the team default")
    else:
        print(f"  → {todo['id']}")

    existing = list_existing_titles(api_key, project["id"])
    print(f"\nFound {len(existing)} existing issue(s) in project. Will skip duplicates.\n")

    created, skipped = 0, 0
    for spec in ISSUES:
        if spec["title"] in existing:
            print(f"  ⏭  skip (exists): {spec['title']}")
            skipped += 1
            continue
        issue = create_issue(
            api_key, team["id"], project["id"],
            todo["id"] if todo else None,
            spec["title"], spec["body"],
        )
        print(f"  ✓ created {issue['identifier']}: {issue['title']}")
        print(f"     {issue['url']}")
        created += 1

    print(f"\nDone. {created} created, {skipped} already existed.")
    print("Issues are sitting in 'Todo' — Symphony will pick them up on its next tick.")


if __name__ == "__main__":
    main()
