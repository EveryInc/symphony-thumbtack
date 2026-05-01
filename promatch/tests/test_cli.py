"""Smoke tests for the CLI. Use a temp DB so we don't touch the user's data."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from promatch.cli import main


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "promatch.db"
    monkeypatch.setenv("PROMATCH_DB", str(db_path))
    yield db_path


def _run(args, *, input=None):
    return CliRunner().invoke(main, args, input=input, catch_exceptions=False)


def test_init_seed_categories_pros(isolated_db):
    assert _run(["init"]).exit_code == 0
    assert _run(["seed"]).exit_code == 0

    cats = _run(["categories", "--json"])
    assert cats.exit_code == 0
    assert any(c["slug"] == "handyman" for c in json.loads(cats.output))

    pros = _run(["pros", "--category", "handyman", "--json"])
    assert pros.exit_code == 0
    handyman_pros = json.loads(pros.output)
    assert len(handyman_pros) >= 2
    assert all(p["category_slug"] == "handyman" for p in handyman_pros)


def test_full_booking_flow(isolated_db):
    _run(["seed"])
    create = _run([
        "request", "Assemble an IKEA Pax wardrobe",
        "-c", "furniture-assembly", "-z", "94103", "-b", "200", "--json",
    ])
    assert create.exit_code == 0
    payload = json.loads(create.output)
    assert payload["status"] == "matched"
    assert len(payload["quotes"]) >= 1
    request_id = payload["id"]

    quotes = json.loads(_run(["quotes", str(request_id), "--json"]).output)
    cheapest = quotes[0]
    accepted = json.loads(_run(["accept", str(cheapest["id"]), "--json"]).output)
    assert accepted["status"] == "booked"
    assert sum(1 for q in accepted["quotes"] if q["status"] == "accepted") == 1
    assert sum(1 for q in accepted["quotes"] if q["status"] == "declined") == len(quotes) - 1


def test_cancel_request(isolated_db):
    _run(["seed"])
    payload = json.loads(_run([
        "request", "deep clean studio apt",
        "-c", "cleaning", "-z", "94103", "-b", "150", "--json",
    ]).output)
    rid = payload["id"]
    cancel = _run(["cancel", str(rid)])
    assert cancel.exit_code == 0

    listing = json.loads(_run(["list", "--status", "cancelled", "--json"]).output)
    assert any(r["id"] == rid for r in listing)


def test_unknown_category_rejected(isolated_db):
    _run(["seed"])
    bad = _run([
        "request", "x", "-c", "nope-not-real", "-z", "94103", "-b", "100",
    ])
    assert bad.exit_code != 0
    assert "unknown category" in bad.output
