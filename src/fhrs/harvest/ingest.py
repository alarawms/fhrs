"""The ingest pipeline. Order is the contract (PROMPT.md):

    store payload (N2) → append source_event (N1) → normalize → classify inclusion
    → dedup/identity resolution BEFORE visibility (N3) → per-key non-destructive
    merge with provenance + history (N1) → checklist bucket or LOUD unmapped queue
    (N5) → tier C enters curation (N8 guards decide from there).

Out-of-scope records (tier None) are counted on the event, never stored as samples.
Study-less samples keep study_id NULL — no sentinel studies (N4).
"""

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fhrs.connectors.insdc import normalize
from fhrs.connectors.normalized import NormalizedRecord
from fhrs.core.models import (
    AttributeHistory,
    CurationEvent,
    MergeEvent,
    Sample,
    SampleAlias,
    SampleAttribute,
    SourceEvent,
    Study,
    UnmappedChecklist,
    Visibility,
)
from fhrs.harvest.payloads import store_payload
from fhrs.rules import merge as merge_rules
from fhrs.rules.checklists import ChecklistRegistry
from fhrs.rules.geo import GeoContext
from fhrs.rules.inclusion import classify

# Scalar sample fields maintained by the merge. Provenance lives in
# sample_attributes; changes in attribute_history.
MERGED_FIELDS = (
    "checklist_id",
    "collection_date",
    "scientific_name",
    "library_strategy",
    "country_raw",
    "center_name",
    "lat",
    "lon",
)


@dataclass
class IngestReport:
    events: int = 0
    skipped_duplicates: int = 0
    samples_created: int = 0
    samples_merged: int = 0
    dropped_out_of_scope: int = 0
    attributes_filled: int = 0
    conflicts_kept: int = 0
    unmapped_checklists: list[str] = field(default_factory=list)


def ingest_bytes(
    session: Session,
    data: bytes,
    source: str,
    *,
    geo: GeoContext,
    registry: ChecklistRegistry,
    payload_root: Path,
    fetched_at: datetime | None = None,
) -> IngestReport:
    report = IngestReport()
    fetched_at = fetched_at or datetime.now(UTC)
    record = normalize(source, json.loads(data))
    relative, sha = store_payload(payload_root, source, record.primary_accession, data)
    if session.scalar(select(SourceEvent).where(SourceEvent.payload_sha256 == sha)):
        report.skipped_duplicates += 1
        return report
    event = SourceEvent(
        source=source,
        accession=record.primary_accession,
        fetched_at=fetched_at,
        payload_path=str(relative),
        payload_sha256=sha,
    )
    session.add(event)
    session.flush()
    report.events += 1
    apply_record(session, event, record, geo=geo, registry=registry, report=report)
    return report


def apply_record(
    session: Session,
    event: SourceEvent,
    record: NormalizedRecord,
    *,
    geo: GeoContext,
    registry: ChecklistRegistry,
    report: IngestReport | None = None,
) -> Sample | None:
    """Shared by live ingest and replay (N2)."""
    report = report or IngestReport()
    decision = classify(
        record.country,
        record.location,
        record.lat,
        record.lon,
        record.gaz_curie,
        record.center_name,
        record.study_text or record.study_title,
        geo,
    )
    event.tier = decision.tier
    if decision.tier is None:
        report.dropped_out_of_scope += 1
        return None

    if decision.coords and record.lat is None and record.lon is None:
        # The location string classified this record; persist the parsed coordinates
        # too (provenance: this event's payload), never leaving them in a string.
        record = replace(record, lat=str(decision.coords[0]), lon=str(decision.coords[1]))

    sample = _resolve_sample(session, record, event, report)
    _merge_attributes(session, sample, record, event, report)
    _apply_inclusion(
        sample,
        decision_fields=(
            decision.tier,
            decision.marine_eez,
            decision.evidence,
            decision.needs_geo_curation,
        ),
    )
    _link_study(session, sample, record)
    _bucket_checklist(session, sample, record, registry, report, event_id=event.id)
    if decision.needs_geo_curation:
        _flag_for_curation(session, sample, decision.evidence)
    return sample


def _resolve_sample(
    session: Session, record: NormalizedRecord, event: SourceEvent, report: IngestReport
) -> Sample:
    """Identity resolution before visibility (N3). Any known alias finds the one
    canonical sample; new cross-references are recorded with a merge event."""
    existing_alias = session.scalar(
        select(SampleAlias).where(SampleAlias.accession.in_(sorted(record.accessions))).limit(1)
    )
    if existing_alias is None:
        sample = Sample(canonical_accession=record.primary_accession, tier=event.tier or "A")
        session.add(sample)
        session.flush()
        for acc in sorted(record.accessions):
            session.add(SampleAlias(accession=acc, sample_id=sample.id, source=record.source))
        report.samples_created += 1
        return sample

    sample = session.get(Sample, existing_alias.sample_id)
    assert sample is not None
    for acc in sorted(record.accessions):
        if not session.scalar(select(SampleAlias).where(SampleAlias.accession == acc)):
            session.add(SampleAlias(accession=acc, sample_id=sample.id, source=record.source))
            session.add(
                MergeEvent(
                    canonical_sample_id=sample.id,
                    merged_accession=acc,
                    reason="accession cross-reference",
                    event_id=event.id,
                )
            )
            report.samples_merged += 1
    return sample


