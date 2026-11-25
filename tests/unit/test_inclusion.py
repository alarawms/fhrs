"""Tier classification (the national footprint definition)."""

from fhrs.rules.inclusion import classify


def test_tier_a_country_alias(geo):
    d = classify("Saudi Arabia: Thuwal", None, "22.31", "39.10", None, None, None, geo)
    assert d.tier == "A"
    assert d.evidence[0]["rule"] == "country_alias"
    assert d.evidence[0]["sublocality"] == "Thuwal"
    assert d.marine_eez is False
    assert d.needs_geo_curation is False


def test_tier_a_gaz_descendant(geo):
    curie = min(c for c in geo.gaz_curies if c != geo.gaz_root)
    d = classify(None, None, None, None, curie, None, None, geo)
    assert d.tier == "A"
    assert d.evidence[0]["rule"] == "gaz_descendant"


def test_tier_b_coordinates_land(geo):
    d = classify(None, None, "24.71", "46.68", None, None, None, geo)
    assert d.tier == "B"
    assert d.marine_eez is False
    assert d.evidence[0]["rule"] == "coordinates"


def test_tier_b_coordinates_eez_flagged(geo):
    d = classify(None, None, "21.0", "38.6", None, None, None, geo)
    assert d.tier == "B"
    assert d.marine_eez is True


def test_explicit_other_country_blocks_coordinates(geo):
    # Coordinates inside the country + country="Bahrain" is a conflict, not evidence.
    d = classify("Bahrain", None, "24.71", "46.68", None, None, None, geo)
    assert d.tier is None
    assert d.evidence[0]["rule"] == "country_conflict"


def test_missing_country_does_not_block_tier_b(geo):
    d = classify("not collected", None, "24.71", "46.68", None, None, None, geo)
    assert d.tier == "B"


def test_tier_c_institution_inferred_needs_curation(geo):
    # KAUST's own aliases are unambiguous (country_check=false) → "institution"
    d = classify("not collected", None, None, None, None, "KAUST", None, geo)
    assert d.tier == "C"
    assert d.needs_geo_curation is True
    assert d.evidence[0]["rule"] == "institution"


def test_tier_c_hint_only_institution(geo):
    # KAU's short alias collides with other abbreviations (country_check=true), so
    # the evidence is a hint — still tier C, still gated by the N8 policy guards.
    d = classify(
        "not collected", None, None, None, None, "King Abdulaziz University", None, geo
    )
    assert d.tier == "C"
    assert d.needs_geo_curation is True
    assert d.evidence[0]["rule"] == "institution_hint_only"


def test_no_signal_is_out_of_scope(geo):
    d = classify(None, None, None, None, None, "unrelated center", None, geo)
    assert d.tier is None
