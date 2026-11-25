"""Relational schema (PROMPT.md N1–N4).

- source_events: append-only record of every fetched payload (N1); payloads live in
  the payload store, referenced by path+sha (N2).
- samples: canonical records only. Accession cross-references live in sample_aliases;
  merges are recorded in merge_events and are queryable (N3).
- studies: real studies only. A study-less sample has study_id NULL — sentinel
  studies are forbidden by design (N4).
- sample_attributes + attribute_history: per-key merge with provenance; history rows
  record every fill/replace so "who knew what when" is always answerable (N1).
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tier(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"


class Visibility(str, enum.Enum):
    public = "public"
    curation = "curation"
    hidden = "hidden"


class SourceEvent(Base):
    __tablename__ = "source_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    accession: Mapped[str] = mapped_column(String(64), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload_path: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    # Inclusion verdict for this event: tier letter, or None when out of scope for the
    # active country's lens (counted as dropped, never stored as a sample).
    tier: Mapped[str | None] = mapped_column(String(1), nullable=True)
    reprocessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accession: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default=Visibility.public.value)
    samples: Mapped[list["Sample"]] = relationship(back_populates="study")


class Sample(Base):
    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_accession: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # N4: study-less is NULL, never a sentinel row.
    study_id: Mapped[int | None] = mapped_column(ForeignKey("studies.id"), nullable=True)
    tier: Mapped[str] = mapped_column(String(1))
    visibility: Mapped[str] = mapped_column(String(16), default=Visibility.public.value)
    marine_eez: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    needs_geo_curation: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    checklist_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    collection_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scientific_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    center_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    study: Mapped[Study | None] = relationship(back_populates="samples")
    aliases: Mapped[list["SampleAlias"]] = relationship(back_populates="sample")


class SampleAlias(Base):
    """Every accession that has ever referred to a canonical sample (N3)."""

    __tablename__ = "sample_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accession: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"))
    source: Mapped[str] = mapped_column(String(16))

    sample: Mapped[Sample] = relationship(back_populates="aliases")


class MergeEvent(Base):
    __tablename__ = "merge_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"))
    merged_accession: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("source_events.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SampleAttribute(Base):
    __tablename__ = "sample_attributes"

    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("source_events.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AttributeHistory(Base):
    __tablename__ = "attribute_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text)
    # "filled" (was null) or "replaced" (newer source won a conflict). "erased" is not
    # a value this column can ever hold — null never overwrites (N1).
    action: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("source_events.id"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UnmappedChecklist(Base):
    """N5: loud queue for checklists missing from registry/checklists.yaml."""

    __tablename__ = "unmapped_checklists"

    checklist_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    example_accession: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CurationEvent(Base):
    __tablename__ = "curation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|applied|rejected
    actor: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StatsSnapshot(Base):
    """N6: the single read model. API/web/reports read this, nothing else computes."""

    __tablename__ = "stats_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON)
