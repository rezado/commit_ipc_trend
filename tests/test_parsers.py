from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from commit_ipc_trend.parsers import (
    load_registry,
    parse_perf_counters,
    parse_score,
    parse_slice_output,
)


class ParsersTest(unittest.TestCase):
    def test_golden_score_and_slice_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            score = root / "score.txt"
            score.write_text(
                "429.mcf 120.406 9120 25.248 1.000\n"
                "SPEC2006 nan nan 20.964 nan\n"
                "Checkpoint Version : /checkpoints/v1/checkpoint\n",
                encoding="utf-8",
            )
            output = root / "simulator_out.txt"
            output.write_text(
                "emu-gsim compiled at Sep 1 2026\n"
                "Using seed = 6164\n"
                "The image is /checkpoints/v1/checkpoint/mcf/6753/image.zstd\n"
                "DRAMSIM3 config: /configs/XiangShan.ini\n"
                "CPU_FREQ: 3000 DRAM_FREQ: 1600\n"
                "Core-0 instrCnt = 40,000,004, cycleCnt = 65,874,404, IPC = 0.607216\n",
                encoding="utf-8",
            )
            scores, info = parse_score(score)
            parsed = parse_slice_output(output)

        self.assertEqual(next(row for row in scores if row["object_id"] == "SPEC2006")["value"], 20.964)
        self.assertEqual(next(row for row in scores if row["object_id"] == "429.mcf")["value"], 25.248)
        self.assertEqual(info["Checkpoint Version"], "/checkpoints/v1/checkpoint")
        self.assertEqual(parsed["instructions"], 40_000_004)
        self.assertEqual(parsed["cycles"], 65_874_404)
        self.assertEqual(parsed["ipc_reported"], 0.607216)
        self.assertAlmostEqual(parsed["cpi"], 65_874_404 / 40_000_004)
        self.assertEqual(parsed["status"], "valid")

    def test_perf_parser_uses_final_dump_and_preserves_zero(self):
        registry = load_registry()
        paths = {row["metric_id"]: row["source_path"] for row in registry["metrics"]}
        with tempfile.TemporaryDirectory() as temporary:
            error = Path(temporary) / "simulator_err.txt"
            error.write_text(
                f"[PERF ][time=10] {paths['ptw_fsm_requests']}: ptw_fsm_req_count, 99\n"
                f"[PERF ][time=10] {paths['ptw_mem_wait_cycles']}: ptw_mem_wait_cycle, 40\n"
                f"[PERF ][time=20] {paths['ptw_fsm_requests']}: ptw_fsm_req_count, 0\n"
                f"[PERF ][time=20] {paths['ptw_mem_wait_cycles']}: ptw_mem_wait_cycle, 42\n",
                encoding="utf-8",
            )
            values = {row["metric_id"]: row for row in parse_perf_counters(error, registry)}

        self.assertEqual(values["ptw_fsm_requests"]["availability"], "available")
        self.assertEqual(values["ptw_fsm_requests"]["value"], 0)
        self.assertEqual(values["ptw_fsm_requests"]["dump_time"], 20)
        self.assertEqual(values["l2_demand_misses"]["availability"], "missing")
        self.assertIsNone(values["l2_demand_misses"]["value"])

    def test_perf_parser_rejects_truncated_final_dump(self):
        registry = load_registry()
        paths = {row["metric_id"]: row["source_path"] for row in registry["metrics"]}
        with tempfile.TemporaryDirectory() as temporary:
            error = Path(temporary) / "simulator_err.txt"
            error.write_text(
                f"[PERF ][time=10] {paths['ptw_fsm_requests']}: ptw_fsm_req_count, 99\n"
                f"[PERF ][time=10] {paths['ptw_mem_wait_cycles']}: ptw_mem_wait_cycle, 40\n"
                f"[PERF ][time=20] {paths['ptw_fsm_requests']}: ptw_fsm_req_count, 0\n",
                encoding="utf-8",
            )
            values = parse_perf_counters(error, registry)

        self.assertTrue(values)
        self.assertTrue(all(row["availability"] == "parse_error" for row in values))
        self.assertTrue(all(row["value"] is None for row in values))


if __name__ == "__main__":
    unittest.main()
