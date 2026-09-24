#!/usr/bin/env python3
"""Build a September XiangShan mainline performance database and dashboard data."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.parsers import parse_score  # noqa: E402
from commit_ipc_trend.schema import (  # noqa: E402
    CPI_FORMULA_VERSION,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SCORE_FORMULA_VERSION,
    canonical_hash,
)
from commit_ipc_trend.store import Store  # noqa: E402
from tools.export_dashboard_anomalies import export_dashboard_anomalies  # noqa: E402
from tools.collect_spec06_slice_ipc_trends import (  # noqa: E402
    collect_observations,
    load_runs,
    load_slices,
    parse_ipc,
    render_heatmap,
    summarize_slices,
    transition_summaries,
    workload_trends,
    write_csv,
    write_report,
    write_wide_csv,
)


DEFAULT_REPORT_ROOT = Path("/nfs/home/cirunner/perf-report")
DEFAULT_GIT_REPO = Path("/nfs/home/wujiabin/work/XiangShan")
DEFAULT_CHECKPOINTS = ROOT / "spec06_gcc16_rva23_novec_260820-checkpoints.txt"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/mainline-september"
DEFAULT_MAINLINE_REF = "origin/kunminghu-v3"
DEFAULT_RUNS = (
    "cr260904-0cdabfc7c-DefaultConfig",
    "cr260906-14af55216-DefaultConfig",
    "cr260911-0eea07ed9-DefaultConfig",
    "cr260914-37ce1b50b-DefaultConfig",
    "cr260917-c8d7b3a5c-DefaultConfig",
)
RUN_RE = re.compile(
    r"^cr(?P<date>26(?P<month>\d{2})(?P<day>\d{2}))-"
    r"(?P<sha>[0-9a-f]{7,40})-(?P<label>.+)$"
)
SCORE_NAME = "score-spec06-rva23-novec-gcc16-1.0c.txt"


BENCHMARKS = {
    "perlbench": "400.perlbench",
    "bzip2": "401.bzip2",
    "gcc": "403.gcc",
    "mcf": "429.mcf",
    "gobmk": "445.gobmk",
    "hmmer": "456.hmmer",
    "sjeng": "458.sjeng",
    "libquantum": "462.libquantum",
    "h264ref": "464.h264ref",
    "omnetpp": "471.omnetpp",
    "astar": "473.astar",
    "xalancbmk": "483.xalancbmk",
    "bwaves": "410.bwaves",
    "gamess": "416.gamess",
    "milc": "433.milc",
    "zeusmp": "434.zeusmp",
    "gromacs": "435.gromacs",
    "cactusADM": "436.cactusADM",
    "leslie3d": "437.leslie3d",
    "namd": "444.namd",
    "dealII": "447.dealII",
    "soplex": "450.soplex",
    "povray": "453.povray",
    "calculix": "454.calculix",
    "GemsFDTD": "459.GemsFDTD",
    "tonto": "465.tonto",
    "lbm": "470.lbm",
    "wrf": "481.wrf",
    "sphinx3": "482.sphinx3",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--git-repo", type=Path, default=DEFAULT_GIT_REPO)
    parser.add_argument("--mainline-ref", default=DEFAULT_MAINLINE_REF)
    parser.add_argument("--checkpoints", type=Path, default=DEFAULT_CHECKPOINTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--month", default="09", help="two-digit month in 2026")
    parser.add_argument("--run", action="append", dest="runs")
    return parser.parse_args()


def git_lines(repo: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def first_parent_shas(repo: Path, ref: str) -> set[str]:
    return set(git_lines(repo, "rev-list", "--first-parent", ref))


def resolve_sha(short_sha: str, full_shas: Iterable[str]) -> str | None:
    matches = [sha for sha in full_shas if sha.startswith(short_sha)]
    return matches[0] if len(matches) == 1 else None


def benchmark_for(workload: str) -> str:
    prefix = workload.split("_", 1)[0]
    try:
        return BENCHMARKS[prefix]
    except KeyError as error:
        raise ValueError(f"no SPEC06 benchmark mapping for workload: {workload}") from error


def audit_candidates(
    report_root: Path,
    expected_slices: set[str],
    full_mainline_shas: set[str],
    month: str,
    selected: set[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    pattern = f"cr26{month}*-*Config*"
    for run_dir in sorted(report_root.glob(pattern)):
        match = RUN_RE.fullmatch(run_dir.name)
        if not match:
            continue
        full_sha = resolve_sha(match.group("sha"), full_mainline_shas)
        actual_slices = {path.name for path in run_dir.iterdir() if path.is_dir()}
        missing = len(expected_slices - actual_slices)
        extra = len(actual_slices - expected_slices)
        score_exists = (run_dir / SCORE_NAME).is_file()
        config = match.group("label").split("-", 1)[0]
        reasons: list[str] = []
        if config != "DefaultConfig":
            reasons.append("non_default_config")
        if full_sha is None:
            reasons.append("not_on_mainline_first_parent")
        if missing:
            reasons.append("missing_expected_slices")
        if not score_exists:
            reasons.append("missing_gcc16_score")
        invalid_roi = 0
        if full_sha is not None and config == "DefaultConfig" and not missing and score_exists:
            for slice_name in expected_slices:
                try:
                    parse_ipc(run_dir / slice_name / "simulator_out.txt")
                except (OSError, ValueError):
                    invalid_roi += 1
            if invalid_roi:
                reasons.append("invalid_roi_summaries")
        records.append(
            {
                "directory": run_dir.name,
                "date": f"2026-{month}-{match.group('day')}",
                "short_commit": match.group("sha"),
                "commit_sha": full_sha,
                "config": config,
                "expected_slice_count": len(expected_slices),
                "present_expected_slices": len(expected_slices & actual_slices),
                "missing_expected_slices": missing,
                "extra_directories": extra,
                "invalid_roi_summaries": invalid_roi,
                "score_file": str(run_dir / SCORE_NAME) if score_exists else None,
                "eligible": not reasons,
                "selected": run_dir.name in selected,
                "exclusion_reasons": reasons,
            }
        )
    return records


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_dashboard_files(
    output_dir: Path,
    checkpoint_path: Path,
    report_root: Path,
    slices: list[Any],
    runs: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, str]]]:
    rows, errors = collect_observations(report_root, slices, runs)
    summaries = summarize_slices(slices, rows, len(runs))
    trends = workload_trends(slices, rows, runs)
    transitions = transition_summaries(rows, runs)
    write_csv(output_dir / "slice-ipc-long.csv", rows)
    write_wide_csv(output_dir / "slice-ipc-wide.csv", slices, rows, runs)
    write_csv(output_dir / "slice-summary.csv", summaries)
    write_csv(output_dir / "workload-weighted-trend.csv", trends)
    write_csv(output_dir / "commit-transition-summary.csv", transitions)
    render_heatmap(output_dir / "workload-ipc-heatmap.png", trends, runs)
    write_report(
        output_dir / "README.generated.md",
        slices,
        rows,
        summaries,
        trends,
        transitions,
        runs,
        errors,
    )
    manifest = {
        "schema_version": "mainline-dashboard/v1",
        "checkpoint_file": str(checkpoint_path),
        "report_root": str(report_root),
        "mainline_ref": DEFAULT_MAINLINE_REF,
        "selection_rule": "selected complete DefaultConfig observations on the mainline first-parent chain",
        "slice_count": len(slices),
        "run_count": len(runs),
        "expected_observations": len(slices) * len(runs),
        "valid_observations": len(rows),
        "complete_slice_count": sum(row["status"] == "complete" for row in summaries),
        "errors": errors,
        "runs": runs,
        "method": {
            "ipc_source": "last instrCnt/cycleCnt/IPC ROI summary in simulator_out.txt",
            "commit_order": "Git committer timestamp (%ct), then short SHA",
            "workload_ipc": "1 / (sum(weight * (1 / slice_ipc)) / sum(weight))",
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(
        output_dir / "perf-counters.json",
        {
            "schema_version": "counter-dashboard/v1",
            "metric_count": 0,
            "observation_count": 0,
            "metrics": [],
            "commits": [],
            "slices": [],
            "values": [],
        },
    )
    return rows, trends, errors


def build_database(
    database: Path,
    report_root: Path,
    checkpoint_path: Path,
    slices: list[Any],
    runs: list[dict[str, object]],
    rows: list[dict[str, object]],
    trends: list[dict[str, object]],
) -> dict[str, Any]:
    by_run: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_run.setdefault(str(row["directory"]), []).append(row)
    trend_by_run: dict[str, list[dict[str, object]]] = {}
    short_to_directory = {str(run["short_commit"]): str(run["directory"]) for run in runs}
    for trend in trends:
        trend_by_run.setdefault(short_to_directory[str(trend["short_commit"])], []).append(trend)

    slice_set_id = canonical_hash(
        [(spec.name, spec.checkpoint, spec.weight) for spec in slices]
    )
    members = [
        {
            "slice_id": canonical_hash(
                {"slice_set": "spec06_gcc16_rva23_novec_260820", "slice": spec.name}
            ),
            "benchmark": benchmark_for(spec.workload),
            "workload": spec.workload,
            "slice": spec.name,
            "checkpoint": spec.checkpoint,
            "checkpoint_identity": None,
            "restore_mode": "checkpoint-image",
            "warmup_definition": "simulator-warmup-reset",
            "roi_definition": "simulator-final-summary",
            "weight": spec.weight,
            "weight_kind": "simpoint_weight",
            "ordinal": spec.order,
        }
        for spec in slices
    ]
    member_by_name = {member["slice"]: member for member in members}
    comparison_key = canonical_hash(
        {
            "config": "DefaultConfig",
            "slice_set_id": slice_set_id,
            "roi_definition": "simulator-final-summary",
            "mainline_ref": DEFAULT_MAINLINE_REF,
        }
    )
    manifests = []
    for run in runs:
        directory = str(run["directory"])
        run_dir = report_root / directory
        score_path = run_dir / SCORE_NAME
        scores, _ = parse_score(score_path)
        run_rows = by_run.get(directory, [])
        results = []
        artifacts = [{"slice_id": None, "kind": "score", "uri": str(score_path)}]
        for row in run_rows:
            member = member_by_name[str(row["slice"])]
            instructions = int(row["instructions"])
            cycles = int(row["cycles"])
            source = str(row["source"])
            results.append(
                {
                    "slice_id": member["slice_id"],
                    "status": "valid",
                    "seed": None,
                    "window_id": "roi_summary",
                    "instructions": instructions,
                    "cycles": cycles,
                    "ipc_reported": float(row["ipc"]),
                    "ipc_computed": instructions / cycles,
                    "cpi": cycles / instructions,
                    "source_out_uri": source,
                    "source_err_uri": str(Path(source).with_name("simulator_err.txt")),
                    "error_code": None,
                }
            )
            artifacts.append(
                {"slice_id": member["slice_id"], "kind": "simulator_out", "uri": source}
            )
        aggregates = []
        for trend in trend_by_run.get(directory, []):
            weighted_ipc = trend["weighted_ipc"]
            weighted_cpi = 1.0 / float(weighted_ipc) if weighted_ipc else None
            for metric_id, value in (
                ("weighted_cpi", weighted_cpi),
                ("equivalent_ipc", weighted_ipc),
            ):
                aggregates.append(
                    {
                        "level": "workload",
                        "object_id": trend["workload"],
                        "metric_id": metric_id,
                        "formula_version": CPI_FORMULA_VERSION,
                        "value": value,
                        "numerator": weighted_cpi,
                        "denominator": 1.0,
                        "coverage_weight": float(trend["weight_coverage_pct"]) / 100.0,
                        "valid_member_count": int(trend["slice_count"]),
                        "total_member_count": int(trend["expected_slice_count"]),
                        "status": "valid" if trend["status"] == "complete" else "partial",
                        "source_uri": str(checkpoint_path),
                    }
                )
        snapshot_at = datetime.fromtimestamp(
            max(path.stat().st_mtime for path in (score_path, run_dir / "emu-gsim")),
            tz=timezone.utc,
        ).isoformat()
        run_id = canonical_hash({"source_uri": str(run_dir), "commit": run["commit"]})
        manifests.append(
            {
                "schema_version": SCHEMA_VERSION,
                "parser_version": PARSER_VERSION,
                "commit": {
                    "commit_sha": run["commit"],
                    "short_sha": run["short_commit"],
                    "parents": run.get("parents", []),
                    "commit_epoch": run["commit_epoch"],
                    "commit_time": run["commit_time"],
                    "subject": run["subject"],
                },
                "run": {
                    "run_id": run_id,
                    "commit_sha": run["commit"],
                    "branch_label": DEFAULT_MAINLINE_REF,
                    "config": run["config"],
                    "started_at": None,
                    "completed_at": snapshot_at,
                    "score_generated_at": datetime.fromtimestamp(
                        score_path.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "snapshot_at": snapshot_at,
                    "source_uri": str(run_dir),
                    "status": "published" if len(results) == len(slices) else "partial",
                    "parser_version": PARSER_VERSION,
                    "score_formula_version": SCORE_FORMULA_VERSION,
                    "comparison_key": comparison_key,
                    "experiment": {
                        "mainline_ref": DEFAULT_MAINLINE_REF,
                        "selection": "first-parent",
                        "slice_set_id": slice_set_id,
                    },
                    "snapshot_identity": canonical_hash(
                        {"score_mtime_ns": score_path.stat().st_mtime_ns, "rows": len(results)}
                    ),
                },
                "slice_set": {
                    "slice_set_id": slice_set_id,
                    "name": "SPEC06 gcc16 rva23 novec",
                    "version": "260820",
                    "source_uri": str(checkpoint_path),
                    "members": members,
                },
                "scores": scores,
                "slice_results": results,
                "counter_values": [],
                "counter_import": "skipped",
                "metric_definitions": [],
                "aggregate_results": aggregates,
                "artifacts": artifacts,
            }
        )

    temporary = database.with_suffix(database.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with Store(temporary) as store:
        store.initialize()
        audit = store.import_manifests(manifests, source_root=str(report_root))
    os.replace(temporary, database)
    return audit


def write_scores_csv(database: Path, output: Path) -> None:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT c.short_sha AS short_commit, c.commit_time, a.level,
                   a.object_id, a.metric_id, a.value, a.coverage_weight, a.status
              FROM aggregate_results a
              JOIN runs r ON r.run_id = a.run_id
              JOIN commits c ON c.commit_sha = r.commit_sha
             WHERE a.metric_id IN ('score', 'score_per_ghz')
             ORDER BY c.commit_epoch, a.level, a.object_id
            """
        ).fetchall()
    finally:
        connection.close()
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)


