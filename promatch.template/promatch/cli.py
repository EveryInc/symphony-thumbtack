"""promatch CLI — the surface an AI agent uses to book a handyman.

Every command prints both human-readable output (rich tables) and a `--json`
mode that returns structured data for programmatic consumption. Agents should
use `--json`.
"""

from __future__ import annotations

import json as jsonlib
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .db import connect, db_path, init_db, reset_db
from .matching import generate_quotes
from .seed import seed as seed_data

console = Console()


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _emit(data: Any, *, as_json: bool, table: Table | None = None) -> None:
    if as_json:
        click.echo(jsonlib.dumps(data, indent=2, default=str))
    elif table is not None:
        console.print(table)


@click.group()
@click.version_option(__version__, prog_name="promatch")
def main() -> None:
    """promatch — local pro-lead-matching marketplace."""


# ---------- setup ----------

@main.command("init")
def cmd_init() -> None:
    """Initialize the local database (idempotent)."""
    init_db()
    click.echo(f"db ready at {db_path()}")


@main.command("seed")
def cmd_seed() -> None:
    """Load demo categories + pros."""
    seed_data()
    click.echo("seeded categories + pros")


@main.command("reset")
@click.confirmation_option(prompt="Drop the entire local DB?")
def cmd_reset() -> None:
    """Drop and recreate the local database, then re-seed."""
    reset_db()
    seed_data()
    click.echo("db reset + reseeded")


# ---------- catalog ----------

@main.command("categories")
@click.option("--json", "as_json", is_flag=True)
def cmd_categories(as_json: bool) -> None:
    """List service categories."""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT slug, name FROM categories ORDER BY name")]
    table = Table(title="Categories")
    table.add_column("slug", style="cyan")
    table.add_column("name")
    for r in rows:
        table.add_row(r["slug"], r["name"])
    _emit(rows, as_json=as_json, table=table)


@main.command("pros")
@click.option("--category", "-c", help="Filter by category slug")
@click.option("--zip", "zip_code", "-z", help="Filter by zip code")
@click.option("--json", "as_json", is_flag=True)
def cmd_pros(category: str | None, zip_code: str | None, as_json: bool) -> None:
    """List pros, optionally filtered."""
    sql = "SELECT * FROM pros"
    where, params = [], []
    if category:
        where.append("category_slug = ?")
        params.append(category)
    if zip_code:
        where.append("zip = ?")
        params.append(zip_code)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rating DESC, name"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
    table = Table(title="Pros")
    table.add_column("id", justify="right", style="dim")
    table.add_column("name")
    table.add_column("category", style="cyan")
    table.add_column("zip")
    table.add_column("rating", justify="right")
    table.add_column("base", justify="right")
    for r in rows:
        table.add_row(
            str(r["id"]), r["name"], r["category_slug"], r["zip"],
            f"{r['rating']:.1f}", _money(r["base_rate_cents"]),
        )
    _emit(rows, as_json=as_json, table=table)


# ---------- requests ----------

@main.command("request")
@click.argument("description")
@click.option("--category", "-c", required=True, help="Category slug (see `promatch categories`)")
@click.option("--zip", "zip_code", "-z", required=True, help="5-digit US zip code")
@click.option("--budget", "-b", required=True, type=int, help="Budget in dollars")
@click.option("--no-quotes", is_flag=True, help="Skip quote simulation (advanced)")
@click.option("--json", "as_json", is_flag=True)
def cmd_request(
    description: str, category: str, zip_code: str,
    budget: int, no_quotes: bool, as_json: bool,
) -> None:
    """Create a job request. Returns the request id."""
    with connect() as conn:
        cat = conn.execute(
            "SELECT slug FROM categories WHERE slug = ?", (category,)
        ).fetchone()
        if not cat:
            raise click.ClickException(
                f"unknown category {category!r} — run `promatch categories`"
            )
        cur = conn.execute(
            "INSERT INTO requests(description, category_slug, zip, budget_cents) "
            "VALUES (?, ?, ?, ?)",
            (description, category, zip_code, budget * 100),
        )
        request_id = cur.lastrowid

    if not no_quotes:
        n = generate_quotes(request_id)
        # Move to 'matched' if any quotes came in.
        if n > 0:
            with connect() as conn:
                conn.execute(
                    "UPDATE requests SET status = 'matched' WHERE id = ?",
                    (request_id,),
                )

    payload = _request_detail(request_id)
    _emit(payload, as_json=as_json, table=_request_table(payload))


@main.command("list")
@click.option("--status", "-s", help="open|matched|booked|cancelled")
@click.option("--json", "as_json", is_flag=True)
def cmd_list(status: str | None, as_json: bool) -> None:
    """List your job requests."""
    sql = "SELECT * FROM requests"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
    table = Table(title="Requests")
    table.add_column("id", justify="right", style="dim")
    table.add_column("category", style="cyan")
    table.add_column("zip")
    table.add_column("budget", justify="right")
    table.add_column("status")
    table.add_column("description")
    for r in rows:
        table.add_row(
            str(r["id"]), r["category_slug"], r["zip"],
            _money(r["budget_cents"]), r["status"],
            r["description"][:60],
        )
    _emit(rows, as_json=as_json, table=table)


