# FHRS — a national lens on public genomics

FHRS harvests public INSDC records (ENA / NCBI / DDBJ), keeps the records that belong
to **one country**, and serves a trustworthy catalogue of that country's genomic
footprint — built to stay correct when metadata is scarce, inconsistent, or wrong.

The country is a single switch: `FHRS_COUNTRY_CODE` in the root `.env`. Geography,
gazetteer, institution aliases, branding, and languages all follow from
`countries/<code>/`.

## Why

Public archives are global; the questions a country asks of its data are not. National
institutions submit under many names, coordinates arrive in every format INSDC has ever
permitted, and a third of records lack the fields a naive filter needs. FHRS treats
those as first-class engineering constraints (see `PROMPT.md`, non-negotiables N1–N9,
each learned from a production failure in the predecessor system).

## Quick start

```bash
docker compose up -d --build        # db + api; web UI and API on :8081
# or locally:
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests/unit tests/property -q
.venv/bin/python scripts/demo_ingest.py   # fixture payloads → stats snapshot
```

Then open `http://localhost:8081/` — the page renders the active country's lens.

## Add your country

1. `cp countries/template.yaml countries/<CC>.yaml` (ISO alpha-2, lowercase file)
2. Fill it in and create `countries/<cc>/` with the four data files it points at.
3. Set `FHRS_COUNTRY_CODE=<CC>` in `.env`. Restart. That's the whole show.

## Layout

- `PROMPT.md` — the governing build spec (read this first)
- `countries/` — per-country profiles + data (aliases, institutions, gazetteer, boundary)
- `registry/checklists.yaml` — checklist → content-type registry (data, coverage-tested)
- `src/fhrs/{rules,core,connectors,harvest,stats,curation,api}` — enforced boundaries
- `tests/{unit,property,integration}` — invariants from PROMPT.md §Acceptance
