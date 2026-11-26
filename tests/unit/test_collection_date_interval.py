"""Regression (live ENA harvest crash, 2026-11-26): INSDC collection_date may be an
ISO 8601 interval. It must be stored whole — never truncated (N1), never crashing
the ingest. samples.collection_date is varchar(96) since alembic 0004.
"""

from sqlalchemy import select

from fhrs.core.models import Sample
from fhrs.harvest.ingest import ingest_bytes

INTERVAL = "2019-06-20T07:55:00Z/2019-06-20T12:20:00Z"


def test_interval_collection_date_stored_whole(session, geo, registry, payload_root):
    report = ingest_bytes(
        session,
        (
            b'{"sample_accession": "SAMEA7770001", "country": "Saudi Arabia",'
            b' "collection_date": "' + INTERVAL.encode() + b'"}'
        ),
        "ena",
        geo=geo,
        registry=registry,
        payload_root=payload_root,
    )
    assert report.samples_created == 1
    sample = session.scalar(select(Sample).where(Sample.canonical_accession == "SAMEA7770001"))
    assert sample.collection_date == INTERVAL
