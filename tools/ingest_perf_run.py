#!/usr/bin/env python3
"""Validate or import a completed perf-trigger receipt into the trend database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.ingest import build_manifest  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True, help="completed perf-trigger-receipt/v1 JSON")
    parser.add_argument("--git-repo", type=Path, required=True, help="local XiangShan checkout with full history")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, help="save the validated structured manifest")
    parser.add_argument("--counters", action="store_true", help="parse registered counters from large PERF logs")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest(args.receipt, args.git_repo, include_counters=args.counters)
    if args.manifest_out:
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    if args.validate_only:
        result = {"status": "validated", "run_id": manifest["run"]["run_id"],
                  "run_status": manifest["run"]["status"], "slices": len(manifest["slice_results"])}
    else:
        with Store(args.db) as store:
            store.initialize()
            audit = store.import_manifests([manifest], source_root=manifest["run"]["source_uri"])
        result = {**audit, "run_status": manifest["run"]["status"]}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
