#!/usr/bin/env python3
"""Import registered PERF counters for slices already present in the database.

The database is the source of truth for runs, slices, and ``simulator_err.txt``
locations.  By default every run/slice observation is imported; repeatable
filters make the same command useful for smaller experiments and repairs.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.parsers import load_registry, parse_perf_counters  # noqa: E402
from tools.export_dashboard_counters import export_dashboard_counters  # noqa: E402


DEFAULT_DB = ROOT / "outputs/mainline-september/mainline-performance.sqlite"
DEFAULT_AUDIT = ROOT / "outputs/mainline-september/selection-audit.json"
DEFAULT_OUTPUT = ROOT / "outputs/mainline-september/perf-counters.json"
_WORKER_REGISTRY: dict[str, Any] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--registry", type=Path)
    parser.add_argument(
        "--workload", action="append", default=[],
        help="only import this workload (repeatable; default: all)",
    )
    parser.add_argument(
        "--run", action="append", default=[],
        help="only import a run directory name or short commit (repeatable; default: all)",
    )
    parser.add_argument(
        "--slice-glob", default="*", help="slice-name glob (default: *)",
    )
    parser.add_argument(
        "--jobs", type=int, default=min(8, os.cpu_count() or 1),
        help="parallel log parsers (default: min(8, CPU count))",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="abort instead of recording parse_error observations when a log cannot be read",
    )
    return parser.parse_args()


def _initialize_worker(registry: dict[str, Any]) -> None:
    global _WORKER_REGISTRY
    _WORKER_REGISTRY = registry


def _unavailable_values(
    registry: dict[str, Any], source: Path, availability: str
) -> list[dict[str, Any]]:
    return [
        {
            "metric_id": definition["metric_id"],
            "semantic_version": definition["semantic_version"],
            "raw_value": None,
            "numerator": None,
            "denominator": None,
            "value": None,
            "availability": availability,
            "window_id": "perf_final_dump",
            "dump_time": None,
            "source_uri": str(source),
        }
        for definition in registry["metrics"]
    ]


def _parse_job(job: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    assert _WORKER_REGISTRY is not None
    source = Path(job["source_err_uri"])
    try:
        if not source.is_file():
            return job, _unavailable_values(_WORKER_REGISTRY, source, "missing"), "missing log"
        return job, parse_perf_counters(source, _WORKER_REGISTRY), None
    except (OSError, ValueError) as error:
        return job, _unavailable_values(_WORKER_REGISTRY, source, "parse_error"), str(error)


def _load_jobs(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT r.run_id, c.short_sha AS short_commit, r.source_uri AS run_source,
                   s.slice_id, s.slice_name AS slice, s.workload,
                   sr.source_err_uri
              FROM slice_results sr
              JOIN runs r ON r.run_id = sr.run_id
              JOIN commits c ON c.commit_sha = r.commit_sha
              JOIN slices s ON s.slice_id = sr.slice_id
             ORDER BY c.commit_epoch, s.workload, s.slice_name
            """
        )
    ]
    workloads = set(args.workload)
    runs = set(args.run)
    selected = []
    for row in rows:
        run_name = Path(str(row["run_source"])).name
        if workloads and row["workload"] not in workloads:
            continue
        if runs and run_name not in runs and row["short_commit"] not in runs:
            continue
        if not fnmatch(str(row["slice"]), args.slice_glob):
            continue
        selected.append(row)
    return selected


def _upsert_definitions(
    connection: sqlite3.Connection, registry: dict[str, Any]
) -> None:
    for definition in registry["metrics"]:
        connection.execute(
            """
            INSERT INTO metric_definitions(
                metric_id, semantic_version, display_name, category, kind, unit,
                direction, source_match_json, aggregation_rule
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_id, semantic_version) DO UPDATE SET
                display_name=excluded.display_name, category=excluded.category,
                kind=excluded.kind, unit=excluded.unit, direction=excluded.direction,
                source_match_json=excluded.source_match_json,
                aggregation_rule=excluded.aggregation_rule
            """,
            (
                definition["metric_id"], definition["semantic_version"],
                definition["display_name"], definition["category"], definition["kind"],
                definition["unit"], definition["direction"],
                json.dumps(
                    {
                        "path": definition["source_path"],
                        "name": definition["source_name"],
                        "expected_matches": definition["expected_matches"],
                    },
                    sort_keys=True,
                ),
                definition["aggregation_rule"],
            ),
        )


