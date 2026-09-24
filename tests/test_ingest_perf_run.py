"""End-to-end contract: two CI receipts become comparable runs, missing slices stay partial."""

from __future__ import annotations

import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen

from commit_ipc_trend.ingest import build_manifest, read_checkpoint_list
from commit_ipc_trend.service import TrendService
from commit_ipc_trend.store import Store
from tools.serve_perf_platform import handler_for
from tools.analyze_perf_pair import prepare_topdown_inputs


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class PerfRunIngestTest(unittest.TestCase):
    def test_partial_checkpoint_profile_keeps_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "partial.json"
            path.write_text(json.dumps({"mcf": {"points": {"1": "0.21", "2": "0.09"}}}))
            entries = read_checkpoint_list(path)
            self.assertEqual(len(entries), 2)
            self.assertAlmostEqual(sum(entry["weight"] for entry in entries), 0.3)

    def test_two_workloads_and_missing_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "xs"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "test")
            ckpt = root / "checkpoints.txt"
            ckpt.write_text("mcf_1_0.7/\nmcf_2_0.3/\nmilc_3_1.0/\n")
            receipts = []
            for index in (1, 2):
                (repo / "version").write_text(str(index))
                git(repo, "add", "version")
                git(repo, "commit", "-qm", f"v{index}")
                sha = git(repo, "rev-parse", "HEAD")
                report = root / f"run-{index}"
                report.mkdir()
                for name, cycles in (("mcf_1_0.7", 100 + 10 * index),
                                     ("mcf_2_0.3", 100), ("milc_3_1.0", 200)):
                    slice_dir = report / name
                    slice_dir.mkdir()
                    (slice_dir / "simulator_out.txt").write_text(
                        f"instrCnt = 100, cycleCnt = {cycles}, IPC = {100/cycles:.6f}\n"
                    )
                receipt = root / f"receipt-{index}.json"
                receipt.write_text(json.dumps({
                    "schema_version": "perf-trigger-receipt/v1", "status": "completed",
                    "report_dir": str(report), "checkpoint_list": str(ckpt),
                    "commit_sha": sha, "actions_run_id": str(index),
                    "experiment": {"config": "DefaultConfig", "emulator": "gsim",
                                   "benchmark_type": "custom", "checkpoint_identity": "ckpt-v1",
                                   "warmup": 20, "max_instr": 100, "max_cycles": "unlimited",
                                   "dram_config": "same", "cpu_frequency_mhz": "1000",
                                   "dram_frequency_mhz": "800", "build_options": "test",
                                   "score_formula_version": "none"},
                }))
                receipts.append(receipt)
            db = root / "trend.sqlite"
            with Store(db) as store:
                store.initialize()
                for receipt in receipts:
                    manifest = build_manifest(receipt, repo)
                    store.import_manifests([manifest])
                store.import_manifests([build_manifest(receipts[0], repo)])
                self.assertEqual(store.counts()["runs"], 2)
                result = TrendService(store).compare_points(
                    build_manifest(receipts[0], repo)["run"]["run_id"],
                    build_manifest(receipts[1], repo)["run"]["run_id"],
                    object_id="mcf",
                )
                self.assertEqual(result["status"], "comparable")
                self.assertEqual(result["total_slice_count"], 2)
                self.assertAlmostEqual(result["weighted_cpi_delta"], 0.07)
                baseline = TrendService(store).select_baseline(result["run_b"]["run_id"], repo)
                self.assertEqual(baseline["status"], "found")
                self.assertEqual(baseline["baseline"]["run_id"], result["run_a"]["run_id"])
                anomalies = TrendService(store).detect_anomalies(result["run_b"]["run_id"], git_repo=repo)
                self.assertEqual(anomalies["comparisons"][0]["baseline_basis"], "first_parent_tested_ancestor")
                profile = root / "topdown.json"
                profile.write_text(json.dumps({"mcf": {"points": {"1": "0.7", "2": "0.3"}},
                                               "milc": {"points": {"3": "1.0"}}}))
                for run_dir in (root / "run-1", root / "run-2"):
                    for name in ("mcf_1_0.7", "mcf_2_0.3"):
                        (run_dir / name / "simulator_err.txt").write_text("test counter log")
                analysis_dir = root / "analysis"
                analysis_dir.mkdir()
                base_dir, target_dir, selected = prepare_topdown_inputs(result, profile, analysis_dir)
                self.assertEqual(set(json.loads(selected.read_text())), {"mcf"})
                for directory in (base_dir, target_dir):
                    self.assertEqual({p.name for p in directory.iterdir()}, {"mcf_1_0.7", "mcf_2_0.3"})
                    self.assertTrue((directory / "mcf_1_0.7/simulator_err.txt").is_file())

            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(db, repo))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}"
                with urlopen(url + "/api/runs") as response:
                    self.assertEqual(len(json.load(response)), 2)
                query = urlencode({"a": result["run_a"]["run_id"],
                                   "b": result["run_b"]["run_id"], "workload": "mcf"})
                with urlopen(url + "/api/compare?" + query) as response:
                    self.assertEqual(json.load(response)["status"], "comparable")
                with urlopen(url + "/api/baseline?" + urlencode({"run": result["run_b"]["run_id"]})) as response:
                    self.assertEqual(json.load(response)["status"], "found")
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

            (root / "run-2/mcf_2_0.3/simulator_out.txt").unlink()
            partial = build_manifest(receipts[1], repo)
            self.assertEqual(partial["run"]["status"], "partial")
            self.assertEqual(len(partial["slice_results"]), 3)
            self.assertEqual(next(a for a in partial["aggregate_results"]
                                  if a["object_id"] == "mcf")["status"], "partial")

            # Original directory spellings, not canonical slice names, determine freshness.
            original = root / "run-1/milc_3_1.0"
            self.assertNotEqual(original.name, "milc_3_1")
            score = root / "score.txt"
            score.write_text("custom score placeholder")
            os.utime(score, (1, 1))
            receipt = json.loads(receipts[0].read_text())
            receipt["score_file"] = str(score)
            receipts[0].write_text(json.dumps(receipt))
            stale = build_manifest(receipts[0], repo)
            self.assertEqual(stale["run"]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
