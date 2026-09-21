from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.collect_spec06_slice_ipc_trends import (
    SliceSpec,
    collect_observations,
    load_slices,
    parse_ipc,
    summarize_slices,
    workload_trends,
)


class CollectSpec06SliceIpcTrendsTest(unittest.TestCase):
    def test_load_slices_parses_workload_with_punctuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkpoints.txt"
            path.write_text(
                "h264ref_foreman.baseline_123_0.75/\nmcf_456_0.25/\n",
                encoding="utf-8",
            )
            slices = load_slices(path)
            self.assertEqual(slices[0].workload, "h264ref_foreman.baseline")
            self.assertEqual(slices[0].checkpoint, 123)
            self.assertEqual(slices[0].weight, 0.75)

    def test_parse_ipc_uses_last_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "simulator_out.txt"
            path.write_text(
                "instrCnt = 10, cycleCnt = 20, IPC = 0.5\n"
                "instrCnt = 40,000,000, cycleCnt = 10,000,000, IPC = 4.0\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_ipc(path), (40_000_000, 10_000_000, 4.0))

    def test_collect_summary_and_weighted_trend(self):
        slices = [
            SliceSpec(1, "work_10_0.25", "work", 10, 0.25),
            SliceSpec(2, "work_20_0.75", "work", 20, 0.75),
        ]
        runs = [
            {
                "commit_order": 1,
                "commit": "a" * 40,
                "short_commit": "a" * 9,
                "commit_time": "2026-09-01T00:00:00+08:00",
                "subject": "first",
                "directory": "run-a",
                "config": "DefaultConfig",
            },
            {
                "commit_order": 2,
                "commit": "b" * 40,
                "short_commit": "b" * 9,
                "commit_time": "2026-09-02T00:00:00+08:00",
                "subject": "second",
                "directory": "run-b",
                "config": "DefaultConfig",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {
                ("run-a", "work_10_0.25"): 1.0,
                ("run-a", "work_20_0.75"): 2.0,
                ("run-b", "work_10_0.25"): 2.0,
                ("run-b", "work_20_0.75"): 4.0,
            }
            for (run, name), ipc in values.items():
                target = root / run / name
                target.mkdir(parents=True)
                (target / "simulator_out.txt").write_text(
                    f"instrCnt = 40,000,000, cycleCnt = 10,000,000, IPC = {ipc}\n",
                    encoding="utf-8",
                )
            rows, errors = collect_observations(root, slices, runs)
            summaries = summarize_slices(slices, rows, 2)
            trends = workload_trends(slices, rows, runs)

        self.assertFalse(errors)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(float(summaries[0]["first_to_last_pct"]), 100.0)
        self.assertAlmostEqual(float(trends[0]["weighted_ipc"]), 1.6)
        self.assertEqual(trends[0]["status"], "complete")
        self.assertEqual(trends[0]["comparison_status"], "complete")
        self.assertAlmostEqual(float(trends[0]["weight_coverage_pct"]), 100.0)
        self.assertAlmostEqual(float(trends[1]["weighted_ipc"]), 3.2)
        self.assertAlmostEqual(float(trends[1]["change_from_first_pct"]), 100.0)


if __name__ == "__main__":
    unittest.main()