@main.command("status")
@click.argument("request_id", type=int)
@click.option("--json", "as_json", is_flag=True)
def cmd_status(request_id: int, as_json: bool) -> None:
    """Show full status of a request including all quotes."""
    payload = _request_detail(request_id)
    if payload is None:
        raise click.ClickException(f"request {request_id} not found")
    _emit(payload, as_json=as_json, table=_request_table(payload))


# ---------- quotes ----------

@main.command("quotes")
@click.argument("request_id", type=int)
@click.option("--all", "show_all", is_flag=True, help="Include declined/accepted")
@click.option("--json", "as_json", is_flag=True)
def cmd_quotes(request_id: int, show_all: bool, as_json: bool) -> None:
    """List quotes received for a request."""
    sql = """
        SELECT q.*, p.name AS pro_name, p.rating AS pro_rating
        FROM quotes q JOIN pros p ON p.id = q.pro_id
        WHERE q.request_id = ?
    """
    params: list = [request_id]
    if not show_all:
        sql += " AND q.status = 'pending'"
    sql += " ORDER BY q.price_cents ASC"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
    table = Table(title=f"Quotes for request {request_id}")
    table.add_column("id", justify="right", style="dim")
    table.add_column("pro")
    table.add_column("rating", justify="right")
    table.add_column("price", justify="right")
    table.add_column("eta", justify="right")
    table.add_column("status")
    table.add_column("message")
    for r in rows:
        table.add_row(
            str(r["id"]), r["pro_name"], f"{r['pro_rating']:.1f}",
            _money(r["price_cents"]), f"{r['eta_hours']}h",
            r["status"], r["message"][:50],
        )
    _emit(rows, as_json=as_json, table=table)


@main.command("accept")
@click.argument("quote_id", type=int)
@click.option("--json", "as_json", is_flag=True)
def cmd_accept(quote_id: int, as_json: bool) -> None:
    """Accept a quote. Marks the request as booked and declines all other quotes."""
    with connect() as conn:
        q = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if not q:
            raise click.ClickException(f"quote {quote_id} not found")
        request_id = q["request_id"]
        conn.execute("UPDATE quotes SET status='accepted' WHERE id = ?", (quote_id,))
        conn.execute(
            "UPDATE quotes SET status='declined' WHERE request_id = ? AND id != ? AND status='pending'",
            (request_id, quote_id),
        )
        conn.execute("UPDATE requests SET status='booked' WHERE id = ?", (request_id,))

    payload = _request_detail(request_id)
    _emit(payload, as_json=as_json, table=_request_table(payload))


@main.command("decline")
@click.argument("quote_id", type=int)
def cmd_decline(quote_id: int) -> None:
    """Decline a single quote."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE quotes SET status='declined' WHERE id = ? AND status='pending'",
            (quote_id,),
        )
        if cur.rowcount == 0:
            raise click.ClickException(f"quote {quote_id} not pending or not found")
    click.echo(f"declined quote {quote_id}")


@main.command("cancel")
@click.argument("request_id", type=int)
def cmd_cancel(request_id: int) -> None:
    """Cancel a request that hasn't been booked."""
    with connect() as conn:
        r = conn.execute("SELECT status FROM requests WHERE id = ?", (request_id,)).fetchone()
        if not r:
            raise click.ClickException(f"request {request_id} not found")
        if r["status"] == "booked":
            raise click.ClickException("cannot cancel a booked request")
        conn.execute("UPDATE requests SET status='cancelled' WHERE id = ?", (request_id,))
    click.echo(f"cancelled request {request_id}")


# ---------- helpers ----------

def _request_detail(request_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        if not r:
            return None
        quotes = conn.execute(
            "SELECT q.*, p.name AS pro_name, p.rating AS pro_rating "
            "FROM quotes q JOIN pros p ON p.id = q.pro_id "
            "WHERE q.request_id = ? ORDER BY q.price_cents ASC",
            (request_id,),
        ).fetchall()
        return {
            **dict(r),
            "budget": r["budget_cents"] / 100,
            "quotes": [
                {**dict(q), "price": q["price_cents"] / 100} for q in quotes
            ],
        }


def _request_table(payload: dict | None) -> Table:
    table = Table(show_header=False)
    if payload is None:
        table.add_row("(missing)")
        return table
    table.add_row("id", str(payload["id"]))
    table.add_row("status", payload["status"])
    table.add_row("category", payload["category_slug"])
    table.add_row("zip", payload["zip"])
    table.add_row("budget", _money(payload["budget_cents"]))
    table.add_row("description", payload["description"])
    table.add_row("quotes", str(len(payload["quotes"])))
    return table


if __name__ == "__main__":
    main()
