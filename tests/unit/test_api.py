"""HTTP surface: config reflects the active country, stats come from the snapshot
(N6), samples list shows public records only."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from fhrs.api.main import create_app
from fhrs.core.config import Settings
from fhrs.core.db import init_schema, session_scope
from fhrs.stats.build import build_snapshot


@pytest.fixture()
def client(tmp_path, geo, registry):
    db = tmp_path / "api.db"
    payload_dir = tmp_path / "payloads"
    settings = Settings(
        country_code="SA",
        database_url=f"sqlite:///{db}",
        payload_dir=payload_dir,
        numbers_out=tmp_path / "numbers.json",
    )
    engine = create_engine(settings.database_url)
    init_schema(engine)
    with session_scope(engine) as session:
        # reuse the corpus through the real pipeline
        from conftest import FIXTURE_ORDER, FIXTURES_DIR

        from fhrs.harvest.ingest import ingest_bytes

        for name, source in FIXTURE_ORDER:
            ingest_bytes(
                session,
                (FIXTURES_DIR / name).read_bytes(),
                source,
                geo=geo,
                registry=registry,
                payload_root=payload_dir,
            )
        build_snapshot(session, "SA")
    engine.dispose()
    return TestClient(create_app(settings))


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok", "country": "SA"}


def test_config_reflects_active_country(client):
    cfg = client.get("/config").json()
    assert cfg["country_code"] == "SA"
    assert cfg["name"] == "Saudi Arabia"
    assert cfg["languages"] == ["en", "ar"]
    assert cfg["branding"]["site_name"] == "FHRS — Saudi Arabia"


def test_stats_serves_snapshot(client):
    payload = client.get("/api/v1/stats").json()
    assert payload["country_code"] == "SA"
    assert payload["samples"]["public"] == 3
    assert payload["samples"]["by_tier"] == {"A": 2, "B": 1, "C": 1}


def test_samples_lists_public_only(client):
    body = client.get("/api/v1/samples").json()
    accessions = {h["accession"] for h in body["hits"]}
    assert accessions == {"SAMEA1110001", "SAMEA1110002", "SAMEA1110003"}
    assert body["total"] == 3
    # tier C record is in curation, not public
    assert "SAMD3330001" not in accessions
    study_linked = {h["accession"]: h["study_linked"] for h in body["hits"]}
    assert study_linked["SAMEA1110002"] is False  # honest NULL, not a placeholder


def test_stats_503_before_first_snapshot(tmp_path, geo, registry):
    settings = Settings(
        country_code="SA",
        database_url=f"sqlite:///{tmp_path / 'empty.db'}",
        payload_dir=tmp_path / "payloads",
        numbers_out=tmp_path / "numbers.json",
    )
    engine = create_engine(settings.database_url)
    init_schema(engine)
    engine.dispose()
    empty = TestClient(create_app(settings))
    assert empty.get("/api/v1/stats").status_code == 503
