# Stage 2 — Search Read Path + Country Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve catalogue search from OpenSearch, kept in sync by a transactional outbox, with degraded search as an explicit tested state, and render the country's lens on a map from its own boundary data.

**Architecture:** Ingest/curation write `search_outbox` rows in the same Postgres transaction as the catalogue change (at-least-once, ADR-0009 pattern from the predecessor). A `fhrs-search` sync job drains the outbox into a versioned OpenSearch index behind an alias; full reindex is the recovery path. The API reads search from OpenSearch only — stats keep coming from the Stage-0/1 snapshot (N6) — and reports `degraded` honestly when the index is unavailable. The map is the profile's `boundary.geojson` plus a grid-aggregated sample-points GeoJSON endpoint, rendered by vendored Leaflet (no build step, no tile server).

**Tech Stack:** FastAPI, SQLAlchemy 2, alembic (explicit ops only), OpenSearch 2.19 (`opensearch-py>=2.8`), digest-pinned compose service, vanilla JS + Leaflet (vendored, pinned).

**Spec:** `PROMPT.md` (Stage 2 line, N6 read-model rule, N7 trunk rule, N9 ops guardrails). Reference for the sync pattern: the predecessor's ADR-0009 (transactional outbox) at `/home/alarawms/dev/seqdb/docs/adr/0009-search-outbox.md`.

## Global Constraints

- Python `>=3.12`, ruff line-length 100; `fhrs.rules` stays pure (no I/O, no settings).
- Migrations are explicit `op.*` calls only — no autogenerate; the drift-guard integration test must stay green (Stage-0 rule, scar b048395).
- Only new runtime dependency: `opensearch-py>=2.8`. Web keeps no build step; third-party JS is vendored under `web/vendor/` with its version recorded.
- OpenSearch image pinned by digest in `docker-compose.yml` (predecessor convention: `opensearchproject/opensearch:2.19.1@sha256:72fe2fc84be8295906b8efca020b46c58ed45c8da60cd9b8b49e1991e38e89a4`).
- Trunk-based (N7): one short-lived branch `feat/stage2-search`, CI gates merge; deploy only from `main`.
- Aggregates come only from the stats snapshot (N6): search results never feed counts; degraded search must not degrade `/api/v1/stats`.
- Import layers (`pyproject.toml`): insert `fhrs.search` between `fhrs.stats` and `fhrs.connectors`. `fhrs.search` may import `fhrs.core` and `fhrs.rules` only; `fhrs.api` may import `fhrs.search`; harvest/curation never import `fhrs.search` — they write outbox rows via `fhrs.core.models` alone.

## Review Focus

Inputs/failure modes the spec implies that are most likely to bite; each is pinned by a test in the owning task:

1. **Visibility flip after indexing** — a sample approved out of curation (or rejected into it) must appear/disappear from public search; a stale public doc is a correctness breach. → Task 2 + Task 4 tests.
2. **Alias growth after indexing** — a merge that adds an accession after the doc was indexed must still find the sample by the new accession. → Task 4 test (doc is rebuilt from DB at drain time).
3. **OpenSearch down at API startup** — `/config`, `/api/v1/stats`, `/api/v1/samples` must keep serving and `/api/v1/search` must answer 503 degraded, never hang or crash. → Task 5 tests (short client timeout, per-request health check).
4. **Reindex racing a harvest** — outbox rows written during a reindex must not be lost; alias flip must be atomic. → Task 4 test (drain runs after flip; reindex then drain leaves zero unprocessed rows).
5. **Points grid edge cases** — samples with `lat` set but `lon` NULL (columns are independently nullable), and cells at extreme coordinates, must never produce invalid GeoJSON or crash the aggregation. → Task 6 test.

---

### Task 1: `search_outbox` migration + model

**Files:**
- Create: `migrations/versions/0004_search_outbox.py`
- Modify: `src/fhrs/core/models.py` (append `SearchOutbox`)
- Test: `tests/integration/test_stack_pg.py` (extend drift-guard table list)

**Interfaces:**
- Produces: `fhrs.core.models.SearchOutbox` with columns `id: int PK`, `sample_id: int FK samples.id`, `op: str(16)` (only `"upsert"` this stage), `attempts: int = 0`, `processed: bool = False`, `last_error: str | None`, `created_at: DateTime(tz)`, `processed_at: DateTime(tz) | None`. Index on `(processed, id)`.

