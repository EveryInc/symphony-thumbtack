"""Seed pros + categories. Idempotent: re-running just updates the catalog."""

from __future__ import annotations

from .db import connect, init_db

CATEGORIES = [
    ("plumbing", "Plumbing"),
    ("electrical", "Electrical"),
    ("handyman", "Handyman"),
    ("cleaning", "House Cleaning"),
    ("moving", "Moving Help"),
    ("furniture-assembly", "Furniture Assembly"),
    ("painting", "Interior Painting"),
    ("yardwork", "Yard Work"),
]

# Mock pros across the SF Bay Area + neighbors. Realistic enough for demos.
PROS = [
    # plumbing
    ("Reliable Plumbing Co.",      "plumbing",            "94103", 4.8, 12000),
    ("Mike's Pipe Service",        "plumbing",            "94110", 4.6,  9500),
    ("South Bay Plumbers",         "plumbing",            "95110", 4.9, 14000),
    # electrical
    ("Bright Spark Electric",      "electrical",          "94103", 4.7, 13000),
    ("Volt & Co.",                 "electrical",          "94117", 4.5, 11000),
    # handyman
    ("Jose's Handyman Services",   "handyman",            "94103", 4.9,  7500),
    ("Bay Area Fix-It",            "handyman",            "94110", 4.4,  6500),
    ("Quick Fix Crew",             "handyman",            "94158", 4.6,  7000),
    ("North Bay Handyman",         "handyman",            "94901", 4.8,  8500),
    # cleaning
    ("Sparkle Clean SF",           "cleaning",            "94103", 4.8,  6000),
    ("Maria's House Cleaning",     "cleaning",            "94110", 4.9,  5500),
    # moving
    ("Two Guys & a Truck (SF)",    "moving",              "94103", 4.5, 18000),
    ("Bay Movers Express",         "moving",              "94158", 4.7, 22000),
    # furniture assembly
    ("IKEA Assembly Pros",         "furniture-assembly",  "94103", 4.9,  8000),
    ("Box-to-Done",                "furniture-assembly",  "94110", 4.7,  7000),
    ("Assembly Required",          "furniture-assembly",  "94117", 4.6,  7500),
    # painting
    ("Color Splash Painters",      "painting",            "94103", 4.8, 25000),
    ("True Coat Painting",         "painting",            "94110", 4.6, 22000),
    # yardwork
    ("Green Thumb Landscaping",    "yardwork",            "94103", 4.7, 14000),
    ("Sunset Yard Care",           "yardwork",            "94117", 4.5, 12000),
]


def seed() -> None:
    init_db()
    with connect() as conn:
        for slug, name in CATEGORIES:
            conn.execute(
                "INSERT INTO categories(slug, name) VALUES(?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET name=excluded.name",
                (slug, name),
            )
        # Wipe-and-reload pros so the catalog stays in sync with this file.
        conn.execute("DELETE FROM pros")
        conn.executemany(
            "INSERT INTO pros(name, category_slug, zip, rating, base_rate_cents) "
            "VALUES(?, ?, ?, ?, ?)",
            PROS,
        )
