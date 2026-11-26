"""NCBI E-utilities client + discovery query builders (Stage 1).

Wire facts verified live against the production E-utilities by the predecessor
system (2026-10-01) and ported deliberately:

- Discovery is ``esearch db=biosample`` paged by ``retstart`` (``retmax`` = page
  size), then ``efetch db=biosample`` in bounded id chunks — 500 BioSample XMLs in
  one request is a several-MB download with a wide window for transient failure.
- Country terms ride the ``[geo_loc_name]`` field; multi-word aliases are quoted
  phrase matches ("Saudi Arabia"[geo_loc_name]). Institution terms ride
  ``[Organization]`` — a controlled text index on the submitting institution; the
  acronym and full name are both indexed ("KAUST"[Organization] → 23,260).
- Non-ASCII aliases are never queried with the default rule: NCBI cannot
  phrase-match a quoted Arabic string and backs off to a per-word All-Fields OR —
  verified the hard way 2026-11-26, when one Arabic alias matched 390k+ records
  (a whole-corpus query). Non-ASCII country terms require a wire-verified
  ``ncbi_queries`` override, exactly ENA's posture for multi-word wildcards.
- Incremental caveat: NCBI's ``edat``/``supdate`` date filters do NOT constrain the
  country terms (a 2026 window returned 0 for a project known to contain 2026
  records). An incremental run therefore re-discovers the full corpus; correctness
  comes from the idempotent, payload-hash-keyed ingest (N1/N2), exactly the ENA
  posture for unchanged records. ``mindate`` is still threaded through for API
  symmetry — a future NCBI that honours the filter gets incrementality for free.
- An empty id list is rejected at the source: an empty ``efetch``/``esearch`` term
  is a whole-corpus query, not a no-match (the same failure class ENA's empty query
  documents).
"""

import datetime as dt
import json
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
RETRY_STATUSES = {429, 500, 502, 503, 504}
EFETCH_CHUNK = 100
PAGE_SIZE = 500


def country_term(alias: str) -> str:
    """``"Saudi Arabia"[geo_loc_name]`` — multi-word aliases are quoted phrases."""
    a = alias.strip()
    if any(c.isspace() for c in a):
        return f'"{a}"[geo_loc_name]'
    return f"{a}[geo_loc_name]"


def institution_term(alias: str) -> str:
    """``<alias>[Organization]`` — the controlled index on the submitting body."""
    return f"{alias.strip()}[Organization]"


def discovery_queries(
    aliases: list[str],
    institution_aliases: list[str],
    overrides: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """(query_key, esearch term) pairs: country terms first (reconcilable), then
    institution terms (Tier-C candidates). Per-alias ``ncbi_queries`` overrides in
    the profile's aliases.yaml carry the wire-verified term where the default
    doesn't work.

    Non-ASCII aliases are SKIPPED without an explicit override (verified the hard
    way 2026-11-26: NCBI cannot phrase-match a quoted Arabic string, backs off to
    a per-word All-Fields OR, and one such alias matched 390k+ records — a
    whole-corpus query. Same posture as ENA's multi-word rule: never guess a query
    form for a language the index doesn't handle; require a wire-verified string).
    """
    overrides = overrides or {}
    out: list[tuple[str, str]] = []
    for alias in aliases:
        key = alias.strip().lower()
        if key in overrides:
            out.append((f"country:{key}", overrides[key]))
        elif alias.strip().isascii():
            out.append((f"country:{key}", country_term(alias)))
        # else: skipped loudly — the harvest report counts only emitted queries,
        # and the profile documents which aliases need overrides (template.yaml).
    for alias in institution_aliases:
        key = alias.strip().lower()
        out.append((f"org:{key}", institution_term(alias)))
    return out


def esearch_params(
    term: str, retmax: int, retstart: int = 0, mindate: str | None = None
) -> dict[str, str]:
    """The exact ``esearch.fcgi`` parameters on the wire — single source of truth,
    so cassettes and smoke scripts reproduce the same request shape."""
    params = {
        "db": "biosample",
        "term": term,
        "retmode": "json",
        "retmax": str(retmax),
        "retstart": str(retstart),
    }
    if mindate is not None:
        # Advisory only — see the module docstring's incremental caveat.
        params.update(datetype="edat", mindate=mindate)
    return params


def parse_biosample_set(xml: str) -> list[ET.Element]:
    """The ``<BioSample>`` elements of an efetch response (single or set)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    if root.tag == "BioSampleSet":
        return [c for c in root if c.tag == "BioSample"]
    return [root] if root.tag == "BioSample" else []


class NcbiClient:
    """Thin sync client: esearch pages + efetch chunks with retry/back-off.
    ``fetch`` is injectable so tests replay recorded responses without a network."""

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
                raise RuntimeError(f"NCBI {path} {status}: {content[:200]!r}")
            return content

    def esearch(
        self, term: str, retmax: int, retstart: int = 0, mindate: str | None = None
    ) -> dict[str, Any]:
        content = self._get("/esearch.fcgi", esearch_params(term, retmax, retstart, mindate))
        return json.loads(content)["esearchresult"]

    def efetch(self, ids: list[str]) -> str:
        """Raw BioSample XML for a chunk of numeric uids (or SAMN accessions)."""
        if not ids:
            raise ValueError("efetch needs at least one id")
        content = self._get(
            "/efetch.fcgi", {"db": "biosample", "id": ",".join(ids), "retmode": "xml"}
        )
        return content.decode()

    def esearch_pages(
        self,
        term: str,
        page_size: int = PAGE_SIZE,
        retstart: int = 0,
        mindate: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[tuple[int, list[str]]]:
        """Yield ``(retstart, idlist)`` pages, advancing by ``page_size``. Stops on a
        short/empty page or ``max_pages``. The yielded ``retstart`` is the page's
        own offset so a caller's resume checkpoint (``retstart + page_size``) stays
        in the server's paging units even if a page comes back short mid-stream."""
        if page_size <= 0:
            raise ValueError(f"esearch_pages page_size must be positive, got {page_size}")
        start = retstart
        pages = 0
        while True:
            result = self.esearch(term, page_size, start, mindate)
            idlist: list[str] = result.get("idlist", [])
            if not idlist:
                return
            yield start, idlist
            start += page_size
            pages += 1
            if max_pages and pages >= max_pages:
                return
            if len(idlist) < page_size:
                return


def parse_date(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None