def import_counters(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_registry(args.registry)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        jobs = _load_jobs(connection, args)
        if not jobs:
            raise RuntimeError("no run/slice observations matched the requested scope")
        print(
            f"importing {len(jobs)} logs × {len(registry['metrics'])} metrics "
            f"with {max(1, args.jobs)} workers",
            flush=True,
        )
        parsed: list[tuple[dict[str, Any], list[dict[str, Any]], str | None]] = []
        if args.jobs == 1:
            _initialize_worker(registry)
            iterator = map(_parse_job, jobs)
            for index, result in enumerate(iterator, 1):
                parsed.append(result)
                if index % 100 == 0 or index == len(jobs):
                    print(f"parsed {index}/{len(jobs)}", flush=True)
        else:
            with ProcessPoolExecutor(
                max_workers=max(1, args.jobs),
                initializer=_initialize_worker,
                initargs=(registry,),
            ) as executor:
                for index, result in enumerate(executor.map(_parse_job, jobs, chunksize=4), 1):
                    parsed.append(result)
                    if index % 100 == 0 or index == len(jobs):
                        print(f"parsed {index}/{len(jobs)}", flush=True)

        errors = [
            {"run": row["short_commit"], "slice": row["slice"], "error": error}
            for row, _values, error in parsed if error
        ]
        unavailable_before_import = [
            (row["short_commit"], row["slice"], value["metric_id"], value["availability"])
            for row, values, _error in parsed
            for value in values
            if value["availability"] != "available"
        ]
        if args.strict and (errors or unavailable_before_import):
            detail = errors[0] if errors else unavailable_before_import[0]
            raise RuntimeError(
                f"strict import rejected {len(errors)} log errors and "
                f"{len(unavailable_before_import)} unavailable metrics; first issue: {detail}"
            )

        available = 0
        imported = 0
        with connection:
            _upsert_definitions(connection, registry)
            connection.executemany(
                "DELETE FROM counter_values WHERE run_id = ? AND slice_id = ?",
                ((row["run_id"], row["slice_id"]) for row, _values, _error in parsed),
            )
            for row, values, _error in parsed:
                for value in values:
                    connection.execute(
                        """
                        INSERT INTO counter_values(
                            run_id, slice_id, metric_id, semantic_version, raw_value,
                            numerator, denominator, value, availability, window_id,
                            dump_time, source_uri
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["run_id"], row["slice_id"], value["metric_id"],
                            value["semantic_version"], value["raw_value"], value["numerator"],
                            value["denominator"], value["value"], value["availability"],
                            value["window_id"], value["dump_time"], value["source_uri"],
                        ),
                    )
                    imported += 1
                    available += value["availability"] == "available"
    finally:
        connection.close()

    export_summary = export_dashboard_counters(args.db, args.output)
    summary = {
        "database": str(args.db),
        "output": str(args.output),
        "files": len(jobs),
        "runs": len({row["run_id"] for row in jobs}),
        "workloads": len({row["workload"] for row in jobs}),
        "slices": len({row["slice_id"] for row in jobs}),
        "metrics": len(registry["metrics"]),
        "observations": imported,
        "available": available,
        "unavailable": imported - available,
        "parse_errors": errors,
        "export": export_summary,
    }
    if args.audit.is_file():
        audit = json.loads(args.audit.read_text(encoding="utf-8"))
        audit["counter_import"] = summary
        args.audit.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return summary


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise SystemExit(f"database does not exist: {args.db}")
    try:
        summary = import_counters(args)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    printable = {**summary, "parse_errors": len(summary["parse_errors"])}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
