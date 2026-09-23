#!/usr/bin/env python3
"""Run XiangShan top-down or rolling tools on a comparable A/B run pair."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.schema import canonical_hash  # noqa: E402
from commit_ipc_trend.service import TrendService  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--a", required=True, help="base run ID")
    p.add_argument("--b", required=True, help="target run ID")
    p.add_argument("--workload", required=True)
    p.add_argument("--xiangshan", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    sub = p.add_subparsers(dest="kind", required=True)
    td = sub.add_parser("topdown")
    td.add_argument("--checkpoint-json", type=Path, required=True)
    td.add_argument("--base-issue", type=float, required=True)
    td.add_argument("--target-issue", type=float, required=True)
    roll = sub.add_parser("rolling")
    roll.add_argument("--base-db", type=Path, required=True)
    roll.add_argument("--target-db", type=Path, required=True)
    roll.add_argument("--perf-name", required=True)
    roll.add_argument("--hart", type=int, default=0)
    args = p.parse_args()
    with Store(args.db) as store:
        store.initialize()
        compared = TrendService(store).compare_points(args.a, args.b, object_id=args.workload)
        if compared["status"] != "comparable":
            p.error(f"comparable A/B runs required: {compared.get('reasons')}")
        base, target = compared["run_a"], compared["run_b"]
        if args.kind == "topdown":
            script = args.xiangshan / "scripts/top-down/top_down.py"
            config = args.xiangshan / "scripts/top-down/configs.py"
            for required in (args.checkpoint_json, script, config):
                if not required.is_file():
                    p.error(f"file is missing: {required}")
            command = [sys.executable, str(script), "-b", base["source_uri"],
                       "-r", target["source_uri"], "-j", str(args.checkpoint_json),
                       "--base-issue", str(args.base_issue), "--ref-issue", str(args.target_issue),
                       "--base-label", "Base", "--ref-label", "Target"]
            inputs = {"checkpoint_json": str(args.checkpoint_json), "checkpoint_json_sha256": digest(args.checkpoint_json),
                      "base_issue": args.base_issue, "target_issue": args.target_issue,
                      "config_sha256": digest(config)}
        else:
            script = args.xiangshan / "scripts/rolling.py"
            if not script.is_file():
                p.error(f"file is missing: {script}")
            for required in (args.base_db, args.target_db):
                if not required.is_file():
                    p.error(f"rolling DB is missing: {required}")
            inputs = {"base_db": str(args.base_db), "target_db": str(args.target_db),
                      "perf_name": args.perf_name, "hart": args.hart}
        analyzer_hash = digest(script)
        analysis_id = canonical_hash({"kind": args.kind, "a": args.a, "b": args.b,
                                      "workload": args.workload, "inputs": inputs,
                                      "analyzer_sha256": analyzer_hash})
        output = args.output_dir.resolve() / analysis_id
        output.mkdir(parents=True, exist_ok=True)
        if args.kind == "rolling":
            command = [sys.executable, str(script), "diff", str(args.base_db), str(args.target_db),
                       "--perf-name", args.perf_name, "--hart", str(args.hart),
                       "--output", str(output / "rolling.png")]
        completed = subprocess.run(command, cwd=output, capture_output=True, text=True, timeout=3600)
        (output / "stdout.txt").write_text(completed.stdout)
        (output / "stderr.txt").write_text(completed.stderr)
        record = {"analysis_id": analysis_id, "run_a": args.a, "run_b": args.b,
                  "workload": args.workload, "kind": args.kind, "status": "completed" if completed.returncode == 0 else "failed",
                  "inputs": inputs, "command": command, "analyzer_sha256": analyzer_hash,
                  "returncode": completed.returncode,
                  "outputs": [str(path) for path in output.rglob("*") if path.is_file()]}
        result = output / "analysis.json"
        result.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        with store.connection:
            store.connection.execute(
                """INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(analysis_id) DO UPDATE SET status=excluded.status,
                     result_uri=excluded.result_uri""",
                (analysis_id, args.a, args.b, args.kind, record["status"], json.dumps(inputs),
                 str(result), analyzer_hash, datetime.now(timezone.utc).isoformat()),
            )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0 if completed.returncode == 0 else 1


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
