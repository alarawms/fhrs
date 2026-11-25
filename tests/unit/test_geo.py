"""Country matching, gazetteer, institution matching — against the real SA data.
Known-good coordinates are taken from the predecessor system's geo test suite."""

import pytest

from fhrs.rules.geo import match_institution, normalise_country, point_in_country


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Saudi Arabia", (True, None)),
        ("saudi arabia", (True, None)),
        ("Saudi Arabia: Jeddah", (True, "Jeddah")),
        ("Saudi Arabia:Thuwal", (True, "Thuwal")),
        ("Saudi Arabia - Riyadh", (True, "Riyadh")),
        # a list of countries is not a sub-locality
        ("Saudi Arabia,Oman,UAE", (False, None)),
        # substring containment is not a match
        ("Saudi Arabian Gulf", (False, None)),
        ("Bahrain", (False, None)),
        (None, (False, None)),
        ("", (False, None)),
    ],
)
def test_normalise_country(geo, raw, expected):
    assert normalise_country(raw, geo) == expected


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (24.71, 46.68, "land"),   # Riyadh
        (21.54, 39.17, "land"),   # Jeddah
        (22.31, 39.10, "land"),   # Thuwal / KAUST (coastal)
        (21.00, 38.60, "eez"),    # central Red Sea, Saudi side of the median line
        (30.04, 31.24, None),     # Cairo
        (25.29, 51.53, None),     # Doha
        (35.69, 51.39, None),     # Tehran
        (29.532, 35.007, None),   # Aqaba (clipped by neighbours)
        (0.0, 0.0, None),
    ],
)
def test_point_in_country(geo, lat, lon, expected):
    assert point_in_country(lat, lon, geo) == expected


@pytest.mark.parametrize(
    ("text", "expected_name"),
    [
        ("KAUST", "King Abdullah University of Science and Technology"),
        ("kaust", "King Abdullah University of Science and Technology"),
        ("King Abdulaziz University", "King Abdulaziz University"),
        ("samples sequenced at KACST labs", "King Abdulaziz City for Science and Technology"),
    ],
)
def test_match_institution(geo, text, expected_name):
    inst = match_institution(text, geo)
    assert inst is not None
    assert inst.name == expected_name


def test_short_alias_requires_whole_word(geo):
    # "KAU" embedded in a longer token must not match King Abdulaziz University
    assert match_institution("KAUAI sequencing centre", geo) is None or (
        match_institution("KAUAI sequencing centre", geo).name != "King Abdulaziz University"
    )


def test_gaz_root_loaded(geo):
    assert geo.gaz_root == "GAZ:00005279"
    assert geo.gaz_root in geo.gaz_curies
