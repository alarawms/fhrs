#!/usr/bin/env python3
"""Demo: ingest the fixture corpus through the real pipeline and build the stats
snapshot. Uses FHRS_DATABASE_URL if set, else a local sqlite file.

    python scripts/demo_ingest.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fhrs.core.config import Settings, get_settings
from fhrs.core.db import init_schema, make_engine, session_scope
from fhrs.harvest.ingest import ingest_bytes
from fhrs.rules.checklists import load_registry
from fhrs.rules.geo import load_geo_context
from fhrs.stats.build import build_snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "payloads"
ORDER = [
    ("01-ena-tier-a.json", "ena"),
    ("02-ena-tier-b.json", "ena"),
    ("03-ncbi-crossref-weak.json", "ncbi"),
    ("04-ddbj-tier-c.json", "ddbj"),
    ("05-ena-unmapped-checklist.json", "ena"),
    ("06-ncbi-country-conflict.json", "ncbi"),
]


def main() -> int:
    try:
        settings = get_settings()
    except Exception:
        settings = Settings(
            country_code="SA",
            database_url="sqlite:///./db/demo.sqlite",
            payload_dir=Path("./data/payloads"),
            numbers_out=Path("./build/numbers.json"),
        )
    profile = settings.load_profile()
    print(f"country lens: {profile.code} ({profile.name})")
    geo = load_geo_context(
        profile.data_path("aliases"),
        profile.data_path("institutions"),
        profile.data_path("gazetteer"),
        profile.data_path("boundary"),
    )
    registry = load_registry(settings.resolved_registry_path())
    engine = make_engine(settings)
    init_schema(engine)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        for i, (name, source) in enumerate(ORDER):
            report = ingest_bytes(
                session,
                (FIXTURES / name).read_bytes(),
                source,
                geo=geo,
                registry=registry,
                payload_root=settings.payload_dir,
                fetched_at=base + timedelta(days=i),
            )
            print(f"  {source:5s} {name}: +{report.samples_created} created, "
                  f"{report.samples_merged} merged, {report.dropped_out_of_scope} dropped"
                  + (f", unmapped {report.unmapped_checklists}" if report.unmapped_checklists else ""))
        payload = build_snapshot(session, settings.country_code, settings.numbers_out)

    s = payload["samples"]
    print(f"\nsnapshot: {s['public']} public + {s['in_curation']} in curation "
          f"(tiers {s['by_tier']}), {payload['studies']} studies, "
          f"{payload['dropped_out_of_scope']} out-of-scope, "
          f"coords {s['with_coordinates']}, buckets {s['by_content_type']}")
    print(f"numbers artifact: {settings.numbers_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