def main() -> int:
    args = parse_args()
    report_root = args.report_root.resolve()
    git_repo = args.git_repo.resolve()
    checkpoint_path = args.checkpoints.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    slices = load_slices(checkpoint_path)
    expected_slices = {spec.name for spec in slices}
    selected_names = tuple(args.runs or DEFAULT_RUNS)
    mainline_shas = first_parent_shas(git_repo, args.mainline_ref)
    candidate_audit = audit_candidates(
        report_root,
        expected_slices,
        mainline_shas,
        args.month,
        set(selected_names),
    )
    selected_audit = {row["directory"]: row for row in candidate_audit if row["selected"]}
    missing_selected = set(selected_names) - set(selected_audit)
    if missing_selected:
        raise SystemExit(f"selected run directories were not found: {sorted(missing_selected)}")
    invalid_selected = {
        name: selected_audit[name]["exclusion_reasons"]
        for name in selected_names
        if not selected_audit[name]["eligible"]
    }
    if invalid_selected:
        raise SystemExit(f"selected runs failed audit: {invalid_selected}")

    runs = load_runs(report_root, selected_names, (git_repo,))
    full_by_short = {sha[:9]: sha for sha in mainline_shas}
    for run in runs:
        sha = str(run["short_commit"])
        if full_by_short.get(sha) != run["commit"]:
            raise SystemExit(f"selected commit is not on {args.mainline_ref}: {sha}")
        run["parents"] = git_lines(git_repo, "show", "-s", "--format=%P", sha)[0].split()

    rows, trends, errors = write_dashboard_files(
        output_dir, checkpoint_path, report_root, slices, runs
    )
    if errors:
        raise SystemExit(f"selected runs contain {len(errors)} invalid slice observations")
    database = output_dir / "mainline-performance.sqlite"
    database_audit = build_database(
        database, report_root, checkpoint_path, slices, runs, rows, trends
    )
    write_scores_csv(database, output_dir / "scores.csv")
    anomaly_audit = export_dashboard_anomalies(
        database, output_dir / "performance-anomalies.json"
    )
    audit = {
        "schema_version": "mainline-selection-audit/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "month": f"2026-{args.month}",
        "git_repo": str(git_repo),
        "mainline_ref": args.mainline_ref,
        "mainline_tip": git_lines(git_repo, "rev-parse", args.mainline_ref)[0],
        "selection_policy": {
            "ancestry": "commit must be on the first-parent chain",
            "config": "DefaultConfig",
            "suite": "SPEC06 gcc16 rva23 novec",
            "completeness": f"all {len(slices)} expected slice directories and gcc16 1.0c score",
            "sampling": "five manually spaced complete observations across the available September window",
        },
        "selected_runs": list(selected_names),
        "candidate_count": len(candidate_audit),
        "eligible_count": sum(row["eligible"] for row in candidate_audit),
        "candidates": candidate_audit,
        "database": {"path": str(database), **database_audit},
        "anomalies": anomaly_audit,
    }
    write_json(output_dir / "selection-audit.json", audit)
    print(json.dumps({
        "output_dir": str(output_dir),
        "database": str(database),
        "selected_runs": list(selected_names),
        "observations": len(rows),
        "database_stats": database_audit["stats"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
