"""Source-agnostic normalized view of one INSDC sample payload."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NormalizedRecord:
    source: str
    primary_accession: str
    accessions: frozenset[str]  # primary + every cross-reference (N3)
    study_accession: str | None = None
    study_title: str | None = None
    study_text: str | None = None
    country: str | None = None
    location: str | None = None
    lat: str | None = None
    lon: str | None = None
    gaz_curie: str | None = None
    collection_date: str | None = None
    checklist_id: str | None = None
    center_name: str | None = None
    scientific_name: str | None = None
    library_strategy: str | None = None
    extra: dict = field(default_factory=dict)
