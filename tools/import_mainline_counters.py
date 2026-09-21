#!/usr/bin/env python3
"""Import registered PERF counters for the selected mainline mcf slices."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.parsers import load_registry, parse_perf_counters, parse_profile, parse_score  # noqa: E402
from commit_ipc_trend.schema import canonical_hash  # noqa: E402


DEFAULT_DB = ROOT / "outputs/mainline-september/mainline-performance.sqlite"
DEFAULT_AUDIT = ROOT / "outputs/mainline-september/selection-audit.json"
DEFAULT_OUTPUT = ROOT / "outputs/mainline-september/perf-counters.json"
SCORE_NAME = "score-spec06-rva23-novec-gcc16-1.0c.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--registry", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    registry = load_registry(args.registry)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        metrics = registry["metrics"]
        for definition in metrics:
            connection.execute(
                """
                INSERT INTO metric_definitions(
                    metric_id, semantic_version, display_name, category, kind, unit,
                    direction, source_match_json, aggregation_rule
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric_id, semantic_version) DO UPDATE SET
                    display_name=excluded.display_name,
                    category=excluded.category,
                    kind=excluded.kind,
                    unit=excluded.unit,
                    direction=excluded.direction,
                    source_match_json=excluded.source_match_json,
                    aggregation_rule=excluded.aggregation_rule
                """,
                (
                    definition["metric_id"], definition["semantic_version"],
                    definition["display_name"], definition["category"], definition["kind"],
                    definition["unit"], definition["direction"],
                    json.dumps({
                        "path": definition["source_path"],
                        "name": definition["source_name"],
                        "expected_matches": definition["expected_matches"],
                    }, sort_keys=True),
                    definition["aggregation_rule"],
                ),
            )

        run_by_source = {
            row["source_uri"]: row
            for row in connection.execute("SELECT * FROM runs")
        }
        slice_by_name = {
            row["slice_name"]: row["slice_id"]
            for row in connection.execute("SELECT slice_id, slice_name FROM slices")
            if row["slice_name"].startswith("mcf_")
        }
        selected = audit["selected_runs"]
        imported = 0
        available = 0
        files = 0
        with connection:
            for directory in selected:
                run_dir = Path(audit["candidates"] and next(
                    row["score_file"] for row in audit["candidates"]
                    if row["directory"] == directory
                )).parent
                run = run_by_source[str(run_dir)]
                score_path = run_dir / SCORE_NAME
                _scores, score_info = parse_score(score_path)
                checkpoint_version = score_info["Checkpoint Version"]
                profile_path = Path(checkpoint_version).parent / "json" / "mcf.json"
                profile = parse_profile(profile_path, "mcf")
                for checkpoint in sorted(profile["points"]):
                    candidates = sorted(run_dir.glob(f"mcf_{checkpoint}_*"))
                    if len(candidates) != 1:
                        raise RuntimeError(f"expected one mcf checkpoint in {run_dir}: {checkpoint}")
                    slice_dir = candidates[0]
                    slice_id = slice_by_name.get(slice_dir.name)
                    if not slice_id:
                        raise RuntimeError(f"slice missing from database: {slice_dir.name}")
                    source = slice_dir / "simulator_err.txt"
                    values = parse_perf_counters(source, registry)
                    files += 1
                    for value in values:
                        connection.execute(
                            """
                            INSERT INTO counter_values(
                                run_id, slice_id, metric_id, semantic_version, raw_value,
                                numerator, denominator, value, availability, window_id,
                                dump_time, source_uri
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(run_id, slice_id, metric_id, semantic_version)
                            DO UPDATE SET raw_value=excluded.raw_value, value=excluded.value,
                                availability=excluded.availability, window_id=excluded.window_id,
                                dump_time=excluded.dump_time, source_uri=excluded.source_uri
                            """,
                            (
                                run["run_id"], slice_id, value["metric_id"], value["semantic_version"],
                                value["raw_value"], value["numerator"], value["denominator"],
                                value["value"], value["availability"], value["window_id"],
                                value["dump_time"], value["source_uri"],
                            ),
                        )
                        imported += 1
                        available += value["availability"] == "available"

        commits = [dict(row) for row in connection.execute(
            """
            SELECT r.run_id, c.commit_sha, c.short_sha AS short_commit, c.commit_time,
                   c.commit_epoch, c.subject, r.config, r.status
              FROM runs r JOIN commits c ON c.commit_sha = r.commit_sha
             ORDER BY c.commit_epoch
            """
        )]
        slices = [dict(row) for row in connection.execute(
            """
            SELECT s.slice_id, s.slice_name AS slice, s.benchmark, s.workload,
                   s.checkpoint, sm.weight, sm.ordinal
              FROM slices s JOIN slice_set_members sm ON sm.slice_id = s.slice_id
             WHERE s.workload = 'mcf'
             ORDER BY sm.ordinal
            """
        )]
        values = [dict(row) for row in connection.execute(
            """
            SELECT cv.run_id, c.short_sha AS short_commit, s.slice_name AS slice,
                   cv.metric_id, cv.semantic_version, cv.value, cv.availability,
                   cv.window_id, cv.dump_time
              FROM counter_values cv
              JOIN runs r ON r.run_id = cv.run_id
              JOIN commits c ON c.commit_sha = r.commit_sha
              JOIN slices s ON s.slice_id = cv.slice_id
             WHERE s.workload = 'mcf'
             ORDER BY c.commit_epoch, s.checkpoint, cv.metric_id
            """
        )]
    finally:
        connection.close()

    payload = {
        "schema_version": "counter-dashboard/v1",
        "database": str(args.db),
        "scope": "selected September mainline runs · mcf slices",
        "metric_count": len(metrics),
        "observation_count": len(values),
        "metrics": metrics,
        "commits": commits,
        "slices": slices,
        "values": values,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    summary = {
        "output": str(args.output), "files": files, "metrics": len(metrics),
        "slices": len(slices), "observations": imported, "available": available,
    }
    audit["counter_import"] = summary
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
