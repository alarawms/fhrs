"""INSDC BioSample structure (NCBI XML / DDBJ JSON-LD) → FHRS flat payload dict.

Both NCBI (E-utilities BioSample XML) and DDBJ (search-API ``properties.BioSample``)
describe samples in the same INSDC BioSample structure; DDBJ's JSON-LD layer only
wraps single-valued text in ``{"content": ...}``. One normaliser serves both, and its
output carries exactly the keys ``fhrs.connectors.insdc.normalize`` reads — so a fix
to the field mapping lands on every archive at once (ported from the predecessor
system's verified ``BiosampleNormalizer``).

Country handling: NCBI/DDBJ expose only ``geo_loc_name`` (a free location: a country,
"City, Country", or worse). It is emitted as ``location`` — never as ``country`` —
because ``classify`` treats a non-empty ``country`` it cannot normalise as an
explicit OTHER country and drops the record, which would wrongly drop e.g.
geo_loc_name="Saudi Arabian Gulf" samples whose coordinates land in the EEZ. The
canonical ``country`` is injected separately by ``apply_country_from_geo`` (exact
match, never substring) at harvest time, where the GeoContext is available.
"""

from typing import Any

from fhrs.rules.coords import parse_lat_lon
from fhrs.rules.geo import GeoContext, normalise_country

