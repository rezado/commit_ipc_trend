#!/usr/bin/env python3
"""Query the imported trend demo and print JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.service import TrendService  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    trend = subparsers.add_parser("trend")
    trend.add_argument("--level", choices=("suite", "benchmark", "workload", "slice", "counter"), required=True)
    trend.add_argument("--object", action="append", dest="objects", required=True)
    trend.add_argument("--metric", required=True)
    trend.add_argument("--comparison-key")
    trend.add_argument("--include-non-published", action="store_true")

    compare = subparsers.add_parser("compare")
    compare.add_argument("--a", required=True, dest="run_a")
    compare.add_argument("--b", required=True, dest="run_b")
    compare.add_argument("--top-n", type=int)
    compare.add_argument("--object", default="mcf", dest="object_id")

    anomalies = subparsers.add_parser(
        "anomalies", help="detect workload/slice anomalies from existing observations"
    )
    anomalies.add_argument("--current", required=True)
    anomalies.add_argument(
        "--baseline",
        help="comparison baseline; defaults to tested parent or nearest compatible prior run",
    )
    anomalies.add_argument("--fixed-baseline")
    anomalies.add_argument("--git-repo", type=Path, help="select a tested first-parent ancestor")
    anomalies.add_argument("--workload", action="append", dest="workloads")
    anomalies.add_argument("--workload-threshold-pct", type=float, default=0.5)
    anomalies.add_argument("--slice-threshold-pct", type=float, default=0.5)
    anomalies.add_argument("--contribution-top-n", type=int, default=10)
    anomalies.add_argument("--counter-top-n", type=int, default=5)
    anomalies.add_argument("--counter-change-threshold-pct", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise SystemExit(f"database does not exist: {args.db}")
    with Store(args.db, read_only=True) as store:
        store.validate_schema()
        service = TrendService(store)
        if args.command == "trend":
            result = service.get_trend(
                args.level,
                args.objects,
                args.metric,
                comparison_key=args.comparison_key,
                include_non_published=args.include_non_published,
            )
        elif args.command == "compare":
            result = service.compare_points(
                args.run_a,
                args.run_b,
                object_id=args.object_id,
                top_n=args.top_n,
            )
        else:
            result = service.detect_anomalies(
                args.current,
                baseline=args.baseline,
                fixed_baseline=args.fixed_baseline,
                workloads=args.workloads,
                git_repo=args.git_repo,
                workload_threshold_pct=args.workload_threshold_pct,
                slice_threshold_pct=args.slice_threshold_pct,
                contribution_top_n=args.contribution_top_n,
                counter_top_n=args.counter_top_n,
                counter_change_threshold_pct=args.counter_change_threshold_pct,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
