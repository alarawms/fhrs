"""fhrs-harvest CLI: ingest-file and replay. Network harvesters arrive in Stage 1 and
must write through the same payload store + this pipeline (PROMPT.md stage plan).
"""

import argparse
import json
import sys
from pathlib import Path

from fhrs.core.config import get_settings
from fhrs.core.db import init_schema, make_engine, session_scope
from fhrs.harvest.ingest import ingest_bytes
from fhrs.harvest.replay import replay
from fhrs.rules.checklists import load_registry
from fhrs.rules.geo import load_geo_context


def _wiring():
    settings = get_settings()
    profile = settings.load_profile()
    geo = load_geo_context(
        profile.data_path("aliases"),
        profile.data_path("institutions"),
        profile.data_path("gazetteer"),
        profile.data_path("boundary"),
    )
    registry = load_registry(settings.resolved_registry_path())
    engine = make_engine(settings)
    init_schema(engine)
    return settings, engine, geo, registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fhrs-harvest")
    sub = parser.add_subparsers(dest="command", required=True)

    p_file = sub.add_parser("ingest-file", help="ingest one JSON payload file")
    p_file.add_argument("path", type=Path)
    p_file.add_argument("--source", default="ena", choices=["ena", "ncbi", "ddbj"])

    sub.add_parser("replay", help="reprocess all stored payloads locally (N2)")

    args = parser.parse_args(argv)
    settings, engine, geo, registry = _wiring()

    if args.command == "ingest-file":
        with session_scope(engine) as session:
            report = ingest_bytes(
                session,
                args.path.read_bytes(),
                args.source,
                geo=geo,
                registry=registry,
                payload_root=settings.payload_dir,
            )
        print(json.dumps(report.__dict__, indent=2))
        return 0

    if args.command == "replay":
        with session_scope(engine) as session:
            report = replay(
                session, geo=geo, registry=registry, payload_root=settings.payload_dir
            )
        print(json.dumps(report.__dict__, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
