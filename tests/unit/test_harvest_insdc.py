"""Harvest-level tests for the NCBI and DDBJ Stage-1 paths: cursor checkpoints and
crash resume, cross-archive dedup (N3 acceptance invariant 4), and the date-window
plumbing — all against fake clients and the real ingest pipeline (sqlite)."""

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from fhrs.core.models import HarvestState, Sample, SampleAlias
from fhrs.harvest.ddbj import harvest_ddbj, sample_payload
from fhrs.harvest.ingest import ingest_bytes
from fhrs.harvest.ncbi import harvest_ncbi
from fhrs.stats.build import build_snapshot


def biosample_xml(
    acc: str,
    geo_loc: str = "Saudi Arabia",
    package: str = "Microbe.1.0",
    last_update: str = "2024-06-01T00:00:00.000",
) -> str:
    return (
        f'<BioSample access="public" last_update="{last_update}" accession="{acc}">'
        f'<Ids><Id db="BioSample" is_primary="1">{acc}</Id></Ids>'
        f"<Description><Title>sample {acc}</Title>"
        f'<Organism taxonomy_name="soil metagenome"><OrganismName>soil metagenome</OrganismName></Organism>'
        f"</Description><Owner><Name>Some Lab</Name></Owner>"
        f"<Package>{package}</Package>"
        f"<Attributes>"
        f'<Attribute attribute_name="geo_loc_name">{geo_loc}</Attribute>'
        f"</Attributes></BioSample>"
    )


class FakeNcbi:
    """esearch_pages serves fixed idlist pages keyed by retstart; efetch serves
    per-uid XML. `fail_on` simulates a transport crash on one page fetch."""

    def __init__(self, pages: dict[int, list[str]], xml_by_uid: dict[str, str]):
        self.pages = pages
        self.xml_by_uid = xml_by_uid
        self.retstarts: list[int] = []
        self.efetched: list[list[str]] = []
        self.fail_on: int | None = None

    def esearch_pages(self, term, page_size=500, retstart=0, mindate=None, max_pages=None):
        start = retstart
        while start in self.pages:
            if self.fail_on is not None and start == self.fail_on:
                raise httpx.TransportError("boom")
            ids = self.pages[start]
            self.retstarts.append(start)
            yield start, ids
            start += page_size

    def efetch(self, ids):
        self.efetched.append(list(ids))
        body = "".join(self.xml_by_uid[u] for u in ids)
        return f"<BioSampleSet>{body}</BioSampleSet>"


def ddbj_item(acc: str, modified: str, geo_loc: str = "Saudi Arabia") -> dict:
    return {
        "identifier": acc,
        "dateModified": modified,
        "properties": {
            "BioSample": {
                "Attributes": {
                    "Attribute": [{"attribute_name": "geo_loc_name", "content": geo_loc}]
                }
            }
        },
        "dbXrefs": [],
    }


class FakeDdbj:
    """Models the real API semantics: windows filter items by dateModified; paging
    slices the matching items. `count_override` feeds the window planner synthetic
    counts (a split needs >9,000, more records than a fixture should carry), while
    paging always serves the real items of the window. `fail_on_page` crashes the
    Nth page fetch across the whole run (1-indexed) to simulate a mid-run failure."""

    def __init__(self, items: list[dict], count_override=None):
        self.items = items
        self.count_override = count_override
        self.paged: list[tuple] = []
        self.total_calls: list[tuple] = []
        self.fail_on_page: int | None = None
        self._page_calls = 0

    def _in_window(self, a: dt.date, b: dt.date) -> list[dict]:
        return [i for i in self.items if a <= dt.date.fromisoformat(i["dateModified"][:10]) <= b]

    def search_total(self, entry_type, *, field, value, date_modified_from, date_modified_to):
        self.total_calls.append((date_modified_from, date_modified_to))
        if self.count_override is not None:
            return self.count_override(date_modified_from, date_modified_to)
        return len(self._in_window(date_modified_from, date_modified_to))

    def search_pages(
        self,
        entry_type,
        *,
        field,
        value,
        date_modified_from,
        date_modified_to,
        start_page=1,
        per_page=50,
        max_pages=None,
    ):
        matching = self._in_window(date_modified_from, date_modified_to)
        pages = [matching[i : i + per_page] for i in range(0, len(matching), per_page)]
        for idx, chunk in enumerate(pages, start=1):
            if idx < start_page:
                continue
            self._page_calls += 1
            if self.fail_on_page is not None and self._page_calls == self.fail_on_page:
                raise httpx.TransportError("boom")
            self.paged.append((date_modified_from, date_modified_to, idx))
            yield _page(chunk, idx, has_next=idx < len(pages))


