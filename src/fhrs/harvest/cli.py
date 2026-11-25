"""fhrs-harvest CLI: ingest-file, replay, the three INSDC network harvesters (Stage
1 — all write through the same payload store + ingest pipeline, PROMPT.md), and
``schedule``, the long-running loop the compose scheduler service runs.
"""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from fhrs.connectors.ddbj import DdbjClient
from fhrs.connectors.ena import EnaClient
from fhrs.connectors.ncbi import NcbiClient
from fhrs.core.config import get_settings
from fhrs.core.db import init_schema, make_engine, session_scope
from fhrs.harvest.ddbj import harvest_ddbj
from fhrs.harvest.ena import harvest_ena
from fhrs.harvest.ingest import ingest_bytes
from fhrs.harvest.ncbi import harvest_ncbi
from fhrs.harvest.replay import replay
from fhrs.rules.checklists import load_registry
from fhrs.rules.geo import load_geo_context
from fhrs.stats.build import build_snapshot

HARVESTERS = {"ena": harvest_ena, "ncbi": harvest_ncbi, "ddbj": harvest_ddbj}


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


def _client(source: str):
    return {"ena": EnaClient, "ncbi": NcbiClient, "ddbj": DdbjClient}[source]()


def _run_source(source: str, args, settings, engine, geo, registry) -> dict:
    profile = settings.load_profile()
    with session_scope(engine) as session:
        report = HARVESTERS[source](
            session,
            _client(source),
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
    if stats is not None:
        out["stats"] = stats["samples"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fhrs-harvest")
    sub = parser.add_subparsers(dest="command", required=True)

    p_file = sub.add_parser("ingest-file", help="ingest one JSON payload file")
    p_file.add_argument("path", type=Path)
    p_file.add_argument("--source", default="ena", choices=["ena", "ncbi", "ddbj"])

    sub.add_parser("replay", help="reprocess all stored payloads locally (N2)")

    for source in ("ena", "ncbi", "ddbj"):
        p = sub.add_parser(
            source, help=f"harvest {source.upper()} through the payload store (Stage 1)"
        )
        p.add_argument(
            "--full", action="store_true", help="ignore the high-water mark (full re-harvest)"
        )
        p.add_argument(
            "--only-query",
            default=None,
            help="run a single discovery query by key (e.g. center:kaust); implies --full",
        )
        p.add_argument(
            "--max-per-query", type=int, default=None, help="cap rows per query (smoke runs)"
        )
        p.add_argument("--no-stats", action="store_true", help="skip the stats rebuild")

    p_sched = sub.add_parser(
        "schedule",
        help="harvest all sources on a fixed interval, forever (the compose scheduler service)",
    )
    p_sched.add_argument(
        "--interval-hours",
        type=float,
        default=None,
        help="hours between runs (default: FHRS_HARVEST_INTERVAL_HOURS or 24)",
    )
    p_sched.add_argument(
        "--sources",
        default="ena,ncbi,ddbj",
        help="comma-separated sources to harvest each cycle (default: all three)",
    )

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
            report = replay(session, geo=geo, registry=registry, payload_root=settings.payload_dir)
        print(json.dumps(report.__dict__, indent=2))
        return 0

    if args.command in HARVESTERS:
        out = _run_source(args.command, args, settings, engine, geo, registry)
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.command == "schedule":
        interval = args.interval_hours or settings.harvest_interval_hours
        sources = [s.strip() for s in args.sources.split(",") if s.strip() in HARVESTERS]
        if not sources:
            print(f"no valid sources in {args.sources!r}", file=sys.stderr)
            return 2

        class _Args:  # the scheduled run is always incremental, always rebuilds stats
            full = False
            only_query = None
            max_per_query = None
            no_stats = False

        while True:
            started = dt.datetime.now(dt.UTC)
            for source in sources:
                try:
                    out = _run_source(source, _Args(), settings, engine, geo, registry)
                    print(json.dumps({"scheduled": source, **out}, default=str), flush=True)
                except Exception as exc:  # one source's failure must not stop the loop
                    print(
                        json.dumps({"scheduled": source, "error": f"{type(exc).__name__}: {exc}"}),
                        file=sys.stderr,
                        flush=True,
                    )
            elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
            time.sleep(max(0.0, interval * 3600 - elapsed))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
