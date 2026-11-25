"""ENA/NCBI/DDBJ payload normalizers.

Stage 0 targets the ENA Portal API "filereport"-style flat JSON shape; the NCBI/DDBJ
package shapes observed in the cross-archive catalogue are handled by the same field
aliases (their checklist ids ride the same registry, N5).

Cross-archive identity (N3): INSDC BioSamples are cross-listed across the three
members with a per-archive 4th letter — SAMEA (EMBL/ENA), SAMN (NCBI), SAMD (DDBJ) —
over a shared numeric suffix (verified live by the predecessor system 2026-10-01:
SAMEA10033096 ≡ SAMN10033096 ≡ SAMD10033096 are one physical sample). NCBI's
BioSample XML does NOT carry the SAMEA xref, so the shared number — not a stored
cross-reference — is what unions the archives. Every source's primary accession is
therefore canonicalised to the SAMEA + stripped-numeric form here, with the
archive-native string kept in `accessions` (it lands in SampleAlias for links).
"""

from typing import Any

from fhrs.connectors.normalized import NormalizedRecord

_ALIASES: dict[str, tuple[str, ...]] = {
    "primary_accession": ("sample_accession", "accession", "biosample_accession"),
    "secondary_accession": ("secondary_sample_accession", "sra_sample_accession"),
    "study_accession": ("study_accession", "bioproject_accession", "secondary_study_accession"),
    "study_title": ("study_title", "study_alias"),
    "study_text": ("description", "study_description", "sample_description"),
    "country": ("country", "geographic_location_country", "geo_loc_name"),
    "location": ("location", "lat_lon", "geographic_location_latitude_and_longitude"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "longitude"),
    "gaz_curie": ("gaz_curie", "gaz"),
    "collection_date": ("collection_date", "collection date", "collection_date_s"),
    "checklist_id": ("checklist", "checklist_id", "ncbi_checklist", "package"),
    "center_name": ("center_name", "center", "submission_center"),
    "scientific_name": ("scientific_name", "taxon_scientific_name", "organism"),
    "library_strategy": ("library_strategy", "library strategy"),
}


def _get(payload: dict[str, Any], key: str) -> str | None:
    for alias in _ALIASES[key]:
        v = payload.get(alias)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


# Archive prefixes for INSDC BioSamples (the 4th letter selects the member archive).
_SAMPLE_ARCHIVE_PREFIXES = ("SAMEA", "SAMN", "SAMD", "SAME", "SAM")
_CANONICAL_SAMPLE_PREFIX = "SAMEA"


def canonical_sample_accession(accession: str | None) -> str | None:
    """Map an INSDC BioSample accession from any member to the canonical SAMEA form.

    SAMN10033096 / SAMD10033096 → SAMEA10033096. Leading zeros in the numeric
    suffix are stripped: the archives pad inconsistently (verified live by the
    predecessor system 2026-10-13 — NCBI returns SAMN03995865 for the sample
    ENA/DDBJ carry as SAMEA3995865), so the numeric value, not the string, is the
    dedup unit. A non-BioSample accession (unknown form) is returned unchanged so
    nothing is silently mangled.
    """
    if not accession:
        return accession
    a = accession.strip().upper()
    for p in _SAMPLE_ARCHIVE_PREFIXES:
        if a.startswith(p) and a[len(p) :].isdigit():
            num = a[len(p) :].lstrip("0") or "0"
            return f"{_CANONICAL_SAMPLE_PREFIX}{num}"
    return accession


def normalize(source: str, payload: dict[str, Any]) -> NormalizedRecord:
    primary = _get(payload, "primary_accession")
    if not primary:
        raise ValueError(f"{source} payload has no sample accession: {sorted(payload)}")
    canonical = canonical_sample_accession(primary) or primary
    accessions = {canonical, primary}
    secondary = _get(payload, "secondary_accession")
    if secondary:
        accessions.add(secondary)
        canonical_secondary = canonical_sample_accession(secondary)
        if canonical_secondary:
            accessions.add(canonical_secondary)
    return NormalizedRecord(
        source=source,
        primary_accession=canonical,
        accessions=frozenset(accessions),
        study_accession=_get(payload, "study_accession"),
        study_title=_get(payload, "study_title"),
        study_text=_get(payload, "study_text"),
        country=_get(payload, "country"),
        location=_get(payload, "location"),
        lat=_get(payload, "lat"),
        lon=_get(payload, "lon"),
        gaz_curie=_get(payload, "gaz_curie"),
        collection_date=_get(payload, "collection_date"),
        checklist_id=_get(payload, "checklist_id"),
        center_name=_get(payload, "center_name"),
        scientific_name=_get(payload, "scientific_name"),
        library_strategy=_get(payload, "library_strategy"),
    )
