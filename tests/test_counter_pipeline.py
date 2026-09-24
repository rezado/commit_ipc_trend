from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.discover_perf_counters import final_complete_dump


class CounterPipelineTest(unittest.TestCase):
    def test_inventory_uses_path_name_and_complete_final_dump(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "simulator_err.txt"
            log.write_text(
                "[PERF ][time=1] path.a: same, 1\n"
                "[PERF ][time=1] path.b: same, 2\n"
                "[PERF ][time=2] path.a: same, 3\n"
                "[PERF ][time=2] path.b: same, 4\n",
                encoding="utf-8",
            )
            dump_time, keys, status = final_complete_dump(log)
        self.assertEqual(dump_time, 2)
        self.assertEqual(status, "complete")
        self.assertEqual(keys[("path.a", "same")], 1)
        self.assertEqual(keys[("path.b", "same")], 1)

    def test_generated_inventory_covers_registered_metrics(self):
        root = Path(__file__).resolve().parents[1]
        inventory_path = root / "outputs/mainline-september/perf-counter-inventory.csv"
        if not inventory_path.is_file():
            self.skipTest("generated inventory is not present")
        with inventory_path.open(encoding="utf-8") as stream:
            inventory = {
                (row["source_path"], row["source_name"]): row
                for row in csv.DictReader(stream)
            }
        registry = json.loads(
            (root / "commit_ipc_trend/metric_registry.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(registry["metrics"]), 20)
        for metric in registry["metrics"]:
            row = inventory[(metric["source_path"], metric["source_name"])]
            self.assertEqual(row["coverage_pct"], "100.000")
            self.assertEqual(row["unique_per_dump"], "True")

    def test_dashboard_export_matches_database(self):
        root = Path(__file__).resolve().parents[1]
        database = root / "outputs/mainline-september/mainline-performance.sqlite"
        exported = root / "outputs/mainline-september/perf-counters.json"
        if not database.is_file() or not exported.is_file():
            self.skipTest("generated database/export is not present")
        payload = json.loads(exported.read_text(encoding="utf-8"))
        connection = sqlite3.connect(database)
        try:
            metric_count = connection.execute("SELECT count(*) FROM metric_definitions").fetchone()[0]
            value_count = connection.execute("SELECT count(*) FROM counter_values").fetchone()[0]
            slice_count = connection.execute("SELECT count(*) FROM slices").fetchone()[0]
            run_slice_count = connection.execute(
                "SELECT count(*) FROM slice_results"
            ).fetchone()[0]
            unavailable = connection.execute(
                "SELECT count(*) FROM counter_values WHERE availability != 'available'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(payload["metric_count"], metric_count)
        self.assertEqual(payload["observation_count"], value_count)
        self.assertEqual(len(payload["values"]), value_count)
        self.assertEqual(len(payload["slices"]), slice_count)
        self.assertEqual(value_count, run_slice_count * metric_count)
        self.assertEqual(
            {(row["slice"], row["short_commit"]) for row in payload["values"]},
            {(row["slice"], commit["short_commit"]) for row in payload["slices"] for commit in payload["commits"]},
        )
        self.assertEqual(unavailable, 0)

    def test_dashboard_javascript_has_valid_syntax(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--check", str(root / "xiangshan-performance-dashboard/app.js")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dashboard_anomaly_export_matches_latest_database_run(self):
        root = Path(__file__).resolve().parents[1]
        database = root / "outputs/mainline-september/mainline-performance.sqlite"
        exported = root / "outputs/mainline-september/performance-anomalies.json"
        if not database.is_file() or not exported.is_file():
            self.skipTest("generated database/anomaly export is not present")
        payload = json.loads(exported.read_text(encoding="utf-8"))
        connection = sqlite3.connect(database)
        try:
            latest = connection.execute(
                """
                SELECT c.short_sha
                  FROM runs r JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE r.status = 'published'
                 ORDER BY c.commit_epoch DESC, r.snapshot_at DESC
                 LIMIT 1
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(payload["schema_version"], "performance-anomalies/v1")
        self.assertEqual(payload["current"]["short_sha"], latest)
        self.assertGreaterEqual(payload["comparison_count"], 1)
        for comparison in payload["comparisons"]:
            self.assertEqual(comparison["summary"]["workload_count"], 55)
            self.assertEqual(comparison["summary"]["incomparable_workload_count"], 0)

    def test_exported_counters_share_a_normalizable_window(self):
        """The per-instruction view normalizes with rob_committed_instructions.

        That is only sound when every (slice, commit) has a base count and the
        final PERF dump window is a fixed size across the whole panel.
        """
        root = Path(__file__).resolve().parents[1]
        exported = root / "outputs/mainline-september/perf-counters.json"
        if not exported.is_file():
            self.skipTest("generated dashboard export is not present")
        payload = json.loads(exported.read_text(encoding="utf-8"))

        base_metric = "rob_committed_instructions"
        self.assertIn(base_metric, {row["metric_id"] for row in payload["values"]})
        bases = {}
        combos = set()
        for row in payload["values"]:
            combos.add((row["slice"], row["short_commit"]))
            if row["metric_id"] == base_metric:
                bases[(row["slice"], row["short_commit"])] = row["value"]
        self.assertEqual(set(bases), combos)
        for value in bases.values():
            self.assertAlmostEqual(value, 20_000_000, delta=2000)


if __name__ == "__main__":
    unittest.main()
