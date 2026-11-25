"""parse_lat_lon as a property: never raises, output sane (acceptance invariant 2)."""

from hypothesis import given
from hypothesis import strategies as st

from fhrs.rules.coords import parse_lat_lon

TEXT = st.one_of(st.none(), st.text())


@given(location=TEXT, lat=TEXT, lon=TEXT)
def test_never_raises_and_stays_in_range(location, lat, lon):
    result = parse_lat_lon(location, lat, lon)
    if result is not None:
        la, lo = result
        assert -90 <= la <= 90
        assert -180 <= lo <= 180
        assert not (la == 0.0 and lo == 0.0)  # null island is never a coordinate
