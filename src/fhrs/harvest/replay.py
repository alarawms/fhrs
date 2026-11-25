"""Local replay (N2): re-run normalize → classify → dedup → merge for every stored
payload, in original fetch order. Because merges are non-destructive, a replay after
a parser improvement can only *fill* previously-missing values — the exact move that
required a re-harvest in the predecessor system.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from fhrs.connectors.insdc import normalize
from fhrs.core.models import SourceEvent
from fhrs.harvest.ingest import IngestReport, apply_record
from fhrs.harvest.payloads import load_payload
from fhrs.rules.checklists import ChecklistRegistry
from fhrs.rules.geo import GeoContext


def replay(session: Session, *, geo: GeoContext, registry: ChecklistRegistry) -> IngestReport:
    report = IngestReport()
    events = session.scalars(select(SourceEvent).order_by(SourceEvent.id)).all()
    for event in events:
        record = normalize(event.source, json.loads(load_payload(event.payload_path)))
        apply_record(session, event, record, geo=geo, registry=registry, report=report)
        event.reprocessed_at = datetime.now(timezone.utc)
        report.events += 1
    return report