- [ ] **Step 1: Write the failing integration assertion** — extend the truncate-table list in `tests/integration/test_stack_pg.py` with `"search_outbox"` and add `test_outbox_table_exists` querying `SELECT count(*) FROM search_outbox`.
- [ ] **Step 2: Run it against the alembic-built schema (`alembic upgrade head` on compose db); expect FAIL (table missing).**
- [ ] **Step 3: Write `0004_search_outbox.py` with explicit `op.create_table`/`op.create_index` matching the model exactly; add the model class.**
- [ ] **Step 4: `docker compose up -d db && .venv/bin/alembic upgrade head && .venv/bin/pytest tests/integration -q` — PASS, drift guard green.**
- [ ] **Step 5: Commit** `feat(search): search_outbox table + model (0004)`.

### Task 2: Ingest + curation emit outbox rows

**Files:**
- Modify: `src/fhrs/harvest/ingest.py` (`apply_record` tail), `src/fhrs/curation/service.py` (on visibility-changing transitions)
- Test: `tests/unit/test_outbox.py`

**Interfaces:**
- Consumes: `SearchOutbox` (Task 1).
- Produces: `emit_outbox(session, sample_id: int, op: str = "upsert") -> None` in `src/fhrs/harvest/ingest.py`, called (a) at the end of every `apply_record` that returned a sample, and (b) from curation `propose()` whenever an applied transition changed `sample.visibility`.

- [ ] **Step 1: Write failing tests** — ingesting the six-fixture corpus creates exactly 5 outbox rows (one per in-scope event; fixture 03 merges into an existing sample but still emits for it) and none for the dropped record (no sample); re-ingesting the same payload creates no new row (no `apply_record` call); an `APPROVE_GEO` curation transition on the Tier-C fixture creates a row. Assert `op == "upsert"`, `processed is False`.
- [ ] **Step 2: Run — FAIL (`emit_outbox` missing).**
- [ ] **Step 3: Implement `emit_outbox` + the two call sites.** Emission is a plain `session.add` — same transaction as the catalogue change, which is the whole point (ADR-0009).
- [ ] **Step 4: Run — PASS; then full `pytest tests/unit tests/property -q` stays green.**
- [ ] **Step 5: Commit** `feat(search): outbox emission from ingest + curation (N1-atomic)`.

### Task 3: `fhrs.search` package — client, mapping, index lifecycle + compose service