def _page(items, page, has_next):
    return {"items": items, "pagination": {"page": page, "hasNext": has_next, "total": len(items)}}


# --------------------------------------------------------------------------- NCBI


def test_ncbi_harvest_ingests_and_checkpoints(session, profile, geo, registry, payload_root):
    client = FakeNcbi(
        pages={0: ["7001", "7002"]},
        xml_by_uid={
            "7001": biosample_xml("SAMN0007001"),
            "7002": biosample_xml("SAMN0007002"),
        },
    )
    report = harvest_ncbi(
        session,
        client,
        profile=profile,
        geo=geo,
        registry=registry,
        payload_root=payload_root,
        full=True,
        only_query="country:saudi arabia",
    )
    assert report.fetched == 2 and report.ingest.samples_created == 2
    state = session.get(HarvestState, ("ncbi", "country:saudi arabia"))
    assert state.status == "done" and state.offset == 0
    assert state.high_water is not None
    # canonical SAMEA accessions are the stored identity; native forms are aliases
    accs = set(session.scalars(select(SampleAlias.accession)))
    assert {"SAMEA7001", "SAMEA7002", "SAMN0007001", "SAMN0007002"} <= accs


def test_ncbi_crash_resumes_from_page_checkpoint(session, profile, geo, registry, payload_root):
    client = FakeNcbi(
        pages={0: ["7001"], 500: ["7002"]},
        xml_by_uid={
            "7001": biosample_xml("SAMN0007001"),
            "7002": biosample_xml("SAMN0007002"),
        },
    )
    client.fail_on = 500
    with pytest.raises(httpx.TransportError):
        harvest_ncbi(
            session,
            client,
            profile=profile,
            geo=geo,
            registry=registry,
            payload_root=payload_root,
            full=True,
            only_query="country:ksa",
        )
    state = session.get(HarvestState, ("ncbi", "country:ksa"))
    assert state.status == "running" and state.offset == 500

    client.fail_on = None
    harvest_ncbi(
        session,
        client,
        profile=profile,
        geo=geo,
        registry=registry,
        payload_root=payload_root,
        only_query="country:ksa",
    )
    # resumed at the checkpointed page — page 1 was not re-walked
    assert client.retstarts == [0, 500]
    state = session.get(HarvestState, ("ncbi", "country:ksa"))
    assert state.status == "done" and state.offset == 0
    assert session.scalar(select(func.count()).select_from(Sample)) == 2


def test_cross_archive_dedup_one_sample_counted_once(session, profile, geo, registry, payload_root):
    """Acceptance invariant 4: ENA's SAMEA3995865 and NCBI's SAMN03995865 are the
    same physical sample — one canonical row, one count."""
    report = ingest_bytes(
        session,
        json.dumps(
            {
                "sample_accession": "SAMEA3995865",
                "country": "Saudi Arabia",
                "checklist": "ERC000047",
                "collection_date": "2020-01-01",
            }
        ).encode(),
        "ena",
        geo=geo,
        registry=registry,
        payload_root=payload_root,
    )
    assert report.samples_created == 1

    client = FakeNcbi(
        pages={0: ["3995865"]},
        xml_by_uid={"3995865": biosample_xml("SAMN03995865")},
    )
    harvest_ncbi(
        session,
        client,
        profile=profile,
        geo=geo,
        registry=registry,
        payload_root=payload_root,
        full=True,
        only_query="country:ksa",
    )

    assert session.scalar(select(func.count()).select_from(Sample)) == 1
    sample = session.scalar(select(Sample))
    aliases = set(
        session.scalars(select(SampleAlias.accession).where(SampleAlias.sample_id == sample.id))
    )
    assert {"SAMEA3995865", "SAMN03995865"} <= aliases
    # N1: the weaker NCBI payload must not erase ENA's collection_date
    assert sample.collection_date == "2020-01-01"
    snap = build_snapshot(session, "SA")
    assert snap["samples"]["total"] == 1


# --------------------------------------------------------------------------- DDBJ


