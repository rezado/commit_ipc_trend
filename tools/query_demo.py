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
        else:
            result = service.compare_points(
                args.run_a,
                args.run_b,
                object_id=args.object_id,
                top_n=args.top_n,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
