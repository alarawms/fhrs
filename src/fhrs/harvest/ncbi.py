"""Stage-1 NCBI harvest: esearch discovery (country + institution terms from the
active country profile) → efetch BioSample XML in bounded chunks → flat payloads
through the standard ingest pipeline → per-query cursor checkpoints.

Incremental posture (verified by the predecessor system 2026-10-01): NCBI's date
filters do not constrain the discovery terms, so every run is effectively a full
pass and correctness comes from the idempotent, payload-hash-keyed ingest (N1/N2).
The high-water mark is still tracked and passed as ``mindate`` — advisory today,
free incrementality if NCBI ever honours it.

Everything the connector learns is a stored payload + source event, so replays and
re-ingests stay idempotent. Cross-archive dedup is by the shared INSDC numeric
accession (N3): ``SAMN03995865`` and ENA's ``SAMEA3995865`` are one canonical
sample.
"""

import datetime as dt
import json
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from fhrs.connectors.biosample import BiosampleNormalizer, apply_country_from_geo
from fhrs.connectors.insdc import canonical_sample_accession
from fhrs.connectors.ncbi import (
    EFETCH_CHUNK,
    PAGE_SIZE,
    NcbiClient,
    discovery_queries,
    parse_biosample_set,
    parse_date,
)
from fhrs.core.config import CountryProfile
from fhrs.core.models import HarvestState
from fhrs.harvest.ena import HarvestReport
from fhrs.harvest.ingest import ingest_bytes
from fhrs.rules.checklists import ChecklistRegistry
from fhrs.rules.geo import GeoContext

SOURCE = "ncbi"


def profile_queries(profile: CountryProfile) -> list[tuple[str, str]]:
    """Discovery terms from profile data — nothing country-shaped is hard-coded."""
    aliases_doc = yaml.safe_load(profile.data_path("aliases").read_text(encoding="utf-8"))
    inst_doc = yaml.safe_load(profile.data_path("institutions").read_text(encoding="utf-8"))
    inst_aliases = [alias for inst in inst_doc["institutions"] for alias in inst["aliases"]]
    return discovery_queries(aliases_doc["aliases"], inst_aliases, aliases_doc.get("ncbi_queries"))


def harvest_ncbi(
    session: Session,
    client: NcbiClient,
    *,
    profile: CountryProfile,
    geo: GeoContext,
    registry: ChecklistRegistry,
    payload_root: Path,
    full: bool = False,
    only_query: str | None = None,
    max_per_query: int | None = None,
) -> HarvestReport:
    report = HarvestReport()
    seen: set[str] = set()  # canonical INSDC accessions, deduped across queries

    for query_key, term in profile_queries(profile):
        if only_query and query_key != only_query:
            continue
        state = session.get(HarvestState, (SOURCE, query_key))
        if state is None:
            state = HarvestState(source=SOURCE, query_key=query_key, status="running")
            session.add(state)
            session.flush()
        since = None if full else (state.high_water.date() if state.high_water else None)
        mindate = since.isoformat() if since else None  # advisory — see docstring
        report.queries += 1

        high_water: dt.date | None = since
        fetched = ingested = 0
        retstart = 0 if full else state.offset
        for page_start, page in client.esearch_pages(term, retstart=retstart, mindate=mindate):
            # Pre-efetch dedup: skip ids whose accession is already seen, so a second
            # query covering the same corpus pages esearch but does not re-download
            # XML it already ingested.
            fresh = [
                n
                for n in page
                if (canonical_sample_accession(f"SAMN{n}") or f"SAMN{n}") not in seen
            ]
            for i in range(0, len(fresh), EFETCH_CHUNK):
                chunk = fresh[i : i + EFETCH_CHUNK]
                # Key the fetched XML by canonical accession: efetch may return the
                # zero-padded form (SAMN03995865) for a uid (3995865).
                elements = {
                    canonical_sample_accession(el.get("accession")): el
                    for el in parse_biosample_set(client.efetch(chunk))
                    if el.get("accession")
                }
                for uid in chunk:
                    canonical = canonical_sample_accession(f"SAMN{uid}") or f"SAMN{uid}"
                    el = elements.get(canonical)
                    if el is None or canonical in seen:
                        continue
                    seen.add(canonical)
                    acc = (el.get("accession") or f"SAMN{uid}").strip()
                    payload = BiosampleNormalizer(acc).flat(el)
                    apply_country_from_geo(payload, geo)
                    when = parse_date(payload.get("last_updated"))
                    if when and (high_water is None or when > high_water):
                        high_water = when
                    report.fetched += 1
                    fetched += 1
                    report.merge_ingest(
                        ingest_bytes(
                            session,
                            json.dumps(payload, ensure_ascii=False).encode(),
                            SOURCE,
                            geo=geo,
                            registry=registry,
                            payload_root=payload_root,
                            fetched_at=dt.datetime.now(dt.UTC),
                        )
                    )
                    ingested += 1
                    if max_per_query is not None and ingested >= max_per_query:
                        break
                if max_per_query is not None and ingested >= max_per_query:
                    break
            state.offset = page_start + PAGE_SIZE  # next page, in the server's paging units
            state.status = "running"
            session.commit()  # page checkpoint: crash-safe resume
            if max_per_query is not None and ingested >= max_per_query:
                break

        if high_water is not None:
            state.high_water = dt.datetime.combine(high_water, dt.time.min, tzinfo=dt.UTC)
        state.offset = 0
        state.status = "done"
        session.commit()
        report.per_query[query_key] = {"fetched": fetched, "ingested": ingested}

    return report