def _merge_attributes(
    session: Session,
    sample: Sample,
    record: NormalizedRecord,
    event: SourceEvent,
    report: IngestReport,
) -> None:
    incoming = {
        "checklist_id": record.checklist_id,
        "collection_date": record.collection_date,
        "scientific_name": record.scientific_name,
        "library_strategy": record.library_strategy,
        "country_raw": record.country,
        "center_name": record.center_name,
        "lat": record.lat,
        "lon": record.lon,
    }
    for key in MERGED_FIELDS:
        current = getattr(sample, key)
        outcome = merge_rules.decide(None if current is None else str(current), incoming[key])
        if outcome.action == "keep":
            continue
        if outcome.action == "conflict":
            # First-writer-wins: the existing value stays; the losing candidate is
            # audited so conflicts are visible and replay order stays deterministic.
            # Deliberate corrections are a curation action (Stage 3), not a side
            # effect of whichever payload arrived last.
            report.conflicts_kept += 1
            session.add(
                AttributeHistory(
                    sample_id=sample.id,
                    key=key,
                    old_value=str(current),
                    new_value=outcome.value,
                    action="conflict_kept",
                    source=event.source,
                    event_id=event.id,
                )
            )
            continue
        _set_field(session, sample, key, outcome.value, "filled", event)
        report.attributes_filled += 1


def _set_field(
    session: Session,
    sample: Sample,
    key: str,
    value: str,
    action: str,
    event: SourceEvent,
) -> None:
    old = getattr(sample, key)
    if key in ("lat", "lon"):
        setattr(sample, key, float(value))
    else:
        setattr(sample, key, value)
    attr = session.get(SampleAttribute, (sample.id, key))
    if attr is None:
        attr = SampleAttribute(
            sample_id=sample.id, key=key, value=value, source=event.source, event_id=event.id
        )
        session.add(attr)
    else:
        attr.value = value
        attr.source = event.source
        attr.event_id = event.id
    session.add(
        AttributeHistory(
            sample_id=sample.id,
            key=key,
            old_value=None if old is None else str(old),
            new_value=value,
            action=action,
            source=event.source,
            event_id=event.id,
        )
    )


def _apply_inclusion(sample: Sample, decision_fields: tuple[str | None, bool, list, bool]) -> None:
    tier, marine_eez, evidence, needs_geo_curation = decision_fields
    if tier:
        sample.tier = tier
    sample.marine_eez = marine_eez
    sample.evidence = evidence
    sample.needs_geo_curation = needs_geo_curation
    sample.visibility = Visibility.curation.value if needs_geo_curation else Visibility.public.value


def _link_study(session: Session, sample: Sample, record: NormalizedRecord) -> None:
    """Real studies only; study-less samples keep study_id NULL (N4)."""
    if not record.study_accession:
        return
    study = session.scalar(select(Study).where(Study.accession == record.study_accession))
    if study is None:
        study = Study(accession=record.study_accession, title=record.study_title)
        session.add(study)
        session.flush()
    elif not study.title and record.study_title:
        study.title = record.study_title
    sample.study_id = study.id


def _bucket_checklist(
    session: Session,
    sample: Sample,
    record: NormalizedRecord,
    registry: ChecklistRegistry,
    report: IngestReport,
    event_id: int | None = None,
) -> None:
    if not record.checklist_id:
        return
    bucket = registry.bucket_for(record.checklist_id)
    if bucket is None:
        row = session.get(UnmappedChecklist, record.checklist_id)
        if row is None:
            row = UnmappedChecklist(
                checklist_id=record.checklist_id,
                example_accession=record.primary_accession,
                last_event_id=event_id,
            )
            session.add(row)
        elif row.last_event_id != event_id:
            row.occurrences += 1
            row.last_event_id = event_id
        if record.checklist_id not in report.unmapped_checklists:
            report.unmapped_checklists.append(record.checklist_id)
    else:
        sample.content_bucket = bucket


def _flag_for_curation(session: Session, sample: Sample, evidence: list) -> None:
    already_open = session.scalar(
        select(CurationEvent).where(
            CurationEvent.sample_id == sample.id, CurationEvent.status == "open"
        )
    )
    if already_open is None:
        session.add(
            CurationEvent(
                sample_id=sample.id,
                action="flag_for_review",
                actor="system",
                rationale=json.dumps(evidence),
            )
        )
