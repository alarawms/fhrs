"""Acceptance invariant 7: the country is a switch. Boot the whole stack against a
second, minimal country profile — no code changes — and /config must reflect it."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from fhrs.api.main import create_app
from fhrs.core.config import Settings
from fhrs.core.db import create_engine, init_schema
from fhrs.rules.geo import load_geo_context
from fhrs.rules.inclusion import classify

PROFILE = {
    "code": "ZZ",
    "name": "Zedland",
    "name_local": "",
    "languages": ["en"],
    "branding": {"site_name": "FHRS — Zedland", "tagline": "test lens", "mark": "ZZ"},
    "data": {
        "aliases": "aliases.yaml",
        "institutions": "institutions.yaml",
        "gazetteer": "gaz.json",
        "boundary": "boundary.geojson",
    },
    "footprint": {"tiers": ["A", "B", "C"]},
    "sources": {"insdc": ["ena"]},
}

SQUARE = lambda x1, y1, x2, y2: {
    "type": "Polygon",
    "coordinates": [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]],
}


@pytest.fixture()
def zz_countries_dir(tmp_path):
    data_dir = tmp_path / "countries" / "zz"
    data_dir.mkdir(parents=True)
    (tmp_path / "countries" / "zz.yaml").write_text(yaml.safe_dump(PROFILE))
    (data_dir / "aliases.yaml").write_text(
        yaml.safe_dump(
            {
                "country": "Zedland",
                "gaz_root": "GAZ:99999999",
                "sublocality_separators": [":", ","],
                "aliases": ["zedland"],
            }
        )
    )
    (data_dir / "institutions.yaml").write_text(
        yaml.safe_dump(
            {
                "institutions": [
                    {"name": "Zed University", "aliases": ["Zed University"], "ror": None}
                ]
            }
        )
    )
    (data_dir / "gaz.json").write_text(json.dumps({"root": "GAZ:99999999", "terms": {}}))
    (data_dir / "boundary.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "land_and_territorial_waters"},
                        "geometry": SQUARE(0, 0, 10, 10),
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "marine_eez"},
                        "geometry": SQUARE(10, 0, 20, 10),
                    },
                ],
            }
        )
    )
    return tmp_path / "countries"


def test_profile_loads_for_new_country(zz_countries_dir):
    settings = Settings(
        country_code="ZZ",
        database_url="sqlite://",
        payload_dir=zz_countries_dir / "p",
        numbers_out=zz_countries_dir / "n.json",
        countries_dir=zz_countries_dir,
    )
    profile = settings.load_profile()
    assert profile.code == "ZZ" and profile.name == "Zedland"


def test_classification_works_for_new_country(zz_countries_dir):
    data = zz_countries_dir / "zz"
    geo = load_geo_context(
        data / "aliases.yaml",
        data / "institutions.yaml",
        data / "gaz.json",
        data / "boundary.geojson",
    )
    assert classify("Zedland", None, None, None, None, None, None, geo).tier == "A"
    assert classify(None, None, "5", "5", None, None, None, geo).tier == "B"
    assert classify(None, None, "5", "15", None, None, None, geo).marine_eez is True
    assert classify(None, None, None, None, None, "Zed University", None, geo).tier == "C"
    assert classify("Elsewhere", None, None, None, None, None, None, geo).tier is None


def test_config_endpoint_reflects_new_country(zz_countries_dir, tmp_path):
    settings = Settings(
        country_code="ZZ",
        database_url=f"sqlite:///{tmp_path / 'zz.db'}",
        payload_dir=tmp_path / "payloads",
        numbers_out=tmp_path / "numbers.json",
        countries_dir=zz_countries_dir,
    )
    engine = create_engine(settings.database_url)
    init_schema(engine)
    engine.dispose()
    client = TestClient(create_app(settings))
    cfg = client.get("/config").json()
    assert cfg["country_code"] == "ZZ"
    assert cfg["name"] == "Zedland"
    assert cfg["branding"]["site_name"] == "FHRS — Zedland"
