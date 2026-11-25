"""The INSDC BioSample normalizer (NCBI XML + DDBJ JSON-LD branches) over the real
recorded fixtures, plus the strict geo_loc_name → country derivation."""

import json
from pathlib import Path

from fhrs.connectors.biosample import (
    BiosampleNormalizer,
    apply_country_from_geo,
    country_from_geo,
)
from fhrs.connectors.ncbi import parse_biosample_set

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _xml_samples():
    return {
        el.get("accession"): el
        for el in parse_biosample_set((FIXTURES / "ncbi_biosample_set.xml").read_text())
    }


def _ddbj_items():
    body = json.loads((FIXTURES / "ddbj_biosample_page.json").read_text())
    return {i["identifier"]: i for i in body["items"]}


def test_ncbi_xml_branch_flat():
    el = _xml_samples()["SAMN65762509"]
    p = BiosampleNormalizer("SAMN65762509").flat(el)
    assert p["sample_accession"] == "SAMN65762509"
    assert p["scientific_name"] == "Salmonella enterica subsp. enterica"
    assert p["center_name"] == "Central Weqaa Center Laboratory in Riyadh"
    assert p["checklist"] == "Microbe.1.0"  # NCBI Package, not the Models display name
    assert p["study_accession"] == "PRJNA1557942"  # the Link label, not its text
    assert p["location"] == "Saudi Arabia"  # geo_loc_name → location, never country
    assert "country" not in p
    assert p["collection_date"] == "2019-01-10"
    assert p["last_updated"].startswith("2026-11-25")
    assert p["description"]  # sample title feeds study_text (Tier-C evidence)


def test_ddbj_dict_branch_flat():
    item = _ddbj_items()["SAMD00055765"]
    node = item["properties"]["BioSample"]
    p = BiosampleNormalizer("SAMD00055765").flat(node)
    assert p["sample_accession"] == "SAMD00055765"
    assert p["scientific_name"] == "Klebsiella pneumoniae subsp. pneumoniae"
    assert (
        p["center_name"]
        == "Clinical Laboratory Medicine, Kyoto University Graduate School of Medicine"
    )
    assert p["location"] == "Saudi Arabia"
    assert p["collection_date"] == "2011"
    assert "checklist" not in p  # DDBJ carries Models, not Package (payload builder maps it)
    # lat_lon "missing" is not a location string and yields no coordinates
    assert "lat" not in p and "lon" not in p


def test_lat_lon_hemisphere_tokens_split():
    node = {
        "Attributes": {
            "Attribute": [
                {"attribute_name": "lat_lon", "content": "24.1 N 39.1 E"},
            ]
        }
    }
    p = BiosampleNormalizer("SAMD1").flat(node)
    assert float(p["lat"]) == 24.1 and float(p["lon"]) == 39.1
    assert "location" not in p


def test_country_from_geo_strict(geo):
    assert country_from_geo("Saudi Arabia", geo) == "Saudi Arabia"
    assert country_from_geo("Saudi Arabia: Jeddah", geo) == "Saudi Arabia"
    # city-first reversed form matches on the LAST component
    assert country_from_geo("Jeddah, Saudi Arabia", geo) == "Saudi Arabia"
    # a substring is never the country
    assert country_from_geo("Saudi Arabian Gulf", geo) is None
    assert country_from_geo("Riyadh, Saudi Arabian Gulf", geo) is None
    assert country_from_geo("USA: Texas", geo) is None
    assert country_from_geo(None, geo) is None


def test_apply_country_from_geo(geo):
    p = {"location": "Saudi Arabia: Thuwal"}
    apply_country_from_geo(p, geo)
    assert p["country"] == "Saudi Arabia"
    # never overwrites an explicit country
    p2 = {"location": "Saudi Arabia", "country": "Bahrain"}
    apply_country_from_geo(p2, geo)
    assert p2["country"] == "Bahrain"
    # non-country location stays country-less (classify falls through to coords)
    p3 = {"location": "Saudi Arabian Gulf"}
    apply_country_from_geo(p3, geo)
    assert "country" not in p3


def test_biosample_set_parse_defensive():
    assert parse_biosample_set("<not xml") == []
    assert parse_biosample_set("<Other/>") == []
    single = "<BioSample accession='SAMN1'/>"
    assert [e.get("accession") for e in parse_biosample_set(single)] == ["SAMN1"]
