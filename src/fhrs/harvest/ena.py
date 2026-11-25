"""Stage-1 ENA harvest: discovery queries from the active country profile →
whole-set fetch → windowed ingest through the standard pipeline → per-query cursor
checkpoints. Everything the connector learns is a stored payload + source event, so
replays and re-ingests stay idempotent (N1/N2).
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from fhrs.connectors.ena import (
    EnaClient,
    country_queries,
    geo_box_query,
    institution_query,
    parse_date,
    with_since,
)
from fhrs.core.config import CountryProfile
from fhrs.core.models import HarvestState
from fhrs.harvest.ingest import IngestReport, ingest_bytes
from fhrs.rules.checklists import ChecklistRegistry
from fhrs.rules.geo import GeoContext

SOURCE = "ena"
WINDOW = 1000


@dataclass
class HarvestReport:
    queries: int = 0
    fetched: int = 0
    skipped_duplicate_accessions: int = 0
    ingest: IngestReport = field(default_factory=IngestReport)
    per_query: dict[str, dict] = field(default_factory=dict)

    def merge_ingest(self, other: IngestReport) -> None:
        for name in self.ingest.__dataclass_fields__:
            value = getattr(other, name)
            current = getattr(self.ingest, name)
            if isinstance(value, list):
                current.extend(v for v in value if v not in current)
            else:
                setattr(self.ingest, name, current + value)


def discovery_queries(profile: CountryProfile, geo: GeoContext) -> list[tuple[str, str]]:
    """Country queries first (Tier A), then the geo box (Tier B), then institutions
    (Tier C). Query strings come from profile data — nothing KSA-shaped is
    hard-coded here."""

    aliases_doc = yaml.safe_load(profile.data_path("aliases").read_text(encoding="utf-8"))
    aliases = aliases_doc["aliases"]
    ena_queries = aliases_doc.get("ena_queries", {})
    cqs = country_queries(aliases, ena_queries)
    bounds = geo.land.union(geo.eez).bounds
    queries = [*cqs, geo_box_query(bounds, cqs)]
    inst_doc = yaml.safe_load(profile.data_path("institutions").read_text(encoding="utf-8"))
    seen_aliases: set[str] = set()
    for inst in inst_doc["institutions"]:
        for alias in inst["aliases"]:
            if alias.lower() not in seen_aliases:
                seen_aliases.add(alias.lower())
                queries.append((f"center:{alias.lower()}", institution_query(alias)))
    return queries


def harvest_ena(
    session: Session,
    client: EnaClient,
    *,
    profile: CountryProfile,
    geo: GeoContext,
    registry: ChecklistRegistry,
    payload_root: Path,
    full: bool = False,
    only_query: str | None = None,
    max_per_query: int | None = None,
    commit_every: int = WINDOW,
) -> HarvestReport:
    report = HarvestReport()
    seen: set[str] = set()

    for query_key, query in discovery_queries(profile, geo):
        if only_query and query_key != only_query:
            continue
        state = session.get(HarvestState, (SOURCE, query_key))
        if state is None:
            state = HarvestState(source=SOURCE, query_key=query_key, status="running")
            session.add(state)
            session.flush()
        # Incremental: re-run every query scoped to the high-water mark. The mark
        # only advances at query completion, so a crash mid-run re-enters with the
        # same `since` and resumes its window offset against the same row set.
        since = None if full else (state.high_water.date() if state.high_water else None)
        rows = client.search("sample", with_since(query, since))
        if max_per_query is not None:
            rows = rows[:max_per_query]
        report.queries += 1

        high_water: dt.date | None = since
        fetched = ingested = 0
        offset = state.offset if not full else 0
        for window_start in range(offset, len(rows), commit_every):
            for row in rows[window_start : window_start + commit_every]:
                acc = (row.get("sample_accession") or "").strip()
                if not acc:
                    continue
                report.fetched += 1
                fetched += 1
                if acc in seen:
                    report.skipped_duplicate_accessions += 1
                    continue
                seen.add(acc)
                when = parse_date(row.get("last_updated"))
                if when and (high_water is None or when > high_water):
                    high_water = when
                report.merge_ingest(
                    ingest_bytes(
                        session,
                        json.dumps(row, ensure_ascii=False).encode(),
                        SOURCE,
                        geo=geo,
                        registry=registry,
                        payload_root=payload_root,
                        fetched_at=dt.datetime.now(dt.UTC),
                    )
                )
                ingested += 1
            state.offset = window_start + commit_every
            state.status = "running"
            session.commit()  # window checkpoint: crash-safe resume

        if high_water is not None:
            state.high_water = dt.datetime.combine(high_water, dt.time.min, tzinfo=dt.UTC)
        state.offset = 0
        state.status = "done"
        session.commit()
        report.per_query[query_key] = {"fetched": fetched, "ingested": ingested}

    return report