def test_ddbj_harvest_windows_and_checkpoints(session, profile, geo, registry, payload_root):
    today = dt.date(2026, 6, 10)
    d1, d2 = dt.date(2026, 6, 1), dt.date(2026, 6, 2)

    def counts(a, b):
        return sum(v for d, v in {d1: 9500, d2: 100}.items() if a <= d <= b)

    items = [ddbj_item("SAMD0007001", "2026-06-01"), ddbj_item("SAMD0007002", "2026-06-02")]
    client = FakeDdbj(items, count_override=counts)
    report = harvest_ddbj(
        session,
        client,
        profile=profile,
        geo=geo,
        registry=registry,
        payload_root=payload_root,
        full=True,
        only_query="country:saudi",
        today=today,
    )
    # 9,600 over the 9,000 window cap → the range bisected into two windows
    assert report.per_query["country:saudi"]["windows"] == 2
    assert report.ingest.samples_created == 2
    base = session.get(HarvestState, ("ddbj", "country:saudi"))
    assert base.status == "done" and base.high_water is not None
    w1 = session.get(HarvestState, ("ddbj", "country:saudi@2026-06-01..2026-06-01"))
    assert w1 is not None and w1.status == "done"
    # dateModified is the cursor axis, not the BioSample's own last_update
    assert base.high_water.date() == d2


def test_ddbj_resume_skips_done_windows(session, profile, geo, registry, payload_root):
    today = dt.date(2026, 6, 10)
    d1, d2 = dt.date(2026, 6, 1), dt.date(2026, 6, 2)

    def counts(a, b):
        return sum(v for d, v in {d1: 9500, d2: 100}.items() if a <= d <= b)

    items = [ddbj_item("SAMD0007001", "2026-06-01"), ddbj_item("SAMD0007002", "2026-06-02")]
    client = FakeDdbj(items, count_override=counts)
    client.fail_on_page = 2  # window 1 pages fine; window 2's first page crashes
    with pytest.raises(httpx.TransportError):
        harvest_ddbj(
            session,
            client,
            profile=profile,
            geo=geo,
            registry=registry,
            payload_root=payload_root,
            full=True,
            only_query="country:saudi",
            today=today,
        )
    assert session.scalar(select(func.count()).select_from(Sample)) == 1

    client.fail_on_page = None
    harvest_ddbj(
        session,
        client,
        profile=profile,
        geo=geo,
        registry=registry,
        payload_root=payload_root,
        only_query="country:saudi",
        today=today,
    )
    # the finished window was never re-paged
    assert client.paged.count((d1, d1, 1)) == 1
    assert session.scalar(select(func.count()).select_from(Sample)) == 2


def test_ddbj_incremental_floor_is_high_water(session, profile, geo, registry, payload_root):
    today = dt.date(2026, 6, 10)
    client = FakeDdbj([ddbj_item("SAMD0007003", "2026-06-03")])
    harvest_ddbj(
        session,
        client,
        profile=profile,
        geo=geo,
        registry=registry,
        payload_root=payload_root,
        full=True,
        only_query="country:ksa",
        today=today,
    )
    base = session.get(HarvestState, ("ddbj", "country:ksa"))
    assert base.high_water.date() == dt.date(2026, 6, 3)

    # incremental run: windows start at the high-water mark, not the epoch
    client2 = FakeDdbj([])
    harvest_ddbj(
        session,
        client2,
        profile=profile,
        geo=geo,
        registry=registry,
        payload_root=payload_root,
        only_query="country:ksa",
        today=today,
    )
    assert client2.total_calls and client2.total_calls[0][0] == dt.date(2026, 6, 3)


def test_ddbj_sample_payload_from_real_fixture(geo):
    from fhrs.connectors.biosample import apply_country_from_geo

    body = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "ddbj_biosample_page.json").read_text()
    )
    item = next(i for i in body["items"] if i["identifier"] == "SAMD00055765")
    p = sample_payload(item)
    assert p["sample_accession"] == "SAMD00055765"
    assert p["checklist"] == "MIGS.ba"  # Models → checklist (no Package on DDBJ)
    assert p["study_accession"] == "PRJDB4948"  # from dbXrefs, not the BioSample
    assert p["last_updated"] == "2026-10-05T04:53:32Z"  # dateModified is the axis
    apply_country_from_geo(p, geo)
    assert p["country"] == "Saudi Arabia"
