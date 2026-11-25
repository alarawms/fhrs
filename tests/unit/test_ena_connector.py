"""ENA connector (Stage 1): query construction against the real SA profile, client
retry/back-off, and the full harvest flow over recorded real ENA rows."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from fhrs.connectors.ena import (
    EnaClient,
    batched_or,
    country_queries,
    institution_query,
    parse_date,
    search_params,
    with_since,
)
from fhrs.core.models import HarvestState, Sample, SampleAlias
from fhrs.harvest.ena import discovery_queries, harvest_ena
from fhrs.stats.build import build_snapshot

FIXTURE_ROWS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "ena_sample_page.json").read_text(
        encoding="utf-8"
    )
)


def test_search_params_wire_shape():
    assert search_params("sample", 'country="Saudi*"') == {
        "result": "sample",
        "query": 'country="Saudi*"',
        "fields": "all",
        "format": "json",
        "limit": "0",  # whole result set; ENA dropped offset pagination
    }


def test_batched_or_rejects_empty():
    with pytest.raises(ValueError):
        batched_or("sample_accession", [])


def test_with_since():
    assert with_since('country="Saudi*"', date(2026, 1, 1)) == (
        '(country="Saudi*") AND last_updated>=2026-01-01'
    )
    assert with_since('country="Saudi*"', None) == 'country="Saudi*"'


def test_parse_date():
    assert parse_date("2023-01-03") == date(2023, 1, 3)
    assert parse_date("") is None and parse_date(None) is None
    assert parse_date("garbage") is None


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("KAUST", 'center_name="*KAUST*"'),                       # single word: wildcard
        ("Aramco", 'center_name="*Aramco*"'),
        ("King Saud University", 'center_name="King Saud University"'),  # exact
    ],
)
def test_institution_query(alias, expected):
    assert institution_query(alias) == expected


def test_country_queries_use_verified_overrides(profile):
    import yaml

    doc = yaml.safe_load(profile.data_path("aliases").read_text(encoding="utf-8"))
    qs = dict(country_queries(doc["aliases"], doc.get("ena_queries", {})))
    assert qs["country:saudi"] == 'country="Saudi*"'
    assert qs["country:ksa"] == 'country="KSA*"'
    assert qs["country:kingdom of saudi arabia"] == 'country="Kingdom of Saudi*"'
    assert qs["country:المملكة العربية السعودية"] == 'country="المملكة*"'


def test_geo_box_query_covers_verified_ksa_box(profile, geo):
    queries = discovery_queries(profile, geo)
    q = next(q for k, q in queries if k == "geo_box_not_country")
    assert q.startswith("geo_box1(")
    # The verified discovery box is (16.0,34.0,32.5,56.0); the derived+padded box
    # must contain it.
    nums = q.removeprefix("geo_box1(").split(")")[0].split(",")
    lat_min, lon_min, lat_max, lon_max = (float(x) for x in nums)
    assert lat_min <= 16.0 and lon_min <= 34.0 and lat_max >= 32.5 and lon_max >= 56.0
    assert "AND NOT" in q


def test_client_retries_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky(path, params):
        calls["n"] += 1
        if calls["n"] <= 2:
            return 429, b"rate limited"
        return 200, b'[{"sample_accession": "SAMEA1"}]'

    client = EnaClient(fetch=flaky, sleeper=sleeps.append)
    rows = client.search("sample", 'country="Saudi*"')
    assert rows == [{"sample_accession": "SAMEA1"}]
    assert sleeps == [2.0, 4.0]  # exponential back-off


def test_client_retries_transport_errors():
    calls = {"n": 0}

    def flaky(path, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TransportError("read error")
        return 200, b"[]"

    client = EnaClient(fetch=flaky, sleeper=lambda s: None)
    assert client.search("sample", "x") == []


def _fake_client_with_rows(rows, log):
    def fetch(path, params):
        log.append((path, dict(params)))
        q = params.get("query", "")
        if path == "/search" and (
            q == 'country="Saudi*"' or q.startswith('(country="Saudi*"')
        ):
            return 200, json.dumps(rows).encode()
        return 200, b"[]"

    return EnaClient(fetch=fetch, sleeper=lambda s: None)


def test_harvest_flow_with_real_ena_rows(session, profile, geo, registry, payload_root):
    wire_log: list = []
    client = _fake_client_with_rows(FIXTURE_ROWS, wire_log)
    report = harvest_ena(
        session, client, profile=profile, geo=geo, registry=registry,
        payload_root=payload_root,
    )
    assert report.queries >= 3  # country + geo box + institutions
    assert report.fetched == 6
    assert session.scalar(select(func.count()).select_from(Sample)) == 6

    by_acc = {
        a: session.scalar(select(Sample).where(Sample.canonical_accession == a))
        for a in ("SAMEA10033100", "SAMEA10033102", "SAMEA103936009",
                  "SAMEA103936010", "SAMEA104071975", "SAMEA104416030")
    }
    assert [s.tier for s in by_acc.values()] == ["A", "A", "A", "A", "C", "C"]
    # coords persisted from the real location strings
    assert by_acc["SAMEA103936009"].lat == pytest.approx(21.608454)
    # the ENA default checklist maps deliberately to "other"
    assert by_acc["SAMEA104071975"].content_bucket == "other"
    assert by_acc["SAMEA10033100"].content_bucket == "environmental"
    # ERS secondary accession resolves to the same canonical sample (N3)
    alias = session.scalar(
        select(SampleAlias).where(SampleAlias.accession == "ERS1735868")
    )
    assert alias is not None and alias.sample_id == by_acc["SAMEA104071975"].id

    # cursor persisted: high water = max(last_updated) = 2023-01-03
    state = session.get(HarvestState, ("ena", "country:saudi"))
    assert state.status == "done"
    assert state.high_water.date() == date(2023, 1, 3)

    # incremental run: the same rows are idempotent, and the wire query is scoped
    wire_log.clear()
    report2 = harvest_ena(
        session, client, profile=profile, geo=geo, registry=registry,
        payload_root=payload_root,
    )
    saudi_queries = [
        p["query"] for path, p in wire_log if p["query"].startswith('(country="Saudi*"')
    ]
    assert saudi_queries and all("last_updated>=2023-01-03" in q for q in saudi_queries)
    assert report2.ingest.skipped_duplicates == 6
    assert session.scalar(select(func.count()).select_from(Sample)) == 6

    stats = build_snapshot(session, "SA")
    s = stats["samples"]
    assert s["public"] == 4 and s["in_curation"] == 2
    assert s["by_tier"] == {"A": 4, "B": 0, "C": 2}
    assert s["by_content_type"] == {"environmental": 4}
    assert s["with_coordinates"] == 2 and s["marine_eez"] == 0
    assert stats["studies"] == 2  # PRJEB48354, PRJEB20120 (curation sample's study excluded)
