"""End-to-end ingest over the fixture corpus: N1 (non-destructive merge), N3
(identity resolution before visibility), N4 (no sentinel studies), N5 (loud
unmapped queue), and the tier → visibility mapping."""

from sqlalchemy import func, select

from fhrs.core.models import (
    AttributeHistory,
    CurationEvent,
    MergeEvent,
    Sample,
    SampleAlias,
    SourceEvent,
    Study,
    UnmappedChecklist,
)


def _sample(session, accession):
    return session.scalar(select(Sample).where(Sample.canonical_accession == accession))


def test_corpus_counts(corpus, session):
    report = corpus
    assert report.events == 6
    assert report.samples_created == 4
    assert report.samples_merged == 1
    assert report.dropped_out_of_scope == 1
    assert report.unmapped_checklists == ["MADEUP.9.9"]
    assert session.scalar(select(func.count()).select_from(Sample)) == 4


def test_n1_weak_payload_never_erases(session, corpus):
    report = corpus
    s = _sample(session, "SAMEA1110001")
    # The third payload knew almost nothing; everything the first payload taught
    # us must still be there.
    assert s.lat == 22.31 and s.lon == 39.10
    assert s.collection_date == "2023-05-14"
    # Its weaker conflicting country string lost (first-writer-wins) and is audited.
    assert s.country_raw == "Saudi Arabia: Thuwal"
    conflict = session.scalar(
        select(AttributeHistory).where(
            AttributeHistory.sample_id == s.id,
            AttributeHistory.key == "country_raw",
            AttributeHistory.action == "conflict_kept",
        )
    )
    assert conflict is not None
    assert conflict.new_value == "Saudi Arabia"
    assert report.conflicts_kept >= 1
    # "erased" is not an action the audit table can hold.
    actions = set(session.scalars(select(AttributeHistory.action)).all())
    assert "erased" not in actions


def test_n3_identity_resolved_before_visibility(session, corpus):
    s = _sample(session, "SAMEA1110001")
    aliases = set(
        session.scalars(select(SampleAlias.accession).where(SampleAlias.sample_id == s.id))
    )
    assert aliases == {"SAMEA1110001", "SAMN2220001"}
    merge = session.scalar(
        select(MergeEvent).where(MergeEvent.canonical_sample_id == s.id)
    )
    assert merge is not None and merge.merged_accession == "SAMN2220001"
    # one canonical record — the second accession never created a twin
    assert session.scalar(select(func.count()).select_from(Sample)) == 4


def test_n4_no_sentinel_studies(session, corpus):
    assert session.scalar(select(func.count()).select_from(Study)) == 2  # PRJEB1000, PRJDB9000
    assert session.scalar(
        select(func.count()).select_from(Study).where(Study.accession.like("NOSTUDY%"))
    ) == 0
    # the study-less tier B sample is represented honestly: NULL, not a placeholder
    assert _sample(session, "SAMEA1110002").study_id is None


def test_tiers_visibility_and_curation_flag(session, corpus):
    by_accession = {
        a: _sample(session, a)
        for a in ("SAMEA1110001", "SAMEA1110002", "SAMD3330001", "SAMEA1110003")
    }
    assert by_accession["SAMEA1110001"].tier == "A"
    assert by_accession["SAMEA1110002"].tier == "B"
    assert by_accession["SAMEA1110003"].tier == "A"
    c = by_accession["SAMD3330001"]
    assert c.tier == "C" and c.visibility == "curation" and c.needs_geo_curation
    for a in ("SAMEA1110001", "SAMEA1110002", "SAMEA1110003"):
        assert by_accession[a].visibility == "public"
    flag = session.scalar(
        select(CurationEvent).where(
            CurationEvent.sample_id == c.id, CurationEvent.status == "open"
        )
    )
    assert flag is not None and flag.action == "flag_for_review"


def test_n5_unmapped_checklist_is_loud(session, corpus):
    row = session.get(UnmappedChecklist, "MADEUP.9.9")
    assert row is not None and row.occurrences == 1
    assert _sample(session, "SAMEA1110003").content_bucket is None
    assert _sample(session, "SAMEA1110002").content_bucket == "metagenome"


def test_dropped_records_are_counted_not_stored(session, corpus):
    assert session.scalar(
        select(func.count()).select_from(Sample).where(
            Sample.canonical_accession == "SAMN4440001"
        )
    ) == 0
    event = session.scalar(
        select(SourceEvent).where(SourceEvent.accession == "SAMN4440001")
    )
    assert event.tier is None


def test_reingest_same_payload_is_idempotent(session, corpus, geo, registry, payload_root):
    from pathlib import Path

    from fhrs.harvest.ingest import ingest_bytes

    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "payloads"
    report = ingest_bytes(
        session,
        (fixtures / "01-ena-tier-a.json").read_bytes(),
        "ena",
        geo=geo,
        registry=registry,
        payload_root=payload_root,
    )
    assert report.events == 0 and report.skipped_duplicates == 1
    assert session.scalar(select(func.count()).select_from(Sample)) == 4


def test_replay_does_not_double_count_unmapped(session, corpus, geo, registry, payload_root):
    from fhrs.harvest.replay import replay
    from fhrs.stats.build import build_snapshot

    before = build_snapshot(session, "SA")
    report = replay(session, geo=geo, registry=registry, payload_root=payload_root)
    assert report.events == 6
    after = build_snapshot(session, "SA")
    assert after["unmapped_checklists"] == [{"checklist_id": "MADEUP.9.9", "occurrences": 1}]
    assert after["samples"] == before["samples"]
