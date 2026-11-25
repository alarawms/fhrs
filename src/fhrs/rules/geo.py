"""Geographic reference data for national inclusion. All matching data comes from the
active country profile's data files; nothing here hard-codes a country spelling,
polygon or institution (that is what makes the country a switch, PROMPT.md).
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Institution:
    name: str
    aliases: tuple[str, ...]
    ror: str | None
    country_check: bool


@dataclass(frozen=True)
class GeoContext:
    country: str
    country_aliases: frozenset[str]
    sublocality_separators: tuple[str, ...]
    gaz_root: str
    gaz_curies: frozenset[str]
    institutions: tuple[Institution, ...]
    land: BaseGeometry
    eez: BaseGeometry
    _land_p: PreparedGeometry
    _eez_p: PreparedGeometry


def _norm(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


@lru_cache(maxsize=8)
def load_geo_context(
    aliases_path: Path, institutions_path: Path, gaz_path: Path, boundary_path: Path
) -> GeoContext:
    aliases_doc = yaml.safe_load(Path(aliases_path).read_text(encoding="utf-8"))
    inst_doc = yaml.safe_load(Path(institutions_path).read_text(encoding="utf-8"))
    gaz_doc = json.loads(Path(gaz_path).read_text(encoding="utf-8"))
    fc = json.loads(Path(boundary_path).read_text(encoding="utf-8"))
    feats = {f["properties"]["name"]: shape(f["geometry"]) for f in fc["features"]}
    land, eez = feats["land_and_territorial_waters"], feats["marine_eez"]
    return GeoContext(
        country=aliases_doc["country"],
        country_aliases=frozenset(_norm(a) for a in aliases_doc["aliases"]),
        sublocality_separators=tuple(aliases_doc["sublocality_separators"]),
        gaz_root=gaz_doc["root"],
        gaz_curies=frozenset(gaz_doc["terms"].keys()) | {gaz_doc["root"]},
        institutions=tuple(
            Institution(
                i["name"], tuple(i["aliases"]), i.get("ror"), bool(i.get("country_check", True))
            )
            for i in inst_doc["institutions"]
        ),
        land=land,
        eez=eez,
        _land_p=prep(land),
        _eez_p=prep(eez),
    )


def normalise_country(raw: str | None, ctx: GeoContext) -> tuple[bool, str | None]:
    """(is_country, sublocality). Exact alias match on the country part — never substring."""
    if not raw:
        return False, None
    text = _norm(raw)
    # Whole-string alias first, so a hyphenated alias ("saudi-arabia") is never split.
    if text in ctx.country_aliases:
        return True, None
    for sep in ctx.sublocality_separators:
        # Collapse internal whitespace only; never strip (" - " must not become "-").
        s = _WS.sub(" ", sep)
        if not s or s not in text:
            continue
        head, _, tail = text.partition(s)
        if _norm(head) not in ctx.country_aliases:
            continue
        # A list delimiter (",") only introduces a sub-locality when the tail is not itself a
        # list: "Saudi Arabia, Jeddah" yes; "Saudi Arabia,Oman,UAE" no. The INSDC ":" form
        # keeps commas in its tail ("Saudi Arabia: Al-Safa, Jeddah").
        if s == "," and ("," in tail or ":" in tail):
            continue
        sub = tail.strip() or None
        if sub is not None:
            # return the sub-locality with original casing when possible
            idx = raw.lower().find(sub)
            sub = raw[idx : idx + len(sub)].strip() if idx >= 0 else sub
        return True, sub
    return False, None


def point_in_country(lat: float, lon: float, ctx: GeoContext) -> Literal["land", "eez"] | None:
    p = Point(lon, lat)
    if ctx._land_p.covers(p):
        return "land"
    if ctx._eez_p.covers(p):
        return "eez"
    return None


def match_institution(text: str | None, ctx: GeoContext) -> Institution | None:
    if not text:
        return None
    hay = _norm(text)
    for inst in ctx.institutions:
        for alias in inst.aliases:
            a = _norm(alias)
            # short all-caps aliases (KSU, KAU, NEOM) must match as whole words
            pattern = rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])" if len(a) <= 6 else re.escape(a)
            if re.search(pattern, hay):
                return inst
    return None
