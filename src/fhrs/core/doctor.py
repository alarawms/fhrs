"""fhrs doctor — startup validation (PROMPT.md N9). Fails fast on a bad environment
instead of serving silently degraded behavior. Exits non-zero when any check fails.
"""

import argparse
import shutil
import sys
from dataclasses import dataclass

from sqlalchemy import inspect, text

from fhrs.core.config import get_settings
from fhrs.core.db import make_engine
from fhrs.rules.checklists import load_registry


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(check_db: bool = True) -> list[Check]:
    checks: list[Check] = []

    try:
        settings = get_settings()
        checks.append(Check("settings", True, f"country={settings.country_code}"))
    except Exception as exc:
        return [Check("settings", False, str(exc))]

    try:
        profile = settings.load_profile()
        checks.append(
            Check(
                "country_profile",
                True,
                f"{profile.code} ({profile.name}), {len(profile.languages)} language(s), "
                f"data files present",
            )
        )
    except Exception as exc:
        checks.append(Check("country_profile", False, str(exc)))

    try:
        registry = load_registry(settings.resolved_registry_path())
        checks.append(
            Check("checklist_registry", True, f"{len(registry.mapping)} checklists mapped")
        )
    except Exception as exc:
        checks.append(Check("checklist_registry", False, str(exc)))

    try:
        settings.payload_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.payload_dir / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("payload_store", True, str(settings.payload_dir)))
    except Exception as exc:
        checks.append(Check("payload_store", False, str(exc)))

    usage = shutil.disk_usage(settings.payload_dir)
    used_pct = round(100 * usage.used / usage.total)
    checks.append(
        Check(
            "disk_watermark",
            used_pct <= settings.disk_watermark_pct,
            f"{used_pct}% used of {usage.total // (1 << 30)} GiB "
            f"(watermark {settings.disk_watermark_pct}%)",
        )
    )

    if check_db:
        try:
            engine = make_engine(settings)
            with engine.connect() as conn:
                conn.execute(text("select 1"))
            tables = set(inspect(engine).get_table_names())
            missing = {"samples", "source_events", "stats_snapshots"} - tables
            detail = "schema present" if not missing else f"missing tables: {sorted(missing)}"
            checks.append(Check("database", not missing, detail))
        except Exception as exc:
            checks.append(Check("database", False, str(exc)))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fhrs-doctor")
    parser.add_argument("--no-db", action="store_true", help="skip the database check")
    args = parser.parse_args(argv)
    checks = run_checks(check_db=not args.no_db)
    failed = 0
    for c in checks:
        mark = "ok " if c.ok else "FAIL"
        print(f"[{mark}] {c.name}: {c.detail}")
        failed += 0 if c.ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
