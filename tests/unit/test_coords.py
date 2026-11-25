"""Golden cases for every INSDC lat_lon form observed in the wild (acceptance
invariant 2) — ported from the predecessor system's coordinate backfill audit."""

import pytest

from fhrs.rules.coords import parse_lat_lon


@pytest.mark.parametrize(
    ("location", "lat", "lon", "expected"),
    [
        # separate decimal fields
        (None, "22.31", "39.10", (22.31, 39.1)),
        # decimal with hemisphere letters
        ("24.7136 N 46.6753 E", None, None, (24.7136, 46.6753)),
        ("24.7136N 46.6753E", None, None, (24.7136, 46.6753)),
        # DMS space-separated
        ("22 18 0 N 39 6 0 E", None, None, (22.3, 39.1)),
        # DMS with symbols
        ("24°42'49\"N 46°40'31\"E", None, None, (24.713611, 46.675278)),
        # plain decimal pair
        ("24.7136, 46.6753", None, None, (24.7136, 46.6753)),
        ("24.7136; 46.6753", None, None, (24.7136, 46.6753)),
        # degree symbols
        ("22.31° N, 39.10° E", None, None, (22.31, 39.1)),
        # southern/western hemispheres flip signs
        ("33.92 S 18.42 W", None, None, (-33.92, -18.42)),
        ("-33.92, -18.42", None, None, (-33.92, -18.42)),
        # missing / junk → None, never raises
        (None, None, None, None),
        ("", None, None, None),
        ("missing", None, None, None),
        ("not collected", None, None, None),
        ("gibberish", None, None, None),
        ("24.7136", None, None, None),
        # out of range → None
        ("91.0 N 46.0 E", None, None, None),
        (None, "91.0", "46.0", None),
        # null island is not a coordinate
        ("0 0 N 0 0 E", None, None, None),
        (None, "0.0", "0.0", None),
    ],
)
def test_parse_lat_lon(location, lat, lon, expected):
    assert parse_lat_lon(location, lat, lon) == expected
