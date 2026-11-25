"""fhrs-stats CLI."""

import argparse
import json
import sys

from fhrs.core.config import get_settings
from fhrs.core.db import init_schema, make_engine, session_scope
from fhrs.stats.build import build_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fhrs-stats")
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="rebuild the stats snapshot (N6)")
    p_build.add_argument(
        "--no-artifact", action="store_true", help="skip writing numbers.json"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = make_engine(settings)
    init_schema(engine)
    with session_scope(engine) as session:
        payload = build_snapshot(
            session,
            settings.country_code,
            numbers_out=None if args.no_artifact else settings.numbers_out,
        )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
