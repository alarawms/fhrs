# AGENTS.md — FHRS

Governing spec: **PROMPT.md** (non-negotiables N1–N9). Read it before changing
ingest, stats, curation, or country handling. If a change invalidates something this
file documents, update this file in the same commit.

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
- Tests (fast): `.venv/bin/pytest tests/unit tests/property -q`
- Tests (Postgres integration): `docker compose up -d db && .venv/bin/pytest tests/integration -q`
- Stack: `docker compose up -d --build` (db + api + web at :8081)
- Demo: `FHRS_DATABASE_URL=sqlite:///./db/demo.sqlite .venv/bin/python scripts/demo_ingest.py`
- Doctor (startup checks, N9): `.venv/bin/fhrs-doctor`
- Stats read model rebuild (N6): `.venv/bin/fhrs-stats build`
- Replay payloads locally after a parser change (N2): `.venv/bin/fhrs-harvest replay`

## The one switch

Root `.env` → `FHRS_COUNTRY_CODE`. Everything country-specific lives in
`countries/<code>/` + `countries/<code>.yaml` — never in Python. Add a country by
copying `countries/template.yaml` and its data directory; no code changes allowed.

## Rules of the house

- `fhrs.rules` is pure: no I/O, no settings access. Heavily property-tested.
- Null never overwrites non-null in merges (N1) — property test must stay green.
- Aggregates come only from the stats snapshot (N6). Do not add ad-hoc counts to the API.
- Curation actions pass code guards (N8); prompt text is not a safety boundary.
- Trunk-based: short-lived branches onto `main`, deploy only from `main` (N7).
