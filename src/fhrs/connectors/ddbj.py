"""DDBJ search-API client + discovery query builders + date-window planner (Stage 1).

The DDBJ search API (``https://ddbj.nig.ac.jp/search/api``) is an
Elasticsearch-backed index over the INSDC union — SAMEA (EMBL), SAMN (NCBI) and
SAMD (DDBJ) BioSamples all appear — which is precisely why cross-archive dedup on
the shared INSDC numeric accession (``insdc.canonical_sample_accession``) is
non-optional: without it one sample is counted once per archive that carries it.

Wire facts verified live by the predecessor system (2026-10-01/11) and ported
deliberately:

- ``geoLocName=<term>`` is a prefix text-match on the sample's geographic location
  (``Saudi`` → ~57k biosamples) — the reconcilable country path. It is NOT a valid
  filter on the ``bioproject`` type (HTTP 422); discovery is Biosample-driven.
- ``organization=<term>`` is the institution field (Tier-C candidates); internal
  whitespace is AND-ed, so pass the most recognised 2-3-token form of each alias.
- ``dateModifiedFrom``/``dateModifiedTo`` are real, *inclusive* filters (unlike
  NCBI's ignored date filters), so a DDBJ incremental run is genuinely bounded, and
  [a..m] + [m+1..b] tiles [a..b] with no gap and no overlap.
- Deep paging is hard-capped at ``page * perPage <= 10000`` (HTTP 400 beyond; a
  full country pass stops at exactly 10,000 records). ``page`` is 1-indexed,
  ``perPage`` max 100. The corpus therefore cannot be walked in one query — each
  query is sliced into date windows whose live count fits under the cap
  (``plan_date_windows``); a restarted run skips finished windows and resumes the
  in-flight one at its checkpointed page.
"""

import datetime as dt
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://ddbj.nig.ac.jp/search/api"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_PER_PAGE = 100
PAGE_SIZE = 50

# The API's deep-paging wall (page*perPage <= 10000) and the margin planned under.
# 9,000 leaves headroom for records whose dateModified moves into a window while it
# is being paged.
DEEP_PAGE_CAP = 10_000
DEFAULT_WINDOW_SIZE = 9_000

# Full-pass floor: a record's dateModified can never precede the epoch.
EPOCH = dt.date(1970, 1, 1)


def discovery_queries(
    aliases: list[str],
    institution_aliases: list[str],
    overrides: dict[str, str] | None = None,
) -> list[tuple[str, str, str]]:
    """(query_key, field, value) triples: country first (geoLocName, reconcilable),
    then institution (organization, Tier-C). Per-alias ``ddbj_queries`` overrides in
    the profile's aliases.yaml carry the wire-verified value where the alias-as-is
    doesn't work (geoLocName is a prefix match, so "Saudi" ≠ "Saudi Arabia")."""
    overrides = overrides or {}
    out: list[tuple[str, str, str]] = []
    for alias in aliases:
        key = alias.strip().lower()
        out.append((f"country:{key}", "geoLocName", overrides.get(key, alias.strip())))
    for alias in institution_aliases:
        key = alias.strip().lower()
        out.append((f"org:{key}", "organization", alias.strip()))
    return out


@dataclass(frozen=True)
class DateWindow:
    start: dt.date  # inclusive
    end: dt.date  # inclusive


def plan_date_windows(
    count: Callable[[dt.date, dt.date], int],
    start: dt.date,
    end: dt.date,
    max_records: int = DEFAULT_WINDOW_SIZE,
) -> list[DateWindow]:
    """Windows covering [start, end] (both ends inclusive) day-for-day.

    A window is emitted when its live ``count(start, end)`` fits ``max_records`` or
    it has been bisected to a single day (a single day over the cap is as far as any
    implementation can get — the API has no offset/cursor). Windows with zero
    records are skipped: nothing is there to page, and a record later modified into
    a skipped span carries a recent dateModified and is caught by the next
    incremental pass.
    """
    if max_records <= 0:
        raise ValueError(f"max_records must be positive, got {max_records}")
    if start > end:
        return []
    windows: list[DateWindow] = []

    def walk(a: dt.date, b: dt.date) -> None:
        n = count(a, b)
        if n == 0:
            return
        if n <= max_records or a == b:
            windows.append(DateWindow(a, b))
            return
        mid = a + (b - a) // 2
        if mid >= b:  # single-day window over the cap
            windows.append(DateWindow(a, b))
            return
        walk(a, mid)
        walk(mid + dt.timedelta(days=1), b)

    walk(start, end)
    return windows


class DdbjClient:
    """Thin sync client for the DDBJ search API with retry/back-off. ``fetch`` is
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
            self._http = httpx.Client(headers={"Accept": "application/json"})
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
            if status == 404:
                raise LookupError(f"ddbj {path} not found (404)")
            if status >= 400:
                raise RuntimeError(f"DDBJ {path} {status}: {content[:200]!r}")
            return content

    def search(
        self,
        entry_type: str,
        *,
        field: str | None = None,
        value: str | None = None,
        date_modified_from: dt.date | None = None,
        date_modified_to: dt.date | None = None,
        page: int = 1,
        per_page: int = MAX_PER_PAGE,
    ) -> dict[str, Any]:
        """One page of ``GET /entries/<type>/`` (parsed JSON: ``items`` +
        ``pagination``). ``field`` is geoLocName|organization."""
        params: dict[str, str] = {
            "page": str(page),
            "perPage": str(max(1, min(per_page, MAX_PER_PAGE))),
        }
        if field is not None and value is not None:
            params[field] = value
        if date_modified_from is not None:
            params["dateModifiedFrom"] = date_modified_from.isoformat()
        if date_modified_to is not None:
            params["dateModifiedTo"] = date_modified_to.isoformat()
        return json.loads(self._get(f"/entries/{entry_type}/", params))

    def search_total(self, entry_type: str, **kw: Any) -> int:
        """The live ``pagination.total`` for a query (one cheap page) — the window
        planner's count and the reconcile step's live total."""
        body = self.search(entry_type, per_page=1, **kw)
        return int(body.get("pagination", {}).get("total", 0))

    def search_pages(
        self,
        entry_type: str,
        *,
        per_page: int = PAGE_SIZE,
        start_page: int = 1,
        max_pages: int | None = None,
        **kw: Any,
    ) -> Iterator[dict[str, Any]]:
        """Yield pages (parsed bodies), advancing the 1-indexed ``page``. Stops when
        ``pagination.hasNext`` is false, on an empty page, or at ``max_pages``."""
        if per_page <= 0:
            raise ValueError(f"search_pages per_page must be positive, got {per_page}")
        page = start_page
        pages = 0
        while True:
            body = self.search(entry_type, page=page, per_page=per_page, **kw)
            if not body.get("items"):
                return
            yield body
            page += 1
            pages += 1
            if max_pages and pages >= max_pages:
                return
            if not body.get("pagination", {}).get("hasNext", False):
                return
