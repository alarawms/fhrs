"""Coordinate parsing for INSDC `lat_lon`/`location` strings → decimal degrees (WGS84).

Every format observed in the wild is covered by golden tests; a value that cannot be
parsed returns None, never raises (PROMPT.md acceptance invariant 2).
"""

import re

_MISSING = {"", "missing", "not collected", "not applicable", "na", "n/a", "unknown"}
_DEC = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*°?\s*([NS])?[\s,;]+(-?\d+(?:\.\d+)?)\s*°?\s*([EW])?\s*$",
    re.I,
)
_DMS = re.compile(
    r"(\d+)\s*[°\s]\s*(\d+(?:\.\d+)?)\s*['′\s]?\s*(\d+(?:\.\d+)?)?\s*[\"″]?\s*([NSEW])",
    re.I,
)


def _dms(deg: str, minutes: str, seconds: str | None, hemi: str) -> float:
    v = float(deg) + float(minutes) / 60 + (float(seconds) / 3600 if seconds else 0.0)
    return -v if hemi.upper() in "SW" else v


def _valid(lat: float, lon: float) -> tuple[float, float] | None:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return round(lat, 6), round(lon, 6)


def parse_lat_lon(
    location: str | None, lat: str | None, lon: str | None
) -> tuple[float, float] | None:
    if lat and lon:
        try:
            return _valid(float(lat), float(lon))
        except ValueError:
            pass
    if not location:
        return None
    text = location.strip()
    if not text or text.lower() in _MISSING:
        return None
    parts = _DMS.findall(text)
    if len(parts) == 2:
        a = _dms(*parts[0])
        b = _dms(*parts[1])
        la, lo = (a, b) if parts[0][3].upper() in "NS" else (b, a)
        return _valid(la, lo)
    m = _DEC.match(text)
    if m:
        la, hn, lo, he = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
        if hn and hn.upper() == "S":
            la = -abs(la)
        if he and he.upper() == "W":
            lo = -abs(lo)
        return _valid(la, lo)
    return None
