#!/usr/bin/env python3
"""Read-only performance investigation dashboard for imported perf runs."""

from __future__ import annotations

import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import subprocess
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.service import TrendService  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402

WEB_ROOT = ROOT / "perf_platform_web"
COUNTER_SEMANTICS = json.loads(
    (ROOT / "commit_ipc_trend" / "counter_semantics.json").read_text()
)
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
TOPDOWN_EVENTS = (
    "total_cycles", "commitInstr", "RobStall", "ControlRedirectStall",
    "TAGEMissBubble", "LoadMemStall", "ICacheMissBubble",
)


def overall_result(store: Store, run_a: str, run_b: str) -> dict:
    connection = store.connection
    runs = {
        row["run_id"]: dict(row)
        for row in connection.execute(
            "SELECT run_id, status, comparison_key, slice_set_id FROM runs WHERE run_id IN (?, ?)",
            (run_a, run_b),
        )
    }
    if run_a not in runs or run_b not in runs:
        raise ValueError("run not found")
    a, b = runs[run_a], runs[run_b]
    reasons = []
    if a["status"] != "published" or b["status"] != "published":
        reasons.append("run_not_published")
    if a["comparison_key"] != b["comparison_key"]:
        reasons.append("comparison_key_mismatch")
    if a["slice_set_id"] != b["slice_set_id"]:
        reasons.append("slice_set_mismatch")

    rows = connection.execute(
        """SELECT a.object_id, a.value AS value_a, b.value AS value_b,
                  a.formula_version
             FROM aggregate_results a
             JOIN aggregate_results b
               ON b.level = a.level AND b.object_id = a.object_id
              AND b.metric_id = a.metric_id AND b.formula_version = a.formula_version
            WHERE a.run_id = ? AND b.run_id = ? AND a.level = 'suite'
              AND a.metric_id = 'score_per_ghz'
              AND a.status = 'valid' AND b.status = 'valid'
            ORDER BY a.object_id""",
        (run_a, run_b),
    )
    scores = []
    for row in rows:
        value_a, value_b = row["value_a"], row["value_b"]
        scores.append({
            "name": row["object_id"],
            "value_a": value_a,
            "value_b": value_b,
            "change_percent": (
                (value_b / value_a - 1) * 100
                if not reasons and value_a not in (None, 0) and value_b is not None
                else None
            ),
            "formula_version": row["formula_version"],
        })
    return {"status": "comparable" if not reasons else "incomparable",
            "reasons": reasons, "scores": scores}


def history_runs(store: Store, target_run_id: str, git_repo: Path | None) -> dict:
    connection = store.connection
    raw = connection.execute(
        """SELECT r.run_id, r.commit_sha, r.status, r.comparison_key, r.slice_set_id,
                  c.short_sha, c.commit_time, c.commit_epoch
             FROM runs r JOIN commits c USING (commit_sha)
            WHERE r.run_id = ?""",
        (target_run_id,),
    ).fetchone()
    if raw is None:
        raise ValueError("run not found")
    target = dict(raw)
    candidates = connection.execute(
        """SELECT r.run_id, r.commit_sha, c.short_sha, c.commit_time, c.commit_epoch
             FROM runs r JOIN commits c USING (commit_sha)
            WHERE r.comparison_key = ? AND r.slice_set_id = ?
              AND r.status = 'published' AND c.commit_epoch <= ?
            ORDER BY c.commit_epoch DESC, r.snapshot_at DESC""",
        (target["comparison_key"], target["slice_set_id"], target["commit_epoch"]),
    )
    by_sha = {}
    for row in candidates:
        by_sha.setdefault(row["commit_sha"], dict(row))
    if target["status"] == "published":
        by_sha[target["commit_sha"]] = {
            key: target[key] for key in (
                "run_id", "commit_sha", "short_sha", "commit_time", "commit_epoch"
            )
        }
    if git_repo is not None:
        result = subprocess.run(
            ["git", "-C", str(git_repo), "rev-list", "--first-parent",
             target["commit_sha"]],
            check=True, capture_output=True, text=True,
        )
        ordered_shas = reversed(result.stdout.splitlines())
        basis = "first_parent_tested_commits"
    else:
        ordered_shas = reversed(list(by_sha))
        basis = "compatible_observations"
    return {
        "basis": basis,
        "comparison_key": target["comparison_key"],
        "runs": [by_sha[sha] for sha in ordered_shas if sha in by_sha],
    }


