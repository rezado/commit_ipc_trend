from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from commit_ipc_trend.schema import PARSER_VERSION, SCHEMA_VERSION
from commit_ipc_trend.service import TrendService
from commit_ipc_trend.store import Store


def manifest(short_sha: str, run_id: str, cpis: tuple[float, float], comparison: str = "same"):
    commit_sha = short_sha + "0" * (40 - len(short_sha))
    members = [
        {
            "slice_id": "slice-a",
            "benchmark": "429.mcf",
            "workload": "mcf",
            "slice": "mcf_1_0.7",
            "checkpoint": 1,
            "checkpoint_identity": "/cp/1",
            "restore_mode": "checkpoint-image",
            "warmup_definition": "warmup",
            "roi_definition": "roi",
            "weight": 0.7,
            "weight_kind": "simpoint_weight_unverified",
            "ordinal": 0,
        },
        {
            "slice_id": "slice-b",
            "benchmark": "429.mcf",
            "workload": "mcf",
            "slice": "mcf_2_0.3",
            "checkpoint": 2,
            "checkpoint_identity": "/cp/2",
            "restore_mode": "checkpoint-image",
            "warmup_definition": "warmup",
            "roi_definition": "roi",
            "weight": 0.3,
            "weight_kind": "simpoint_weight_unverified",
            "ordinal": 1,
        },
    ]
    results = []
    for member, cpi in zip(members, cpis):
        results.append(
            {
                "slice_id": member["slice_id"],
                "status": "valid",
                "seed": 1,
                "window_id": "roi_summary",
                "instructions": 100,
                "cycles": round(cpi * 100),
                "ipc_reported": 1 / cpi,
                "ipc_computed": 1 / cpi,
                "cpi": cpi,
                "source_out_uri": f"/raw/{run_id}/{member['slice']}/out",
                "source_err_uri": f"/raw/{run_id}/{member['slice']}/err",
                "error_code": None,
            }
        )
    weighted = sum(member["weight"] * cpi for member, cpi in zip(members, cpis))
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "commit": {
            "commit_sha": commit_sha,
            "short_sha": short_sha,
            "parents": [],
            "commit_epoch": 1 if run_id == "run-a" else 2,
            "commit_time": "2026-09-01T00:00:00+00:00",
            "subject": run_id,
        },
        "run": {
            "run_id": run_id,
            "commit_sha": commit_sha,
            "branch_label": "DefaultConfig",
            "config": "DefaultConfig",
            "started_at": "2026-09-01T00:00:00+00:00",
            "completed_at": "2026-09-01T01:00:00+00:00",
            "score_generated_at": "2026-09-01T01:00:00+00:00",
            "snapshot_at": "2026-09-01T01:00:00+00:00",
            "source_uri": f"/raw/{run_id}",
            "status": "published",
            "parser_version": PARSER_VERSION,
            "score_formula_version": "score/v1",
            "comparison_key": comparison,
            "experiment": {"comparison_status": "provisional"},
            "snapshot_identity": run_id,
        },
        "slice_set": {
            "slice_set_id": "slice-set",
            "name": "mcf",
            "version": "v1",
            "source_uri": "/profile/mcf.json",
            "members": members,
        },
        "scores": [
            {
                "level": "suite",
                "object_id": "SPEC2006",
                "metric_id": "score_per_ghz",
                "formula_version": "score/v1",
                "value": 20.0,
                "numerator": None,
                "denominator": None,
                "coverage_weight": None,
                "valid_member_count": None,
                "total_member_count": None,
                "status": "valid",
                "source_uri": f"/raw/{run_id}/score",
            }
        ],
        "slice_results": results,
        "metric_definitions": [
            {
                "metric_id": "zero_counter",
                "semantic_version": "v1",
                "display_name": "Zero counter",
                "category": "test",
                "kind": "counter",
                "unit": "count",
                "direction": "lower_is_better",
                "source_path": "path",
                "source_name": "name",
                "aggregation_rule": "unique",
                "expected_matches": 1,
            }
        ],
        "counter_values": [
            {
                "slice_id": "slice-a",
                "metric_id": "zero_counter",
                "semantic_version": "v1",
                "raw_value": 0 if run_id == "run-a" else None,
                "numerator": None,
                "denominator": None,
                "value": 0 if run_id == "run-a" else None,
                "availability": "available" if run_id == "run-a" else "missing",
                "window_id": "perf_final_dump",
                "dump_time": 100,
                "source_uri": f"/raw/{run_id}/err",
            }
        ],
        "aggregate_results": [
            {
                "level": "workload",
                "object_id": "mcf",
                "metric_id": "weighted_cpi",
                "formula_version": "cpi/v1",
                "value": weighted,
                "numerator": weighted,
                "denominator": 1.0,
                "coverage_weight": 1.0,
                "valid_member_count": 2,
                "total_member_count": 2,
                "status": "valid",
                "source_uri": "/profile/mcf.json",
            }
        ],
        "artifacts": [
            {"slice_id": None, "kind": "score", "uri": f"/raw/{run_id}/score"}
        ],
    }


class StoreServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "demo.sqlite")
        self.store.initialize()
        self.manifests = [
            manifest("aaaaaaaaa", "run-a", (2.0, 1.0)),
            manifest("bbbbbbbbb", "run-b", (2.2, 0.5)),
        ]

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_import_is_idempotent_and_preserves_zero_vs_missing(self):
        first = self.store.import_manifests(self.manifests)["stats"]
        second = self.store.import_manifests(self.manifests)["stats"]
        self.assertEqual(first, second)
        self.assertEqual(second["runs"], 2)
        self.assertEqual(second["slice_results"], 4)
        trend = TrendService(self.store).get_trend(
            "counter", "mcf_1_0.7", "zero_counter"
        )
        self.assertEqual(trend[0]["value"], 0)
        self.assertEqual(trend[0]["status"], "available")
        self.assertIsNone(trend[1]["value"])
        self.assertEqual(trend[1]["status"], "missing")

    def test_ab_contribution_identity_and_partial_label(self):
        self.store.import_manifests(self.manifests)
        result = TrendService(self.store).compare_points("aaaaaaaaa", "bbbbbbbbb", top_n=1)
        self.assertEqual(result["status"], "comparable")
        self.assertEqual(result["mode"], "diagnostic_partial_subset")
        self.assertAlmostEqual(result["coverage_weight"], 0.7)
        self.assertAlmostEqual(result["contribution_identity_error"], 0.0, places=12)
        self.assertIsNone(result["weighted_cpi_a"])
        self.assertIsNone(result["equivalent_ipc_a"])
        self.assertAlmostEqual(result["diagnostic_subset_cpi_a"], 2.0)

    def test_ab_full_workload_uses_workload_scoped_weight(self):
        # A global slice set may contain many workloads whose weights each sum to 1.
        # The comparison coverage denominator must only include the selected workload.
        for item in self.manifests:
            member = {
                "slice_id": "slice-gcc",
                "benchmark": "403.gcc",
                "workload": "gcc_ref",
                "slice": "gcc_ref_1_1.0",
                "checkpoint": 1,
                "checkpoint_identity": "/cp/gcc/1",
                "restore_mode": "checkpoint-image",
                "warmup_definition": "warmup",
                "roi_definition": "roi",
                "weight": 1.0,
                "weight_kind": "simpoint_weight_unverified",
                "ordinal": 2,
            }
            item["slice_set"]["members"].append(member)
            item["slice_results"].append(
                {
                    "slice_id": "slice-gcc",
                    "status": "valid",
                    "seed": 1,
                    "window_id": "roi_summary",
                    "instructions": 100,
                    "cycles": 100,
                    "ipc_reported": 1.0,
                    "ipc_computed": 1.0,
                    "cpi": 1.0,
                    "source_out_uri": f"/raw/{item['run']['run_id']}/gcc/out",
                    "source_err_uri": f"/raw/{item['run']['run_id']}/gcc/err",
                    "error_code": None,
                }
            )
        self.store.import_manifests(self.manifests)
        result = TrendService(self.store).compare_points("run-a", "run-b", object_id="mcf")
        self.assertEqual(result["status"], "comparable")
        self.assertEqual(result["mode"], "full_slice_set")
        self.assertEqual(result["coverage_weight"], 1.0)
        self.assertEqual(result["total_slice_count"], 2)

        gcc = TrendService(self.store).compare_points(
            "run-a", "run-b", object_id="gcc_ref"
        )
        self.assertEqual(gcc["status"], "comparable")
        self.assertEqual(gcc["mode"], "full_slice_set")
        self.assertEqual(gcc["total_slice_count"], 1)

    def test_incomplete_workload_membership_is_incomparable(self):
        self.store.import_manifests(self.manifests)
        self.store.connection.execute(
            "DELETE FROM slice_results WHERE run_id = ? AND slice_id = ?",
            ("run-b", "slice-b"),
        )
        result = TrendService(self.store).compare_points("run-a", "run-b")
        self.assertEqual(result["status"], "incomparable")
        self.assertIn("slice_membership_incomplete", result["reasons"])
        self.assertEqual(result["membership"]["missing_in_b"], ["slice-b"])

    def test_anomaly_report_auto_selects_prior_and_includes_counter_clues(self):
        self.manifests[1] = manifest("bbbbbbbbb", "run-b", (2.2, 1.0))
        for item, value in zip(self.manifests, (100.0, 120.0)):
            item["counter_values"][0].update(
                {
                    "raw_value": value,
                    "value": value,
                    "availability": "available",
                }
            )
        self.store.import_manifests(self.manifests)
        report = TrendService(self.store).detect_anomalies("run-b")
        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["policy"]["rerun_required"])
        comparison = report["comparisons"][0]
        self.assertEqual(comparison["baseline_basis"], "nearest_prior_compatible")
        self.assertEqual(comparison["summary"]["anomalous_workload_count"], 1)
        self.assertGreaterEqual(comparison["summary"]["anomalous_slice_count"], 1)
        workload = comparison["workloads"][0]
        self.assertTrue(workload["is_anomaly"])
        slice_a = next(
            row for row in workload["anomalous_slices"] if row["slice_id"] == "slice-a"
        )
        self.assertIn("slice_cpi_degradation", slice_a["reasons"])
        self.assertEqual(slice_a["counter_clues"][0]["metric_id"], "zero_counter")
        self.assertAlmostEqual(
            slice_a["counter_clues"][0]["directional_degradation_percent"], 20.0
        )

    def test_comparison_key_mismatch_is_incomparable(self):
        self.manifests[1]["run"]["comparison_key"] = "different"
        self.store.import_manifests(self.manifests)
        result = TrendService(self.store).compare_points("aaaaaaaaa", "bbbbbbbbb")
        self.assertEqual(result["status"], "incomparable")
        self.assertIn("comparison_key_mismatch", result["reasons"])

    def test_reimport_replaces_run_scoped_rows(self):
        self.store.import_manifests([self.manifests[0]])
        replacement = copy.deepcopy(self.manifests[0])
        replacement["counter_values"] = []
        replacement["scores"] = []
        replacement["aggregate_results"] = []
        replacement["artifacts"] = []
        stats = self.store.import_manifests([replacement])["stats"]

        self.assertEqual(stats["counter_values"], 0)
        self.assertEqual(stats["aggregate_results"], 0)
        self.assertEqual(stats["artifacts"], 0)
        self.assertEqual(stats["slice_results"], 2)

    def test_metric_semantic_version_cannot_be_redefined(self):
        self.store.import_manifests([self.manifests[0]])
        changed = copy.deepcopy(self.manifests[0])
        changed["metric_definitions"][0]["direction"] = "higher_is_better"
        with self.assertRaisesRegex(ValueError, "immutable metric_definitions"):
            self.store.import_manifests([changed])
        direction = self.store.connection.execute(
            "SELECT direction FROM metric_definitions WHERE metric_id = 'zero_counter'"
        ).fetchone()[0]
        self.assertEqual(direction, "lower_is_better")

    def test_skipped_counter_import_does_not_erase_existing_values(self):
        self.store.import_manifests([self.manifests[0]])
        replacement = copy.deepcopy(self.manifests[0])
        replacement["counter_import"] = "skipped"
        replacement["counter_values"] = []
        stats = self.store.import_manifests([replacement])["stats"]
        self.assertEqual(stats["counter_values"], 1)

    def test_read_only_store_requires_existing_valid_database(self):
        missing = Path(self.temporary.name) / "missing.sqlite"
        with self.assertRaises(FileNotFoundError):
            Store(missing, read_only=True)
        self.assertFalse(missing.exists())

        with Store(self.store.path, read_only=True) as reader:
            reader.validate_schema()
            self.assertEqual(reader.counts()["runs"], 0)


if __name__ == "__main__":
    unittest.main()
