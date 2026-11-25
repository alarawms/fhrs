# FHRS — Build Prompt

You are building **FHRS**, a national lens on public genomics: a system that harvests
INSDC records (ENA / NCBI / DDBJ), keeps the records that belong to one country, and
serves a trustworthy, citable catalogue of that country's genomic footprint.

FHRS is a ground-up rebuild of lessons learned operating a production instance of this
idea. Those lessons are **non-negotiable requirements** below — each exists because its
absence caused a real production failure. Do not defer them to "later".

## The one switch

A root-level `.env` stores `FHRS_COUNTRY_CODE` (ISO 3166-1 alpha-2). That single value
drives everything: geography filters, gazetteer, institution aliases, branding,
languages, and the definition of "national footprint". Changing the country and
restarting must produce a working lens for that country with **zero code changes**.
Country data lives in `countries/<code>/`; the profile schema is documented in
`countries/template.yaml`.

## Non-negotiables (each maps to a production scar)

**N1. Ingest is non-destructive by construction.**
Every source record is an append-only `source_event`. Attributes merge per key with
provenance (source, event, fetched-at). Null/empty **never** overwrites a known value —
this is a property-tested invariant, not a convention. (Scar: harvester upserts erased
known coordinates/dates whenever a weaker payload arrived; discovered months later by
vanishing metadata.)

**N2. Raw payloads are stored; reprocessing is local.**
Every fetched payload lands in the payload store keyed by source/accession/hash before
normalization. Any parser improvement is a `replay` job over local payloads — never a
re-harvest. (Scar: a lat_lon parser fix required re-harvesting two archives to backfill
32k coordinates.)

**N3. Identity resolution is a first-class pipeline stage.**
Accession cross-references resolve **before** a record is visible. The merge graph is
stored and queryable. Every count everywhere derives from canonical records only.
(Scar: late dedup left one assay type at nearly twice its true size in published
snapshots, and dead accession links in the curation UI.)

**N4. No sentinel domain objects.**
A study-less sample has `study_id = NULL`. Never invent placeholder studies
(`NOSTUDY:*`) to satisfy joins. (Scar: 22k synthetic studies leaked into stats, APIs,
and at least one endpoint counted them as real publications.)

**N5. Registries are data, with coverage tests.**
Checklist → content-type mapping is versioned YAML (`registry/checklists.yaml`), not a
dict buried in a router. Ingest of an unmapped checklist is a **loud** event (unmapped
queue + metric + failing coverage test), never a silent fallback bucket.
(Scar: MIMAG/MISAG/DDBJ-package checklist forms were silently bucketed "other" for
months, undercounting every content type.)

**N6. One read model for statistics.**
All aggregates are computed once, by `fhrs-stats build`, into a stats snapshot (table +
`numbers.json` artifact). The API, the web UI, reports, and papers consume the snapshot.
Nothing computes its own ad-hoc aggregate. Numbers in documentation are generated,
never hand-written. (Scar: endpoint/UI/paper computed overlapping-but-divergent numbers;
reconciling them required rewriting a paper.)

**N7. Trunk-based development; deploy only from main.**
Short-lived branches, CI gates merge, the deployed image digest is recorded. Production
behavior must be answerable from `main`. (Scar: the running harvester was built from an
unmerged worktree branch; "what does production do?" had no single answer.)

**N8. Agentic curation: LLM proposes, code disposes.**
The set of allowed curation actions is a code enum with per-action guard predicates. A
proposal that fails its guard is rejected by code regardless of how it was produced.
Safety restrictions never live only in a prompt. (Scar: an auto-approver's action
restrictions were prompt-level; a prompt regression could have widened its blast
radius.)

**N9. Ops guardrails from day one.**
- Disk watermark alerting before the search/db volume floods. (Scar: flood-stage outage
  at 96% disk.)
- Container entrypoints never swallow arbitrary commands (`exec "$@"` pattern); ad-hoc
  ops must not need image rebuilds. (Scar: every one-off job required `--entrypoint`
  workarounds.)
- Config is validated at startup against the deployment profile (`fhrs doctor`); a bad
  default fails fast, not silently at query time. (Scar: an embed-timeout default sized
  for hardware the host didn't have degraded every hybrid search query.)

## Architecture contract

Monorepo, single Python distribution `fhrs`, sub-packages with enforced import
boundaries (import-linter contracts in CI):

- `fhrs.rules` — pure functions, no I/O: coords, geo/inclusion, checklist registry.
  This is the most-tested code in the repo (unit + property tests).
- `fhrs.core` — config (root `.env` → validated settings → country profile), DB
  models/engine, `doctor`.
- `fhrs.connectors` — INSDC payload → normalized record. Only place that knows
  source-specific shapes.
- `fhrs.harvest` — store payload → append event → normalize → dedup → merge →
  stats rebuild; plus `replay`.
- `fhrs.stats` — the single read-model builder.
- `fhrs.curation` — policy enum + guards + proposal service.
- `fhrs.api` — FastAPI; serves `/config`, `/api/v1/stats` (from snapshot), samples,
  and the static web app.

Postgres is the system of record. SQLite is acceptable for unit/property tests only;
integration tests run against Postgres in CI.

## Acceptance invariants (CI must enforce)

1. Property: `merge(existing, incoming)` never returns null/empty for a key where
   `existing` was non-null.
2. Property: `parse_lat_lon` never raises; output is within WGS84 ranges or `None`;
   golden parametrized cases cover every INSDC form seen in the wild.
3. Coverage: every checklist id in fixtures AND in the live catalogue maps to a
   bucket in `registry/checklists.yaml`; unknown → unmapped queue, and the test suite
   fails if the registry and fixtures drift.
4. Dedup: two payloads whose accession sets intersect produce exactly one canonical
   sample; stats count it once.
5. Stats: the snapshot equals an independent recomputation from canonical+visible
   rows (test recomputes with separate SQL).
6. Policy: every `AllowedAction` guard rejects a deliberately illegal proposal.
7. Country switch: a test boots the app config with a second (template) country
   profile and asserts `/config` reflects it with no code change.
8. No sample row exists with a placeholder/sentinel study; `study_id IS NULL` is the
   only study-less representation (enforced by a migration-time constraint/test).

## Stage plan

- **Stage 0 (this scaffold):** everything above, runnable end-to-end with fixture
  payloads; compose stack (db + api + static web); `fhrs doctor`; CI green.
- **Stage 1:** real ENA/NCBI/DDBJ connectors behind the payload store; scheduled
  harvest; incremental cursors.
- **Stage 2:** OpenSearch read path + map tiles from the country boundary; degraded
  search is a first-class, tested state.
- **Stage 3:** curation UI + LLM geo-assist behind the N8 policy guards; audit log.
- **Stage 4:** FAIR/DOI minting, report/paper number generation from `numbers.json`.

## Process rules

Trunk-based (N7). Conventional Commits. Docs and numbers are generated artifacts.
Every stage ships with its tests; a stage is not done while its invariants are red.