def slice_evidence(store: Store, service: TrendService, run_a: str, run_b: str,
                   workload: str, slice_id: str) -> dict:
    comparison = service.compare_points(run_a, run_b, object_id=workload)
    if comparison["status"] != "comparable":
        return {"status": "incomparable", "reasons": comparison["reasons"]}
    selected = next((row for row in comparison["slices"] if row["slice_id"] == slice_id), None)
    if selected is None:
        raise ValueError("slice is not in the compared workload")

    connection = store.connection
    roi = {
        row["run_id"]: dict(row)
        for row in connection.execute(
            """SELECT run_id, instructions, cycles, ipc_computed, seed,
                      source_out_uri, source_err_uri
                 FROM slice_results WHERE slice_id = ? AND run_id IN (?, ?)""",
            (slice_id, run_a, run_b),
        )
    }
    rows = connection.execute(
        """SELECT m.metric_id, m.semantic_version, m.display_name, m.category,
                  m.unit, m.direction, m.source_match_json,
                  a.value AS value_a, a.availability AS availability_a,
                  a.window_id AS window_a, a.source_uri AS source_a,
                  b.value AS value_b, b.availability AS availability_b,
                  b.window_id AS window_b, b.source_uri AS source_b
             FROM metric_definitions m
             LEFT JOIN counter_values a
               ON a.metric_id = m.metric_id AND a.semantic_version = m.semantic_version
              AND a.run_id = ? AND a.slice_id = ?
             LEFT JOIN counter_values b
               ON b.metric_id = m.metric_id AND b.semantic_version = m.semantic_version
              AND b.run_id = ? AND b.slice_id = ?
            ORDER BY m.category, m.display_name""",
        (run_a, slice_id, run_b, slice_id),
    )
    metrics = []
    for raw in rows:
        row = dict(raw)
        source = json.loads(row.pop("source_match_json"))
        semantics = COUNTER_SEMANTICS["metrics"].get(row["metric_id"])
        full_name = source["path"] + ": " + source["name"]
        row["semantics"] = (
            semantics if semantics and semantics["full_name"] == full_name else None
        )
        paired = (row["availability_a"] == "available"
                  and row["availability_b"] == "available"
                  and row["value_a"] is not None and row["value_b"] is not None
                  and row["window_a"] == row["window_b"])
        row["paired"] = paired
        row["change_percent"] = (
            (row["value_b"] - row["value_a"]) / abs(row["value_a"]) * 100
            if paired and row["value_a"] else None
        )
        metrics.append(row)
    return {"status": "ok", "slice": selected, "roi_a": roi.get(run_a),
            "roi_b": roi.get(run_b), "metrics": metrics,
            "catalog_revision": COUNTER_SEMANTICS["catalog_revision"]}


def analysis_record(row: dict) -> dict | None:
    try:
        return json.loads(Path(row["result_uri"]).read_text())
    except (OSError, ValueError):
        return None


def analysis_outputs(row: dict, record: dict) -> list[str]:
    root = Path(row["result_uri"]).parent.resolve()
    outputs = []
    for source in record.get("outputs", []):
        path = Path(source).resolve()
        if (path.is_relative_to(root) and path.is_file()
                and path.suffix.lower() in {".png", ".csv"}):
            outputs.append(path.name)
    return sorted(set(outputs))


def topdown_events(result_dir: Path, workload: str) -> list[dict]:
    values = []
    for side in ("base", "ref"):
        path = result_dir / ("results-weighted_" + side + ".csv")
        if not path.is_file():
            return []
        with path.open(newline="") as stream:
            row = next((row for row in csv.DictReader(stream)
                        if row.get("") == workload), None)
        if row is None:
            return []
        values.append(row)
    result = []
    for name in TOPDOWN_EVENTS:
        try:
            value_a, value_b = float(values[0][name]), float(values[1][name])
        except (KeyError, ValueError):
            continue
        if math.isfinite(value_a) and math.isfinite(value_b):
            result.append({
                "name": name, "value_a": value_a, "value_b": value_b,
                "semantics": COUNTER_SEMANTICS["topdown_events"].get(name),
            })
    return result


def analyses_for(store: Store, run_a: str, run_b: str, workload: str) -> list[dict]:
    rows = store.connection.execute(
        """SELECT * FROM analysis_runs WHERE run_a = ? AND run_b = ?
              AND kind IN ('topdown', 'rolling') ORDER BY created_at DESC""",
        (run_a, run_b),
    )
    result = []
    for raw in rows:
        row = dict(raw)
        record = analysis_record(row)
        if not record or record.get("workload") != workload:
            continue
        outputs = analysis_outputs(row, record)
        item = {
            "analysis_id": row["analysis_id"], "kind": row["kind"],
            "status": row["status"], "created_at": row["created_at"],
            "result_uri": row["result_uri"],
            "images": [name for name in outputs if name.endswith(".png")
                       and name != "result_intel_topdown.png"],
            "csvs": [name for name in outputs if name.endswith(".csv")],
        }
        if row["kind"] == "topdown" and row["status"] == "completed":
            item["catalog_revision"] = COUNTER_SEMANTICS["catalog_revision"]
            item["events"] = topdown_events(
                Path(row["result_uri"]).parent / "results", workload
            )
        result.append(item)
    return result


