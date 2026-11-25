"""National inclusion tiers. Pure function over source fields + GeoContext.

Tier A — explicit: country/geo_loc_name normalises to the active country, or a GAZ
         CURIE that is that country or a descendant.
Tier B — coordinates inside land+territorial waters (marine_eez=False) or the EEZ
         (marine_eez=True).
Tier C — inferred: no country/coords, but center_name or study text names a national
         institution. needs_geo_curation=True → enters the curation queue (N8 guards
         decide what happens next).
None   — out of scope for this country's lens (counted as dropped, never stored).

An explicit OTHER country (a value that names some other place) blocks Tier B/C:
coordinates inside the country with country="<neighbour>" is a data conflict, not
evidence.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from fhrs.rules.coords import parse_lat_lon
from fhrs.rules.geo import GeoContext, match_institution, normalise_country, point_in_country

Tier = Literal["A", "B", "C"]
_MISSING = {
    "",
    "missing",
    "not collected",
    "not applicable",
    "na",
    "n/a",
    "unknown",
    "not provided",
    "restricted access",
}


@dataclass
class Decision:
    tier: Tier | None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    marine_eez: bool = False
    needs_geo_curation: bool = False


def _explicit_other_country(country_raw: str | None) -> bool:
    if not country_raw:
        return False
    return country_raw.strip().lower() not in _MISSING


def classify(
    country_raw: str | None,
    location: str | None,
    lat: str | None,
    lon: str | None,
    gaz_curie: str | None,
    center_name: str | None,
    study_text: str | None,
    ctx: GeoContext,
) -> Decision:
    evidence: list[dict[str, Any]] = []
    coords = parse_lat_lon(location, lat, lon)
    where = point_in_country(*coords, ctx) if coords else None

    is_national, sub = normalise_country(country_raw, ctx)
    if is_national:
        evidence.append(
            {
                "rule": "country_alias",
                "field": "country",
                "value": country_raw,
                "matched": ctx.country,
                "sublocality": sub,
            }
        )
        if coords and where is None:
            evidence.append(
                {
                    "rule": "coordinates_outside_country",
                    "field": "location",
                    "value": coords,
                    "matched": None,
                }
            )
        return Decision("A", evidence, marine_eez=(where == "eez"))

    if gaz_curie and gaz_curie in ctx.gaz_curies:
        evidence.append(
            {
                "rule": "gaz_descendant",
                "field": "gaz_curie",
                "value": gaz_curie,
                "matched": ctx.gaz_root,
            }
        )
        return Decision("A", evidence, marine_eez=(where == "eez"))

    if _explicit_other_country(country_raw):
        # names another place (or a multi-country blob) → out of lens, whatever the
        # coordinates say
        if where:
            evidence.append(
                {
                    "rule": "country_conflict",
                    "field": "country",
                    "value": country_raw,
                    "matched": where,
                }
            )
        return Decision(None, evidence)

    if where:
        evidence.append(
            {"rule": "coordinates", "field": "location", "value": coords, "matched": where}
        )
        return Decision("B", evidence, marine_eez=(where == "eez"))

    for fld, text in (("center_name", center_name), ("study_text", study_text)):
        inst = match_institution(text, ctx)
        if inst:
            rule = "institution_hint_only" if inst.country_check else "institution"
            evidence.append(
                {
                    "rule": rule,
                    "field": fld,
                    "value": text,
                    "matched": inst.name,
                    "ror": inst.ror,
                }
            )
            return Decision("C", evidence, needs_geo_curation=True)

    return Decision(None, evidence)