# INSDC "unknown value" spellings — a lat_lon of "missing" is not a location string.
_MISSING_TOKENS = {
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


def _txt(v: Any) -> str | None:
    """Coerce an NCBI-XML or DDBJ-JSON-LD node to its text content, if any."""
    if v is None:
        return None
    if isinstance(v, dict):
        if "content" in v:
            return _txt(v["content"])
        return None
    if isinstance(v, (list, tuple)):
        for item in v:
            t = _txt(item)
            if t:
                return t
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    if hasattr(v, "text"):  # xml.etree.Element
        t = v.text
        return t.strip() if isinstance(t, str) and t.strip() else None
    s = str(v).strip()
    return s or None


def _attr_name(a: Any) -> str | None:
    if isinstance(a, dict):
        return _txt(a.get("harmonized_name")) or a.get("attribute_name") or a.get("name")
    return a.get("harmonized_name") or a.get("attribute_name") or a.get("name")


def _attr_value(a: Any) -> str | None:
    if isinstance(a, dict):
        return _txt(a.get("content"))
    return _txt(a.text)


def _iter_children(el: Any, tag: str) -> list[Any]:
    if el is None:
        return []
    try:
        return [c for c in el if getattr(c, "tag", None) == tag]
    except TypeError:
        return []


def _plausible_accession(s: str) -> bool:
    """Secondary Ids must look like accessions — short, whitespace-free. This keeps
    submitter free-text (sample names, strain descriptions) out of the alias table."""
    return len(s) <= 40 and not any(c.isspace() for c in s)


class BiosampleNormalizer:
    """Turn an NCBI ``<BioSample>`` Element or a DDBJ ``properties.BioSample`` dict
    into the flat payload dict ``fhrs.connectors.insdc.normalize`` consumes."""

    def __init__(self, accession: str) -> None:
        self.accession = (accession or "").strip()

    def flat(self, node: Any) -> dict[str, Any]:
        is_el = hasattr(node, "find")
        out: dict[str, Any] = {"sample_accession": self.accession}

        # Ids → secondary accession (SRA sample id, GenBank, ...). The primary
        # BioSample Id is the accession itself; skip it. Entries WITHOUT a database
        # (`db`, or `namespace` on the DDBJ JSON-LD side) are not accessions at all —
        # e.g. NCBI's `<Id db_label="Sample name">` is the submitter's free-text
        # sample name, which must never become an accession alias.
        secondary: list[str] = []
        if is_el:
            for i in _iter_children(node.find("Ids"), "Id"):
                content = _txt(i.text)
                if i.get("db") == "BioSample" and i.get("is_primary") == "1":
                    continue
                if content and i.get("db") and _plausible_accession(content):
                    secondary.append(content)
        else:
            for i in (node.get("Ids") or {}).get("Id") or []:
                if not isinstance(i, dict):
                    continue
                content = _txt(i.get("content"))
                db = i.get("db") or i.get("namespace")
                if db == "BioSample" and str(i.get("is_primary")) == "1":
                    continue
                if content and db and _plausible_accession(content):
                    secondary.append(content)
        if secondary:
            out["secondary_sample_accession"] = secondary[0]

        # Description → title (feeds study_text for Tier-C institution evidence),
        # organism → scientific_name.
        if is_el:
            desc = node.find("Description")
            title_el = desc.find("Title") if desc is not None else None
            title = _txt(title_el.text) if title_el is not None else None
            org = desc.find("Organism") if desc is not None else None
            name_el = org.find("OrganismName") if org is not None else None
            tax_name = _txt(name_el.text) if name_el is not None else None
            if tax_name is None and org is not None:
                tax_name = org.get("taxonomy_name")
        else:
            desc = node.get("Description") or {}
            title = _txt(desc.get("Title"))
            org = desc.get("Organism") or {}
            tax_name = _txt(org.get("OrganismName")) or org.get("taxonomy_name")
        if title:
            out["description"] = title
        if tax_name:
            out["scientific_name"] = tax_name

        # Owner → center_name (submitting institution; feeds Tier-C evidence).
        owner = None
        if is_el:
            o = node.find("Owner")
            if o is not None:
                n = o.find("Name")
                if n is not None:
                    owner = _txt(n.findtext("Institution")) or _txt(n.text)
        else:
            o = node.get("Owner") or {}
            owner = _txt(o.get("Name"))
        if owner:
            out["center_name"] = owner

        # Package → checklist id (DDBJ carries Models instead; the payload builder
        # fills `checklist` from Models when Package is absent).
        if is_el:
            pkg = node.find("Package")
            checklist = _txt(pkg.text) if pkg is not None else None
        else:
            checklist = _txt(node.get("Package"))
        if checklist:
            out["checklist"] = checklist

        # Links → bioproject accession (the sample's study). DDBJ carries the link
        # in the entry's top-level dbXrefs instead; the payload builder adds it.
        if is_el:
            links = node.find("Links")
            for link in list(links) if links is not None else []:
                if link.tag == "Link" and link.get("target") == "bioproject":
                    study = link.get("label") or _txt(link.text)
                    if study:
                        out["study_accession"] = study
                    break
        else:
            links = (node.get("Links") or {}).get("Link") or []
            if isinstance(links, dict):
                links = [links]
            for link in links:
                if isinstance(link, dict) and link.get("target") == "bioproject":
                    study = link.get("label") or _txt(link.get("content"))
                    if study:
                        out["study_accession"] = study
                    break

        # Attributes → geo_loc_name / lat_lon / collection_date (+ pass-through).
        if is_el:
            a = node.find("Attributes")
            attrs = [c for c in a if c.tag == "Attribute"] if a is not None else []
        else:
            raw = (node.get("Attributes") or {}).get("Attribute")
            if isinstance(raw, dict):
                raw = [raw]
            attrs = raw or []
        for a in attrs:
            name = _attr_name(a)
            value = _attr_value(a)
            if not name or not value:
                continue
            if name in ("geo_loc_name", "geo_loc_name_locality"):
                out.setdefault("location", value)
            elif name == "lat_lon":
                self._split_lat_lon(out, value)
            elif name == "collection_date":
                out.setdefault("collection_date", value)
            else:
                out.setdefault(name, value)

        # last_updated.
        lu = None
        if is_el:
            lu = node.get("last_update")
            if lu is None:
                st = node.find("Status")
                if st is not None:
                    lu = st.get("when")
        else:
            lu = node.get("last_update") or _txt((node.get("Status") or {}).get("when"))
        if lu:
            out["last_updated"] = lu
        return out

    @staticmethod
    def _split_lat_lon(out: dict[str, Any], value: str) -> None:
        # INSDC lat_lon arrives mostly as hemisphere tokens ("24.1 N 55.4 E"),
        # sometimes comma-decimal or DMS — the rules parser handles all of them.
        parsed = parse_lat_lon(value, None, None)
        if parsed is not None:
            out["lat"], out["lon"] = str(parsed[0]), str(parsed[1])
        elif value and value.strip().lower() not in _MISSING_TOKENS:
            out.setdefault("location", value)


def country_from_geo(geo: str | None, ctx: GeoContext) -> str | None:
    """Canonical country string if ``geo`` names the active country, else None.

    Strict check: the value matches only when the country part — the whole string,
    or the part before the first sub-locality separator ("Saudi Arabia: Jeddah") —
    matches an alias EXACTLY, per ``normalise_country``. A mere substring ("Saudi
    Arabian Gulf") is NOT the country. One fallback: the reversed "Jeddah, Saudi
    Arabia" (city-first) form, checked as the LAST separator component — which still
    rejects "Riyadh, Saudi Arabian Gulf".
    """
    if not geo:
        return None
    if normalise_country(geo, ctx)[0]:
        return ctx.country
    for sep in ctx.sublocality_separators:
        parts = geo.split(sep)
        if len(parts) > 1 and normalise_country(parts[-1], ctx)[0]:
            return ctx.country
    return None


def apply_country_from_geo(payload: dict[str, Any], ctx: GeoContext | None) -> None:
    """Inject the canonical ``country`` into an NCBI/DDBJ sample payload (in place).

    Never overwrites a non-empty ``country``, and never marks a non-country location
    as the country (``country_from_geo`` is exact, not substring).
    """
    if ctx is None or payload.get("country"):
        return
    c = country_from_geo(payload.get("location"), ctx)
    if c:
        payload["country"] = c
