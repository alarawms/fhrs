"""Integration: full pipeline against Postgres (CI service / `docker compose up db`).

Covers acceptance invariant 8 (no sentinel representation of study-less samples)
and the alembic-built schema end to end.
"""

import os

import pytest
from sqlalchemy import create_engine, text

from fhrs.core.db import init_schema, session_scope
from fhrs.harvest.replay import replay
from fhrs.stats.build import build_snapshot

pytestmark = pytest.mark.integration

DB_URL = os.environ.get("FHRS_DATABASE_URL", "postgresql+psycopg://fhrs:fhrs@localhost:15432/fhrs")


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    init_schema(engine)  # idempotent; alembic 0001 created the same metadata
    for table in (
        "attribute_history",
        "sample_attributes",
        "merge_events",
        "sample_aliases",
        "curation_events",
        "samples",
        "studies",
        "source_events",
        "unmapped_checklists",
        "stats_snapshots",
        # the suite owns the whole dev database — cursors included. Never run it
        # against a database that is mid-harvest (learned the hard way 2026-11-26).
        "harvest_states",
    ):
        with engine.begin() as conn:
            conn.execute(text(f"truncate table {table} cascade"))
    yield engine
    engine.dispose()


def test_alembic_schema_matches_orm_metadata(pg_engine):
    """Drift guard: the alembic-built schema and the ORM models must agree. 0001 is
    frozen by hand, so this is the check that catches a model edit without a
    matching revision."""
    from sqlalchemy import inspect

    live = inspect(pg_engine)
    from fhrs.core.models import Base

    for table_name, table in Base.metadata.tables.items():
        live_cols = {c["name"] for c in live.get_columns(table_name)}
        model_cols = {c.name for c in table.columns}
        assert live_cols == model_cols, (
            f"{table_name}: live^{live_cols - model_cols} model^{model_cols - live_cols}"
        )


def test_full_pipeline_on_postgres(pg_engine, geo, registry, tmp_path):
    from conftest import FIXTURE_ORDER, FIXTURES_DIR

    from fhrs.harvest.ingest import ingest_bytes

    with session_scope(pg_engine) as session:
        for name, source in FIXTURE_ORDER:
            ingest_bytes(
                session,
                (FIXTURES_DIR / name).read_bytes(),
                source,
                geo=geo,
                registry=registry,
                payload_root=tmp_path / "payloads",
            )
        payload = build_snapshot(session, "SA")

    assert payload["samples"]["total"] == 4
    assert payload["samples"]["public"] == 3
    assert payload["samples"]["by_tier"] == {"A": 2, "B": 1, "C": 1}

    with pg_engine.connect() as conn:
        # invariant 8: no sentinel studies; study-less is NULL only
        sentinels = conn.execute(
            text("select count(*) from studies where accession like 'NOSTUDY%'")
        ).scalar()
        assert sentinels == 0
        null_study = conn.execute(
            text("select count(*) from samples where study_id is null")
        ).scalar()
        assert null_study == 1

    # N2: replay over the payload store reaches the same state
    with session_scope(pg_engine) as session:
        report = replay(session, geo=geo, registry=registry, payload_root=tmp_path / "payloads")
        assert report.events == 6
        payload2 = build_snapshot(session, "SA")
        assert payload2["samples"] == payload["samples"]
