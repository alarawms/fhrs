"""ENA/NCBI/DDBJ payload normalizers.

Stage 0 targets the ENA Portal API "filereport"-style flat JSON shape; the NCBI/DDBJ
package shapes observed in the cross-archive catalogue are handled by the same field
aliases (their checklist ids ride the same registry, N5).
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


def normalize(source: str, payload: dict[str, Any]) -> NormalizedRecord:
    primary = _get(payload, "primary_accession")
    if not primary:
        raise ValueError(f"{source} payload has no sample accession: {sorted(payload)}")
    accessions = {primary}
    secondary = _get(payload, "secondary_accession")
    if secondary:
        accessions.add(secondary)
    return NormalizedRecord(
        source=source,
        primary_accession=primary,
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
