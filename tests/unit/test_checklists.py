"""Registry behavior (N5): deliberate buckets, base-id splitting, loud unknowns."""

import pytest
import yaml

from fhrs.rules.checklists import load_registry


def test_registry_loads_and_buckets_are_valid(registry):
    assert "metagenome" in registry.buckets
    assert "other" in registry.buckets
    assert len(registry.mapping) > 50


@pytest.mark.parametrize(
    ("checklist_id", "expected"),
    [
        ("ERC000047", "metagenome"),
        ("ERC000047:v2", "metagenome"),  # version suffix splits at ":"
        ("MIMAG", "metagenome"),
        ("Pathogen.cl", "pathogen"),
        ("MIMARKS.survey.soil.6.0", "soil"),
        ("ERC000013", "host_associated"),
    ],
)
def test_known_checklists(registry, checklist_id, expected):
    assert registry.bucket_for(checklist_id) == expected


def test_unknown_checklist_is_loud_none(registry):
    assert registry.bucket_for("MADEUP.9.9") is None
    assert registry.bucket_for(None) is None
    assert registry.bucket_for("") is None


def test_registry_rejects_undeclared_bucket(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.safe_dump({"buckets": ["a"], "map": {"X": "not-a-bucket"}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="undeclared"):
        load_registry(bad)


def test_every_fixture_checklist_maps_or_is_deliberately_unmapped(registry):
    """Coverage: the fixture corpus exercises both mapped and loudly-unmapped paths."""
    import json
    from pathlib import Path

    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "payloads"
    seen = {}
    for path in sorted(fixtures.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        checklist = payload.get("checklist")
        if checklist:
            seen[checklist] = registry.bucket_for(checklist)
    assert seen["MADEUP.9.9"] is None  # the loud path
    assert all(v is not None for k, v in seen.items() if k != "MADEUP.9.9")
