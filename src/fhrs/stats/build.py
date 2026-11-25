"""fhrs-stats build: compute every aggregate once, from canonical records only, and
store it as a StatsSnapshot plus a numbers.json artifact. The API, web UI, reports
and papers consume the snapshot — nothing else computes aggregates (N6).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fhrs.core.models import (
    Sample,
    SourceEvent,
    StatsSnapshot,
    UnmappedChecklist,
    Visibility,
)


def compute_payload(session: Session, country_code: str) -> dict:
    """Aggregates over canonical samples only (dedup folds aliases into one row, N3).
    Curation-scope rows are reported separately, never mixed into public counts.
    """
    public = [Visibility.public.value]
    curation = [Visibility.curation.value]

    def count(vis: list[str]) -> int:
        return session.scalar(
            select(func.count()).select_from(Sample).where(Sample.visibility.in_(vis))
        ) or 0

    by_tier = dict(
        session.execute(
            select(Sample.tier, func.count())
            .where(Sample.visibility.in_(public + curation))
            .group_by(Sample.tier)
        ).all()
    )
    by_bucket = {
        bucket or "unmapped": n
        for bucket, n in session.execute(
            select(Sample.content_bucket, func.count())
            .where(Sample.visibility.in_(public))
            .group_by(Sample.content_bucket)
        ).all()
    }
    with_coords = session.scalar(
        select(func.count())
        .select_from(Sample)
        .where(Sample.visibility.in_(public), Sample.lat.is_not(None), Sample.lon.is_not(None))
    ) or 0
    with_date = session.scalar(
        select(func.count())
        .select_from(Sample)
        .where(Sample.visibility.in_(public), Sample.collection_date.is_not(None))
    ) or 0
    marine = session.scalar(
        select(func.count())
        .select_from(Sample)
        .where(Sample.visibility.in_(public), Sample.marine_eez.is_(True))
    ) or 0
    studies = session.scalar(
        select(func.count(func.distinct(Sample.study_id)))
        .where(Sample.visibility.in_(public), Sample.study_id.is_not(None))
    ) or 0
    dropped = session.scalar(
        select(func.count()).select_from(SourceEvent).where(SourceEvent.tier.is_(None))
    ) or 0
    events = session.scalar(select(func.count()).select_from(SourceEvent)) or 0
    unmapped = [
        {"checklist_id": row.checklist_id, "occurrences": row.occurrences}
        for row in session.scalars(select(UnmappedChecklist)).all()
    ]

    n_public, n_curation = count(public), count(curation)
    return {
        "country_code": country_code,
        "built_at": datetime.now(UTC).isoformat(),
        "samples": {
            "total": n_public + n_curation,
            "public": n_public,
            "in_curation": n_curation,
            "by_tier": {t: by_tier.get(t, 0) for t in ("A", "B", "C")},
            "by_content_type": by_bucket,
            "with_coordinates": with_coords,
            "with_collection_date": with_date,
            "marine_eez": marine,
        },
        "studies": studies,
        "source_events": events,
        "dropped_out_of_scope": dropped,
        "unmapped_checklists": sorted(unmapped, key=lambda r: -r["occurrences"]),
    }


def build_snapshot(session: Session, country_code: str, numbers_out: Path | None = None) -> dict:
    payload = compute_payload(session, country_code)
    session.add(StatsSnapshot(country_code=country_code, payload=payload))
    session.flush()
    if numbers_out is not None:
        numbers_out.parent.mkdir(parents=True, exist_ok=True)
        numbers_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def latest_snapshot(session: Session, country_code: str) -> StatsSnapshot | None:
    return session.scalar(
        select(StatsSnapshot)
        .where(StatsSnapshot.country_code == country_code)
        .order_by(StatsSnapshot.id.desc())
        .limit(1)
    )
