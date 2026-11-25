"""DDBJ connector: query builders, the date-window planner (the 10k deep-paging
wall workaround), and client paging — no network."""

import datetime as dt

import pytest

from fhrs.connectors.ddbj import (
    DateWindow,
    DdbjClient,
    discovery_queries,
    plan_date_windows,
)

D0 = dt.date(2026, 1, 1)


def _population_counts(pop: dict[dt.date, int]):
    def count(a: dt.date, b: dt.date) -> int:
        return sum(v for d, v in pop.items() if a <= d <= b)

    return count


def test_discovery_queries_overrides_and_defaults():
    qs = discovery_queries(["saudi", "ksa", "saudi arabia"], ["King Saud"], {"saudi": "Saudi"})
    assert qs[0] == ("country:saudi", "geoLocName", "Saudi")  # override (prefix form)
    assert qs[1] == ("country:ksa", "geoLocName", "ksa")  # default: alias as-is
    assert qs[2] == ("country:saudi arabia", "geoLocName", "saudi arabia")
    assert qs[3] == ("org:king saud", "organization", "King Saud")


def test_plan_windows_empty_range():
    assert plan_date_windows(_population_counts({}), D0, D0 + dt.timedelta(days=9)) == []
    assert plan_date_windows(_population_counts({D0: 5}), D0 + dt.timedelta(days=1), D0) == []


def test_plan_windows_single_when_under_cap():
    pop = {D0: 100, D0 + dt.timedelta(days=2): 100}
    windows = plan_date_windows(_population_counts(pop), D0, D0 + dt.timedelta(days=2))
    assert windows == [DateWindow(D0, D0 + dt.timedelta(days=2))]


def test_plan_windows_bisects_until_each_fits():
    pop = {D0 + dt.timedelta(days=i): 6000 for i in range(3)}
    windows = plan_date_windows(_population_counts(pop), D0, D0 + dt.timedelta(days=2))
    # 18000 over a 9000 cap → bisected to single days
    assert windows == [
        DateWindow(D0 + dt.timedelta(days=i), D0 + dt.timedelta(days=i)) for i in range(3)
    ]


def test_plan_windows_single_day_over_cap_is_emitted():
    # the API cannot subdivide a day; the residual surfaces via reconcile instead
    pop = {D0: 20000}
    windows = plan_date_windows(
        _population_counts(pop), D0, D0 + dt.timedelta(days=5), max_records=9000
    )
    assert windows == [DateWindow(D0, D0)]


def test_plan_windows_rejects_bad_args():
    with pytest.raises(ValueError, match="positive"):
        plan_date_windows(_population_counts({}), D0, D0, max_records=0)


def _page(items, page, has_next):
    return {"items": items, "pagination": {"page": page, "hasNext": has_next, "total": 3}}


def test_search_pages_follows_has_next():
    bodies = {
        "1": _page([{"identifier": "SAMD1"}], 1, True),
        "2": _page([{"identifier": "SAMD2"}, {"identifier": "SAMD3"}], 2, False),
    }

    def fetch(path, params):
        import json

        return 200, json.dumps(bodies[params["page"]]).encode()

    client = DdbjClient(fetch=fetch)
    pages = list(client.search_pages("biosample", field="geoLocName", value="Saudi"))
    assert [[i["identifier"] for i in p["items"]] for p in pages] == [["SAMD1"], ["SAMD2", "SAMD3"]]


def test_search_total_reads_pagination():
    import json

    client = DdbjClient(fetch=lambda p, q: (200, json.dumps(_page([], 1, False)).encode()))
    assert client.search_total("biosample", field="geoLocName", value="Saudi") == 3


def test_404_is_lookup_error_not_crash():
    client = DdbjClient(fetch=lambda p, q: (404, b"no"))
    with pytest.raises(LookupError):
        client.search("biosample", field="geoLocName", value="Saudi")
