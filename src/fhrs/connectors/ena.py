"""ENA Portal API client + query builders (Stage 1).

Wire facts verified live against the production Portal API by the predecessor
system (2026-09-19/20) and ported deliberately:

- `offset` pagination is gone; `limit=0` returns the entire result set in one
  response. Window client-side if you need pages.
- Wildcards are unreliable in multi-word quoted strings. Verified working forms:
  `country="Saudi*"` style prefixes for country, and for center_name an EXACT
  match for multi-word aliases vs `center_name="*KAUST*"` for single-word ones.
- An empty query string matches EVERYTHING, not nothing — `batched_or` refuses
  empty input at the root.
- `last_updated>=YYYY-MM-DD` scopes incremental harvests.
"""

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://www.ebi.ac.uk/ena/portal/api"
RETRY_STATUSES = {429, 500, 502, 503, 504}


def search_params(result: str, query: str, fields: str = "all") -> dict[str, str]:
    """The exact `/search` query parameters that go on the wire — single source of
    truth, so cassettes and smoke scripts reproduce the same request shape."""
    return {"result": result, "query": query, "fields": fields, "format": "json", "limit": "0"}


def parse_date(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def batched_or(field: str, accessions: list[str]) -> str:
    """`field="A" OR field="B" ...`. Empty input would mean an empty query string,
    and an empty ENA query matches the entire catalogue — reject it here."""
    if not accessions:
        raise ValueError(f"batched_or({field!r}) needs at least one accession; got an empty list")
    return " OR ".join(f'{field}="{a}"' for a in accessions)


def with_since(query: str, since: dt.date | None) -> str:
    return query if since is None else f"({query}) AND last_updated>={since.isoformat()}"


def institution_query(alias: str) -> str:
    """Multi-word aliases match exactly (wildcards in quoted whitespace strings
    return 0 rows); single-word aliases keep the substring wildcard form."""
    if any(c.isspace() for c in alias):
        return f'center_name="{alias}"'
    return f'center_name="*{alias}*"'


def country_queries(aliases: list[str], ena_queries: dict[str, str]) -> list[tuple[str, str]]:
    """(query_key, query) pairs from the profile's country spellings.

    Per-alias `ena_queries` overrides (profile data) carry the wire-verified string
    where the default doesn't work; the default for a whitespace-free alias is the
    prefix form `country="Alias*"`; a multi-word alias without an override is
    skipped — guessing broken wildcard forms silently returns 0 rows.
    """
    out: list[tuple[str, str]] = []
    for alias in aliases:
        key = alias.strip().lower()
        if key in ena_queries:
            out.append((f"country:{key}", ena_queries[key]))
        elif not any(c.isspace() for c in alias.strip()):
            out.append((f"country:{key}", f'country="{alias.strip()}*"'))
    return out


def geo_box(bounds: tuple[float, float, float, float], pad_degrees: float = 0.5) -> str:
    """geo_box1(lat_min, lon_min, lat_max, lon_max) from the boundary envelope
    (shapely bounds: minx, miny, maxx, maxy = lon/lat), padded so borderline
    records aren't lost to polygon precision. Discovery only — inclusion is
    decided by exact polygons in fhrs.rules.inclusion."""
    minx, miny, maxx, maxy = bounds
    return (
        f"geo_box1({miny - pad_degrees:.1f},{minx - pad_degrees:.1f},"
        f"{maxy + pad_degrees:.1f},{maxx + pad_degrees:.1f})"
    )


def geo_box_query(
    bounds: tuple[float, float, float, float],
    country_qs: list[tuple[str, str]],
    pad_degrees: float = 0.5,
) -> tuple[str, str]:
    """The Tier-B discovery query: inside the box but NOT matched by any country
    query (those rows are already covered by the country queries)."""
    box = geo_box(bounds, pad_degrees)
    if not country_qs:
        return ("geo_box", box)
    exclusion = " OR ".join(q for _, q in country_qs)
    return ("geo_box_not_country", f"{box} AND NOT ({exclusion})")


class EnaClient:
    """Thin sync client: whole-set search with retry/back-off. `fetch` is
    injectable so tests replay recorded responses without a network."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        fetch: Callable[[str, dict[str, str]], tuple[int, bytes]] | None = None,
        backoff_base: float = 2.0,
        max_retries: int = 5,
        timeout: float = 120.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.backoff_base, self.max_retries, self.timeout = backoff_base, max_retries, timeout
        self._sleeper = sleeper
        self._http: httpx.Client | None = None
        self._fetch = fetch

    def _default_fetch(self, path: str, params: dict[str, str]) -> tuple[int, bytes]:
        if self._http is None:
            self._http = httpx.Client()
        resp = self._http.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        return resp.status_code, resp.content

    def _get(self, path: str, params: dict[str, str]) -> bytes:
        fetch = self._fetch or self._default_fetch
        attempt = 0
        while True:
            try:
                status, content = fetch(path, params)
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    raise
                self._sleeper(self.backoff_base * (2**attempt))
                attempt += 1
                continue
            if status in RETRY_STATUSES and attempt < self.max_retries:
                self._sleeper(self.backoff_base * (2**attempt))
                attempt += 1
                continue
            if status >= 400:
                raise RuntimeError(f"ENA {path} {status}: {content[:200]!r}")
            return content

    def search(self, result: str, query: str, fields: str = "all") -> list[dict[str, Any]]:
        import json

        content = self._get("/search", search_params(result, query, fields))
        return json.loads(content) if content.strip() else []

    def count(self, result: str, query: str) -> int:
        content = self._get("/count", {"result": result, "query": query})
        return int(content.decode().strip().splitlines()[-1])