def analysis_file(store: Store, analysis_id: str, name: str) -> tuple[bytes, str]:
    if Path(name).name != name:
        raise ValueError("invalid analysis filename")
    raw = store.connection.execute(
        "SELECT * FROM analysis_runs WHERE analysis_id = ?", (analysis_id,)
    ).fetchone()
    if raw is None:
        raise ValueError("analysis not found")
    row = dict(raw)
    record = analysis_record(row)
    if record is None or name not in analysis_outputs(row, record):
        raise ValueError("analysis output not found")
    root = Path(row["result_uri"]).parent.resolve()
    candidates = [
        Path(source).resolve() for source in record["outputs"]
        if Path(source).name == name and Path(source).resolve().is_relative_to(root)
    ]
    if not candidates:
        raise ValueError("analysis output not found")
    path = candidates[0]
    return path.read_bytes(), ("image/png" if path.suffix == ".png"
                               else "text/csv; charset=utf-8")


def handler_for(db: Path, git_repo: Path | None = None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            q = parse_qs(parsed.query)
            try:
                if parsed.path in STATIC:
                    name, content_type = STATIC[parsed.path]
                    body = (WEB_ROOT / name).read_bytes()
                else:
                    with Store(db, read_only=True) as store:
                        service = TrendService(store)
                        if parsed.path == "/api/runs":
                            value = [dict(row) for row in store.connection.execute(
                                """SELECT r.run_id, r.commit_sha, c.short_sha, r.branch_label,
                                          r.config, r.status, r.comparison_key, r.source_uri,
                                          r.snapshot_at
                                     FROM runs r JOIN commits c USING (commit_sha)
                                    ORDER BY c.commit_epoch DESC, r.snapshot_at DESC LIMIT 500"""
                            )]
                        elif parsed.path == "/api/workloads":
                            value = [row[0] for row in store.connection.execute(
                                """SELECT DISTINCT s.workload FROM slices s
                                     JOIN slice_results sr USING (slice_id)
                                    WHERE sr.run_id = ? ORDER BY s.workload""",
                                (q["run"][0],))]
                        elif parsed.path == "/api/baseline":
                            if git_repo is None:
                                raise ValueError("baseline selection needs --git-repo")
                            value = service.select_baseline(q["run"][0], git_repo)
                        elif parsed.path == "/api/overall":
                            value = overall_result(store, q["a"][0], q["b"][0])
                        elif parsed.path == "/api/history":
                            value = history_runs(store, q["run"][0], git_repo)
                        elif parsed.path == "/api/anomalies":
                            report = service.detect_anomalies(
                                q["current"][0], baseline=q["baseline"][0],
                                counter_top_n=0,
                            )
                            pair = report["comparisons"][0]
                            fields = (
                                "workload", "comparison_mode", "warnings", "is_anomaly",
                                "cpi_degradation_percent", "weighted_cpi_a",
                                "weighted_cpi_b", "weighted_cpi_delta",
                                "equivalent_ipc_a", "equivalent_ipc_b",
                                "coverage_weight", "slice_count",
                            )
                            value = {
                                "status": pair["status"],
                                "summary": pair["summary"],
                                "workloads": [{key: row[key] for key in fields}
                                              for row in pair["workloads"]],
                            }
                        elif parsed.path == "/api/compare":
                            value = service.compare_points(
                                q["a"][0], q["b"][0], object_id=q["workload"][0]
                            )
                        elif parsed.path == "/api/slice":
                            value = slice_evidence(
                                store, service, q["a"][0], q["b"][0],
                                q["workload"][0], q["slice"][0],
                            )
                        elif parsed.path == "/api/analyses":
                            value = analyses_for(
                                store, q["a"][0], q["b"][0], q["workload"][0]
                            )
                        elif parsed.path == "/api/analysis-file":
                            body, content_type = analysis_file(
                                store, q["analysis"][0], q["name"][0]
                            )
                            value = None
                        elif parsed.path == "/api/trend":
                            value = service.get_trend(
                                q["level"][0], q["object"], q["metric"][0],
                                comparison_key=q.get("comparison_key", [None])[0],
                            )
                        elif parsed.path == "/api/artifacts":
                            value = service.get_artifacts(
                                q["run"][0], q.get("slice", [None])[0]
                            )
                        else:
                            self.send_error(404)
                            return
                    if parsed.path != "/api/analysis-file":
                        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
                        content_type = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (KeyError, ValueError) as exc:
                self.send_error(400, str(exc))

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--git-repo", type=Path,
                        help="XiangShan checkout for first-parent baseline selection")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with Store(args.db, read_only=True) as store:
        store.validate_schema()
    ThreadingHTTPServer(
        (args.host, args.port), handler_for(args.db, args.git_repo)
    ).serve_forever()


if __name__ == "__main__":
    main()
