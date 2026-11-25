"""fhrs-harvest CLI: ingest-file and replay. Network harvesters arrive in Stage 1 and
must write through the same payload store + this pipeline (PROMPT.md stage plan).
"""

import argparse
import json
import sys
from pathlib import Path

from fhrs.connectors.ena import EnaClient
from fhrs.core.config import get_settings
from fhrs.core.db import init_schema, make_engine, session_scope
from fhrs.harvest.ena import harvest_ena
from fhrs.harvest.ingest import ingest_bytes
from fhrs.harvest.replay import replay
from fhrs.rules.checklists import load_registry
from fhrs.rules.geo import load_geo_context
from fhrs.stats.build import build_snapshot


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

    p_ena = sub.add_parser("ena", help="harvest ENA through the payload store (Stage 1)")
    p_ena.add_argument(
        "--full", action="store_true", help="ignore the high-water mark (full re-harvest)"
    )
    p_ena.add_argument(
        "--only-query",
        default=None,
        help="run a single discovery query by key (e.g. center:kaust); implies --full",
    )
    p_ena.add_argument(
        "--max-per-query", type=int, default=None, help="cap rows per query (smoke runs)"
    )
    p_ena.add_argument("--no-stats", action="store_true", help="skip the stats rebuild")

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

    if args.command == "ena":
        profile = settings.load_profile()
        client = EnaClient()
        with session_scope(engine) as session:
            report = harvest_ena(
                session,
                client,
                profile=profile,
                geo=geo,
                registry=registry,
                payload_root=settings.payload_dir,
                full=args.full or args.only_query is not None,
                only_query=args.only_query,
                max_per_query=args.max_per_query,
            )
            stats = None
            if not args.no_stats:
                stats = build_snapshot(session, settings.country_code, settings.numbers_out)
        out = report.__dict__ | {"ingest": report.ingest.__dict__}
        print(json.dumps(out, indent=2, default=str))
        if stats is not None:
            print(json.dumps({"stats": stats["samples"]}, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
