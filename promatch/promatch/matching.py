"""Matching + quote simulation.

When a request is created, mock pros in the same category produce quotes within
a few "seconds" of the request being submitted. We don't run real timers — the
simulator runs synchronously and writes a small batch of quotes per request,
so the demo is deterministic.
"""

from __future__ import annotations

import random
from typing import List, Sequence

from .db import connect


def _zip_distance(a: str, b: str) -> int:
    """Cheap stand-in for geo distance: how many leading chars differ."""
    if a == b:
        return 0
    common = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        common += 1
    return max(1, 5 - common)


def matching_pros(category_slug: str, zip_code: str, *, limit: int = 4) -> List[dict]:
    """Return the top-N pros for a (category, zip), nearest+highest-rated first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pros WHERE category_slug = ?",
            (category_slug,),
        ).fetchall()
    scored = []
    for row in rows:
        d = _zip_distance(zip_code, row["zip"])
        # Lower is better: distance penalty - rating bonus.
        score = d * 1.0 - row["rating"] * 0.5
        scored.append((score, dict(row)))
    scored.sort(key=lambda t: t[0])
    return [p for _, p in scored[:limit]]


def generate_quotes(request_id: int, *, seed: int | None = None) -> int:
    """Generate quotes from matching pros for the given request.

    Returns the number of quotes written. The price model: the pro's base rate
    plus a randomized markup, with a small chance of going above budget.
    """
    rng = random.Random(seed if seed is not None else request_id)

    with connect() as conn:
        req = conn.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id,)
        ).fetchone()
        if req is None:
            return 0

    pros = matching_pros(req["category_slug"], req["zip"], limit=4)
    written = 0
    with connect() as conn:
        for pro in pros:
            base = pro["base_rate_cents"]
            # +/- 30% jitter, then a small chance of a budget-busting bid
            jitter = rng.uniform(0.7, 1.3)
            price = int(base * jitter)
            eta = rng.randint(2, 48)
            message = _quote_message(rng, req, pro)
            conn.execute(
                "INSERT INTO quotes(request_id, pro_id, price_cents, eta_hours, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (request_id, pro["id"], price, eta, message),
            )
            written += 1
    return written


_TEMPLATES: Sequence[str] = (
    "Happy to help. I can be there in {eta}h. — {pro_name}",
    "Available this week. {eta}h job, mostly. Cash or Venmo OK.",
    "Easy fix, I've done dozens of these. Quote includes parts.",
    "Booked solid today, but I can swing by in {eta}h.",
    "Fair price for the work. Two-year guarantee on labor.",
)


def _quote_message(rng: random.Random, req: dict, pro: dict) -> str:
    template = rng.choice(_TEMPLATES)
    return template.format(eta=rng.randint(2, 48), pro_name=pro["name"])
