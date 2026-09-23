#!/usr/bin/env python3
"""Write a completion receipt after perf-trigger finished its report step."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("report-dir", "checkpoint-list", "commit-sha", "config", "emulator",
                 "benchmark-type", "checkpoint-identity", "warmup", "max-instr", "max-cycles",
                 "dram-config", "cpu-frequency-mhz", "dram-frequency-mhz",
                 "build-options", "score-formula-version"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--score-file")
    parser.add_argument("--actions-run-id")
    parser.add_argument("--branch-label")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report_dir = Path(args.report_dir).resolve(strict=True)
    checkpoint_list = Path(args.checkpoint_list).resolve(strict=True)
    if args.score_file and not Path(args.score_file).is_file():
        parser.error(f"score file is missing: {args.score_file}")
    payload = {
        "schema_version": "perf-trigger-receipt/v1", "status": "completed",
        "report_dir": str(report_dir), "checkpoint_list": str(checkpoint_list),
        "commit_sha": args.commit_sha, "score_file": args.score_file,
        "actions_run_id": args.actions_run_id, "branch_label": args.branch_label,
        "experiment": {
            "config": args.config, "emulator": args.emulator,
            "benchmark_type": args.benchmark_type, "checkpoint_identity": args.checkpoint_identity,
            "warmup": args.warmup, "max_instr": args.max_instr,
            "max_cycles": args.max_cycles, "dram_config": args.dram_config,
            "cpu_frequency_mhz": args.cpu_frequency_mhz,
            "dram_frequency_mhz": args.dram_frequency_mhz,
            "build_options": args.build_options,
            "score_formula_version": args.score_formula_version,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_name(args.output.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
