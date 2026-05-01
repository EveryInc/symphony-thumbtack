# promatch

A local-only Thumbtack-style pro lead matching marketplace. AI agents post job
requests, mock pros respond with quotes, agents accept the best one.

Backed by SQLite. No external APIs. No keys. `pip install -e .` and go.

## Install

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## CLI quickstart

```sh
promatch seed                                                      # load demo catalog
promatch categories                                                # list service categories
promatch request "Mount a flat-screen TV" -c handyman -z 94110 -b 200
# -> creates request 1, simulator generates ~4 quotes from matching pros

promatch quotes 1                                                  # see quotes
promatch accept <quote-id>                                         # book the pro
promatch status 1                                                  # final state
```

Every command supports `--json` for agent consumption.

## Database

A single SQLite file at `~/.promatch/promatch.db` (override with `$PROMATCH_DB`).
`promatch reset` drops + reseeds.

## Tests

```sh
pytest
```

## What's NOT here yet

A web dashboard. That's coming via the engineering issues this repo's owner has
queued up — see Linear.
