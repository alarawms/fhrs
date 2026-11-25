"""NCBI connector: query builders, esearch paging, retry/back-off — all through an
injected fetch, no network. Fixture XML is a recorded live efetch response."""

import json
from pathlib import Path

import httpx
import pytest

from fhrs.connectors.ncbi import (
    NcbiClient,
    country_term,
    discovery_queries,
    esearch_params,
    institution_term,
    parse_biosample_set,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_country_term_quoting():
    assert country_term("saudi arabia") == '"saudi arabia"[geo_loc_name]'
    assert country_term("ksa") == "ksa[geo_loc_name]"


def test_institution_term():
    assert institution_term("KAUST") == "KAUST[Organization]"


def test_discovery_queries_country_first_and_overrides():
    qs = discovery_queries(["saudi arabia", "ksa"], ["KAUST"], {"ksa": "KSA[geo_loc_name]"})
    assert qs[0] == ("country:saudi arabia", '"saudi arabia"[geo_loc_name]')
    assert qs[1] == ("country:ksa", "KSA[geo_loc_name]")  # override wins
    assert qs[2] == ("org:kaust", "KAUST[Organization]")


def test_esearch_params_wire_shape():
    p = esearch_params('"Saudi Arabia"[geo_loc_name]', 500, retstart=500, mindate="2026-01-01")
    assert p == {
        "db": "biosample",
        "term": '"Saudi Arabia"[geo_loc_name]',
        "retmode": "json",
        "retmax": "500",
        "retstart": "500",
        "datetype": "edat",
        "mindate": "2026-01-01",
    }


def _esearch_body(ids):
    return json.dumps({"esearchresult": {"count": str(len(ids)), "idlist": ids}}).encode()


def test_esearch_pages_stops_on_short_page():
    calls = []

    def fetch(path, params):
        calls.append(params["retstart"])
        ids = {"0": ["1", "2"], "2": ["4"]}[params["retstart"]]
        return 200, _esearch_body(ids)

    client = NcbiClient(fetch=fetch)
    pages = list(client.esearch_pages("term", page_size=2))
    assert pages == [(0, ["1", "2"]), (2, ["4"])]
    assert calls == ["0", "2"]


def test_esearch_pages_resumes_from_retstart():
    def fetch(path, params):
        return 200, _esearch_body(["9"] if params["retstart"] == "2" else ["1", "2"])

    client = NcbiClient(fetch=fetch)
    assert list(client.esearch_pages("term", page_size=2, retstart=2)) == [(2, ["9"])]


def test_retry_on_transient_status_and_transport_error():
    attempts = []
    sleeps = []

    def fetch(path, params):
        attempts.append(1)
        if len(attempts) == 1:
            return 429, b"slow down"
        if len(attempts) == 2:
            raise httpx.TransportError("boom")
        return 200, _esearch_body(["1"])

    client = NcbiClient(fetch=fetch, sleeper=sleeps.append, backoff_base=2.0)
    assert client.esearch("term", 1)["idlist"] == ["1"]
    assert len(attempts) == 3
    assert sleeps == [2.0, 4.0]


def test_efetch_rejects_empty_id_list():
    client = NcbiClient(fetch=lambda p, q: (200, b""))
    with pytest.raises(ValueError, match="at least one id"):
        client.efetch([])


def test_parse_fixture_biosample_set():
    els = parse_biosample_set((FIXTURES / "ncbi_biosample_set.xml").read_text())
    assert [e.get("accession") for e in els] == ["SAMN65762509", "SAMN65761091"]
