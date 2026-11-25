"""Stage-1 DDBJ harvest: search-API discovery (country + institution queries from
the active country profile) → date-windowed paging under the deep-paging cap → flat
payloads through the standard ingest pipeline → per-window cursor checkpoints.

Unlike NCBI, DDBJ's ``dateModifiedFrom`` is a real filter (verified live by the
predecessor system 2026-10-01), so an incremental run plans its windows from the
high-water mark forward and is genuinely bounded. The 10,000-record deep-paging
wall makes windows mandatory for the full corpus: each query is sliced on the
``dateModified`` axis until every window's live count fits (``plan_date_windows``);
a restarted run skips finished windows (status done) and resumes the in-flight one
at its checkpointed 1-indexed page.
"""

import datetime as dt
import json
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from fhrs.connectors.biosample import BiosampleNormalizer, _txt, apply_country_from_geo
from fhrs.connectors.ddbj import (
    EPOCH,
    DdbjClient,
    discovery_queries,
    plan_date_windows,
)
from fhrs.connectors.insdc import canonical_sample_accession
from fhrs.connectors.ncbi import parse_date
from fhrs.core.config import CountryProfile
from fhrs.core.models import HarvestState
from fhrs.harvest.ena import HarvestReport
from fhrs.harvest.ingest import ingest_bytes
from fhrs.rules.checklists import ChecklistRegistry
from fhrs.rules.geo import GeoContext

SOURCE = "ddbj"


def profile_queries(profile: CountryProfile) -> list[tuple[str, str, str]]:
    """Discovery triples from profile data — nothing country-shaped is hard-coded."""
    aliases_doc = yaml.safe_load(profile.data_path("aliases").read_text(encoding="utf-8"))
    inst_doc = yaml.safe_load(profile.data_path("institutions").read_text(encoding="utf-8"))
    inst_aliases = [alias for inst in inst_doc["institutions"] for alias in inst["aliases"]]
    return discovery_queries(aliases_doc["aliases"], inst_aliases, aliases_doc.get("ddbj_queries"))


def _models_of(item: dict) -> str | None:
    """The INSDC model(s) from a DDBJ entry's properties.BioSample.Models, if any.
    DDBJ BioSamples carry no ``Package``; Models (MIGS.eu, SRA004434, "Generic", …)
    is the checklist equivalent so DDBJ records ride the same registry (N5)."""
    bs = (item.get("properties") or {}).get("BioSample") or {}
    models = (bs.get("Models") or {}).get("Model")
    if isinstance(models, dict):
        models = [models]
    names = [_txt(m.get("content")) if isinstance(m, dict) else _txt(m) for m in (models or [])]
    names = [n for n in names if n]
    return ", ".join(sorted(names)) if names else None


def sample_payload(item: dict) -> dict | None:
    """A DDBJ search item (or full entry) → the flat payload for ingest."""
    accession = str(item.get("identifier") or "").strip()
    if not accession:
        return None
    node = (item.get("properties") or {}).get("BioSample") or {}
    p = BiosampleNormalizer(accession).flat(node)
    p["sample_accession"] = accession
    if "checklist" not in p:
        models = _models_of(item)
        if models:
            p["checklist"] = models
    # The bioproject link lives in the item's top-level dbXrefs, not in
    # properties.BioSample (which carries no Links element on the DDBJ side).
    if "study_accession" not in p:
        for x in item.get("dbXrefs") or []:
            if x.get("type") == "bioproject" and x.get("identifier"):
                p["study_accession"] = x["identifier"]
                break
    # The cursor axis is the index's dateModified (windows and the high-water mark
    # are planned on it), NOT the BioSample's own last_update — a record modified
    # today with a 2022 last_update must advance the watermark.
    if item.get("dateModified"):
        p["last_updated"] = str(item["dateModified"])
    return p


def harvest_ddbj(
    session: Session,
    client: DdbjClient,
    *,
    profile: CountryProfile,
    geo: GeoContext,
    registry: ChecklistRegistry,
    payload_root: Path,
    full: bool = False,
    only_query: str | None = None,
    max_per_query: int | None = None,
    today: dt.date | None = None,
) -> HarvestReport:
    report = HarvestReport()
    seen: set[str] = set()  # canonical INSDC accessions, deduped across queries
    today = today or dt.date.today()

    for query_key, field, value in profile_queries(profile):
        if only_query and query_key != only_query:
            continue
        base = session.get(HarvestState, (SOURCE, query_key))
        if base is None:
            base = HarvestState(source=SOURCE, query_key=query_key, status="running")
            session.add(base)
            session.flush()
        since = None if full else (base.high_water.date() if base.high_water else None)
        floor = since or EPOCH
        report.queries += 1

        def count(a: dt.date, b: dt.date, field: str = field, value: str = value) -> int:
            return client.search_total(
                "biosample",
                field=field,
                value=value,
                date_modified_from=a,
                date_modified_to=b,
            )

        windows = plan_date_windows(count, floor, today)
        high_water: dt.date | None = since
        fetched = ingested = 0
        stop = False
        for w in windows:
            # A single-window range keeps the bare key (checkpoint-compatible with
            # pre-windowing runs); split ranges get a per-window suffix so resume is
            # per-window.
            wkey = (
                query_key
                if len(windows) == 1
                else f"{query_key}@{w.start.isoformat()}..{w.end.isoformat()}"
            )
            state = base if wkey == query_key else session.get(HarvestState, (SOURCE, wkey))
            if state is None:
                state = HarvestState(source=SOURCE, query_key=wkey, status="running")
                session.add(state)
                session.flush()
            if state.status == "done" and not full:
                continue  # window finished in a previous run
            start_page = 1 if full else (state.offset or 1)
            for body in client.search_pages(
                "biosample",
                field=field,
                value=value,
                date_modified_from=w.start,
                date_modified_to=w.end,
                start_page=start_page,
            ):
                page_no = body.get("pagination", {}).get("page", start_page)
                for item in body.get("items", []):
                    p = sample_payload(item)
                    if p is None:
                        continue
                    canonical = (
                        canonical_sample_accession(p["sample_accession"]) or p["sample_accession"]
                    )
                    if canonical in seen:
                        report.skipped_duplicate_accessions += 1
                        continue
                    seen.add(canonical)
                    apply_country_from_geo(p, geo)
                    when = parse_date(p.get("last_updated"))
                    if when and (high_water is None or when > high_water):
                        high_water = when
                    report.fetched += 1
                    fetched += 1
                    report.merge_ingest(
                        ingest_bytes(
                            session,
                            json.dumps(p, ensure_ascii=False).encode(),
                            SOURCE,
                            geo=geo,
                            registry=registry,
                            payload_root=payload_root,
                            fetched_at=dt.datetime.now(dt.UTC),
                        )
                    )
                    ingested += 1
                    if max_per_query is not None and ingested >= max_per_query:
                        stop = True
                        break
                if stop:
                    break
                state.offset = page_no + 1  # next page to fetch
                state.status = "running"
                session.commit()  # page checkpoint: crash-safe resume
            if stop:
                break
            state.offset = 0
            state.status = "done"
            session.commit()

        if high_water is not None:
            base.high_water = dt.datetime.combine(high_water, dt.time.min, tzinfo=dt.UTC)
        if not windows or base is not state:
            base.offset = 0
            base.status = "done"
        session.commit()
        report.per_query[query_key] = {
            "fetched": fetched,
            "ingested": ingested,
            "windows": len(windows),
        }

    return report
