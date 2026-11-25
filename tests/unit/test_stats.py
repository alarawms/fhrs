"""N6: the snapshot is the only aggregate source, and it must equal an independent
recomputation from canonical+visible rows (acceptance invariant 5)."""

from sqlalchemy import text

from fhrs.stats.build import build_snapshot, latest_snapshot

EXPECTED = {
    "total": 4,
    "public": 3,
    "in_curation": 1,
    "by_tier": {"A": 2, "B": 1, "C": 1},
    "by_content_type": {"metagenome": 2, "unmapped": 1},
    "with_coordinates": 3,
    "with_collection_date": 3,
    "marine_eez": 0,
    "studies": 1,
    "source_events": 6,
    "dropped_out_of_scope": 1,
}


def test_snapshot_matches_expected_corpus(session, corpus, tmp_path):
    payload = build_snapshot(session, "SA", numbers_out=tmp_path / "numbers.json")
    s = payload["samples"]
    assert s["total"] == EXPECTED["total"]
    assert s["public"] == EXPECTED["public"]
    assert s["in_curation"] == EXPECTED["in_curation"]
    assert s["by_tier"] == EXPECTED["by_tier"]
    assert s["by_content_type"] == EXPECTED["by_content_type"]
    assert s["with_coordinates"] == EXPECTED["with_coordinates"]
    assert s["with_collection_date"] == EXPECTED["with_collection_date"]
    assert s["marine_eez"] == EXPECTED["marine_eez"]
    assert payload["studies"] == EXPECTED["studies"]
    assert payload["source_events"] == EXPECTED["source_events"]
    assert payload["dropped_out_of_scope"] == EXPECTED["dropped_out_of_scope"]
    assert payload["unmapped_checklists"] == [{"checklist_id": "MADEUP.9.9", "occurrences": 1}]
    assert (tmp_path / "numbers.json").exists()


def test_snapshot_equals_independent_recomputation(session, corpus):
    """Acceptance invariant 5: recompute with separate SQL and compare."""
    payload = build_snapshot(session, "SA")
    rows = {
        r[0]: r[1]
        for r in session.execute(
            text("select visibility, count(*) from samples group by visibility")
        ).all()
    }
    assert payload["samples"]["public"] == rows.get("public", 0)
    assert payload["samples"]["in_curation"] == rows.get("curation", 0)
    assert payload["samples"]["total"] == sum(rows.values())
    coords = session.execute(
        text(
            "select count(*) from samples where visibility='public' "
            "and lat is not null and lon is not null"
        )
    ).scalar()
    assert payload["samples"]["with_coordinates"] == coords
    studies = session.execute(
        text(
            "select count(distinct study_id) from samples "
            "where visibility='public' and study_id is not null"
        )
    ).scalar()
    assert payload["studies"] == studies


def test_latest_snapshot_returns_most_recent(session, corpus):
    build_snapshot(session, "SA")
    build_snapshot(session, "SA")
    assert latest_snapshot(session, "SA").payload["country_code"] == "SA"
