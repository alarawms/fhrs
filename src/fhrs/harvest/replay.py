"""Local replay (N2): re-run normalize → classify → dedup → merge for every stored
payload, in original fetch order. Because merges are non-destructive, a replay after
a parser improvement can only *fill* previously-missing values — the exact move that
required a re-harvest in the predecessor system.

Payload paths are relative to the payload root, so replay works wherever the store
is mounted in this environment.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fhrs.connectors.insdc import normalize
from fhrs.core.models import SourceEvent
from fhrs.harvest.ingest import IngestReport, apply_record
from fhrs.harvest.payloads import load_payload
from fhrs.rules.checklists import ChecklistRegistry
from fhrs.rules.geo import GeoContext


def replay(
    session: Session,
    *,
    geo: GeoContext,
    registry: ChecklistRegistry,
    payload_root: Path,
) -> IngestReport:
    report = IngestReport()
    events = session.scalars(select(SourceEvent).order_by(SourceEvent.id)).all()
    for event in events:
        record = normalize(
            event.source, json.loads(load_payload(payload_root, event.payload_path))
        )
        apply_record(session, event, record, geo=geo, registry=registry, report=report)
        event.reprocessed_at = datetime.now(UTC)
        report.events += 1
    return report