**Files:**
- Create: `src/fhrs/search/__init__.py`, `src/fhrs/search/client.py`, `src/fhrs/search/index.py`, `search/mappings/samples-v1.json`
- Modify: `pyproject.toml` (dep `opensearch-py>=2.8`, import-linter layers), `src/fhrs/core/config.py` (`opensearch_url: str | None = None` via `FHRS_OPENSEARCH_URL`), `docker-compose.yml` (add the `opensearch` service — defined here because Task 4's integration test consumes it)
- Test: `tests/unit/test_search_index.py`

**Interfaces:**
- Produces:
  - `INDEX_ALIAS = "samples"`, `INDEX_VERSION = "samples-v1"`.
  - `class SearchClient:` wraps `opensearchpy.OpenSearch`, constructor `(url: str, timeout: float = 2.0, transport: Any | None = None)`; methods `healthy() -> bool`, `ensure_index() -> None` (create versioned index + alias if absent), `bulk(actions: list[dict]) -> tuple[int, list[str]]` (returns indexed count + error ids), `delete(ids: list[str]) -> None`, `flip_alias(new_index: str) -> None` (atomic `aliases` action), `doc_count() -> int`, `fs_used_percent() -> float | None` (`_cluster/stats`).
  - `sample_to_doc(row: dict) -> dict` — pure function mapping a DB row to the index document (see mapping fields below; `coords` = `{"lat": float, "lon": float}` only when both non-null).
  - Mapping fields (samples-v1): `canonical_accession` kw, `aliases` kw, `tier` kw, `content_bucket` kw, `checklist_id` kw, `scientific_name` text+kw, `center_name` text+kw, `country_raw` kw, `location` text, `coords` geo_point, `collection_date` date (`yyyy-MM-dd||yyyy-MM||yyyy`, `ignore_malformed`), `marine_eez` bool, `study_accession` kw, `study_title` text, `indexed_at` date.

- [ ] **Step 1: Write failing tests** with an injected fake transport: `ensure_index` creates only when missing; `flip_alias` issues one atomic `aliases.update`; `sample_to_doc` on a row with `lat=NULL, lon=NULL` omits `coords`; a row with only `lat` set also omits it (Review Focus 5); malformed `collection_date` strings pass through unchanged (index tolerates via `ignore_malformed`).
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement client + `sample_to_doc` + the mapping JSON.** Client timeout default 2.0s (the API read path must fail fast, Review Focus 3). Compose service: `opensearch` with `discovery.type=single-node`, security plugin disabled, `OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m`, healthcheck on `/_cluster/health`, named volume `osdata`, digest-pinned image (Global Constraints).
- [ ] **Step 4: Run — PASS; `lint-imports` green with the updated layers contract; live smoke: `docker compose up -d opensearch` healthy and `SearchClient("http://localhost:9200").healthy()` is true.**
- [ ] **Step 5: Commit** `feat(search): OpenSearch client, samples-v1 mapping, index lifecycle, compose service`.

### Task 4: Outbox drain + reindex (`fhrs-search` CLI)

**Files:**
- Create: `src/fhrs/search/sync.py`, `src/fhrs/search/cli.py`
- Modify: `pyproject.toml` (script `fhrs-search = "fhrs.search.cli:main"`)
- Test: `tests/unit/test_search_sync.py`, `tests/integration/test_search_pg.py`

**Interfaces:**
- Consumes: `SearchOutbox` (T1), `SearchClient`, `sample_to_doc` (T3).
- Produces:
  - `drain_outbox(session, client, batch: int = 500) -> dict` — fetch unprocessed rows (id order, limit), last-op-wins dedupe per `sample_id`, read current sample state, index docs for `visibility == "public"` / delete others, mark processed; on bulk error increment `attempts` and store `last_error`; rows with `attempts >= 5` stay unprocessed (operator inspection, ADR-0009). Returns `{indexed, deleted, failed, stalled}`.
  - `reindex(session, client) -> dict` — create `samples-v{n+1}`, bulk all public samples from DB, `flip_alias`, drop old version, then `drain_outbox` (Review Focus 4). Returns `{index, docs}`.
  - CLI: `fhrs-search sync`, `fhrs-search reindex`, `fhrs-search status` (prints `{alias, index, docs, outbox_pending}`).

- [ ] **Step 1: Write failing unit tests** (fake client): drain dedupes two rows for one sample (last wins); a sample that flipped to `visibility="curation"` after outbox emission is deleted, not indexed (Review Focus 1); a sample that gained an alias after emission is indexed with the new alias (Review Focus 2); a bulk error increments `attempts` and leaves the row unprocessed; the 5th failure stalls the row.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `drain_outbox` / `reindex` / CLI.**
- [ ] **Step 4: Integration against compose Postgres + compose OpenSearch: ingest fixture corpus → `fhrs-search reindex` → `status` shows docs == 4 public samples; then drain is a no-op. PASS.**
- [ ] **Step 5: Commit** `feat(search): outbox drain, reindex, fhrs-search CLI`.

### Task 5: API read path with degraded state + scheduler/doctor wiring

**Files:**
- Modify: `docker-compose.yml` (`FHRS_OPENSEARCH_URL=http://opensearch:9200` + `depends_on` on `api` and `scheduler`), `src/fhrs/api/main.py` (`/api/v1/search`, `/api/v1/search/health`), `src/fhrs/harvest/cli.py` (scheduler runs search sync after harvest, before stats build), `src/fhrs/core/doctor.py` (search checks), `.github/workflows` (opensearch service container for the integration job)
- Test: `tests/unit/test_api_search.py`, `tests/integration/test_search_pg.py` (extend)

**Interfaces:**
- Consumes: `SearchClient` (T3), `drain_outbox` (T4).
- Produces:
  - `GET /api/v1/search` params: `q` (multi_match over `canonical_accession^3, aliases^3, scientific_name, center_name, study_title, location`), `tier`, `content_bucket`, `marine_eez: bool`, `has_coords: bool`, `bbox=minlon,minlat,maxlon,maxlat`, `from_` (`ge=0`), `size` (`ge=1, le=100`). Response `{total, hits: [...], degraded: false}`.
  - `GET /api/v1/search/health` → `{status: "ok"|"degraded", index, docs, outbox_pending}`.
  - Degraded contract: client unreachable, `healthy()` false, or alias missing → `503 {"detail": "search degraded", "degraded": true}`; every other endpoint unaffected (Review Focus 3).
  - Doctor (N9): warns on unreachable search, missing alias, `fs_used_percent() >= settings.disk_watermark_pct`, and doc-count vs samples-count drift > 1%.

- [ ] **Step 1: Write failing API tests** (app built with a fake `SearchClient` factory): query passthrough shapes each filter into the expected OpenSearch bool query (assert on the captured body); `bbox` produces a `geo_bounding_box` filter; degraded client → 503 with `degraded: true` while `/api/v1/stats` still 200; health endpoint reports `outbox_pending`.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement endpoints + env wiring + scheduler hook + doctor checks.**
- [ ] **Step 4: Integration: `docker compose up -d db opensearch` + CI service container for OpenSearch in the integration job (same pinned image) — `test_search_pg` PASS; CI green on the branch.**
- [ ] **Step 5: Commit** `feat(search): API read path, degraded state, compose service, doctor checks`.

### Task 6: Country map — boundary + grid points + web

**Files:**
- Create: `web/vendor/leaflet/` (pinned Leaflet 1.9.4 js+css+images, fetched once and committed), `tests/unit/test_map_api.py`
- Modify: `src/fhrs/api/main.py` (`/api/v1/map/boundary`, `/api/v1/map/points`), `web/index.html`, `web/app.js`, `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: profile `boundary.geojson` (existing `CountryProfile.data_path`), DB samples (Stage 0/1 models).
- Produces:
  - `GET /api/v1/map/boundary` → the profile's boundary GeoJSON (`FileResponse`, cache header 1h).
  - `GET /api/v1/map/points?cell=0.5` → GeoJSON FeatureCollection; one feature per occupied `cell`° grid cell: `{type: Feature, geometry: {type: Point, coordinates: [lon, lat]}, properties: {count, marine}}`, over public samples where **both** `lat` and `lon` are non-null (Review Focus 5), aggregated in SQL (`floor(lon/cell)`, `floor(lat/cell)`, `count(*)`, `count(*) filter marine_eez`), `cell` constrained to `{0.1, 0.25, 0.5, 1.0}` default 0.5. Cached in-process keyed by the latest `StatsSnapshot.id` (invalidates when stats rebuild).
  - Web: map section in `index.html`; `app.js` initialises Leaflet with OSM raster basemap, overlays `/api/v1/map/boundary` (land + EEZ styled per tier meaning), adds one circle marker per grid cell scaled by `count`; when `/api/v1/search/health` reports degraded, shows a "search degraded — stats unaffected" banner (the banner element already has the stats-error pattern to follow).

- [ ] **Step 1: Write failing API tests**: fixture corpus (2 coordinated public samples) → `/api/v1/map/points` returns cells with exact counts; a sample with only `lat` set is excluded; `cell=0.05` → 422; boundary endpoint returns the profile's two features (`land_and_territorial_waters`, `marine_eez`).
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement endpoints, vendor Leaflet, wire the map + degraded banner.**
- [ ] **Step 4: Run — PASS; `pytest tests/unit tests/property -q` green; manual smoke: `docker compose up -d --build` → `http://localhost:8081/` shows the map with boundary + points from live data.**
- [ ] **Step 5: Commit** `feat(map): boundary + grid points endpoints, Leaflet map, degraded banner`.

### Task 7: Ops docs + close-out

**Files:**
- Modify: `README.md` (search + map sections), `AGENTS.md` (commands: `fhrs-search sync|reindex|status`), `docs/operator/search.md` (new: reindex runbook, stalled-outbox inspection, disk watermark response)
- Test: none (docs only) — but the whole suite + CI must be green before merge.

- [ ] **Step 1: Write the docs; verify every command in them runs against the compose stack.**
- [ ] **Step 2: Full gate: `ruff check`, `ruff format --check`, `lint-imports`, `pytest tests/unit tests/property -q`, `pytest tests/integration -q`, CI green.**
- [ ] **Step 3: PR + squash-merge to `main` (N7); record merge SHA in this plan's checkbox.**

---

## Decisions already made (don't reopen without new evidence)

- **Outbox over direct indexing** — ingest stays synchronous-DB-only; search catches up asynchronously with retry + reindex recovery (ADR-0009, proven in the predecessor).
- **Drain reads current state** — outbox rows carry no payload; last-op-wins + read-through collapses visibility flips and alias growth correctly (Review Focus 1–2).
- **Grid-aggregation GeoJSON, not a tile server** — ~30–60k coordinated samples aggregate to a few thousand cells; honest and small. Escape hatch documented: if client weight ever hurts, switch `/api/v1/map/points` to emit PMTiles built offline by a pinned tippecanoe image (the predecessor's Planetiler/gcc-states path is the reference, including the "no standalone saudi-arabia extract" gotcha).
- **Stats never come from the index (N6)** — degraded search never degrades the numbers page.

## Self-review notes

- Spec coverage: search read path (T3–T5), map from boundary (T6), degraded first-class tested (T5 steps + Review Focus 3), N9 ops (doctor + runbook in T5/T7), N6/N7 (constraints, T7).
- Type consistency: `emit_outbox(session, sample_id, op)`, `drain_outbox(session, client, batch)`, `reindex(session, client)`, `SearchClient` methods, and endpoint contracts are used consistently across tasks.
- Proportion: six TDD tasks + one docs task for a two-sentence spec line is justified by the four subsystems touched (DB, sync, API, web); bodies are omitted except where the signature doesn't determine them. Ordering: the compose OpenSearch service lands in Task 3 so Task 4's integration step can consume it.
