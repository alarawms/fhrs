"""Shared fixtures: the real SA country data, the real registry, and helpers to run
the actual ingest pipeline over the fixture corpus — tests exercise production code
paths, not stand-ins.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from fhrs.core.config import COUNTRIES_DIR, REGISTRY_PATH
from fhrs.core.db import init_schema, session_scope
from fhrs.harvest.ingest import IngestReport, ingest_bytes
from fhrs.rules.checklists import load_registry
from fhrs.rules.geo import load_geo_context

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "payloads"

FIXTURE_ORDER = [
    ("01-ena-tier-a.json", "ena"),
    ("02-ena-tier-b.json", "ena"),
    ("03-ncbi-crossref-weak.json", "ncbi"),
    ("04-ddbj-tier-c.json", "ddbj"),
    ("05-ena-unmapped-checklist.json", "ena"),
    ("06-ncbi-country-conflict.json", "ncbi"),
]


@pytest.fixture(scope="session")
def geo():
    sa = COUNTRIES_DIR / "sa"
    return load_geo_context(
        sa / "aliases.yaml", sa / "institutions.yaml", sa / "gaz.json", sa / "boundary.geojson"
    )


@pytest.fixture(scope="session")
def registry():
    return load_registry(REGISTRY_PATH)


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_schema(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    with session_scope(engine) as s:
        yield s


@pytest.fixture()
def payload_root(tmp_path):
    return tmp_path / "payloads"


def _merge_reports(total: IngestReport, report: IngestReport) -> IngestReport:
    for name in total.__dataclass_fields__:
        value = getattr(report, name)
        current = getattr(total, name)
        if isinstance(value, list):
            current.extend(v for v in value if v not in current)
        else:
            setattr(total, name, current + value)
    return total


@pytest.fixture()
def corpus(session, geo, registry, payload_root) -> IngestReport:
    """The six-payload corpus ingested in order with deterministic fetch times."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    total = IngestReport()
    for i, (name, source) in enumerate(FIXTURE_ORDER):
        report = ingest_bytes(
            session,
            (FIXTURES_DIR / name).read_bytes(),
            source,
            geo=geo,
            registry=registry,
            payload_root=payload_root,
            fetched_at=base + timedelta(days=i),
        )
        _merge_reports(total, report)
    return total
