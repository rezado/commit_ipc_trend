import unittest

from commit_ipc_trend.anomalies import counter_clues, select_slice_candidates


class AnomalySelectionTest(unittest.TestCase):
    def test_contribution_and_degradation_select_different_slices(self):
        slices = [
            {"slice_id": "large-drop", "cpi_degradation_percent": 10.0,
             "weighted_cpi_contribution": 0.01},
            {"slice_id": "large-impact", "cpi_degradation_percent": 0.4,
             "weighted_cpi_contribution": 0.1},
            {"slice_id": "improved", "cpi_degradation_percent": -10.0,
             "weighted_cpi_contribution": -0.2},
        ]
        candidates = select_slice_candidates(slices, True, 0.5, 1)
        self.assertEqual([row["slice_id"] for row in candidates], ["large-impact", "large-drop"])
        self.assertEqual(candidates[0]["reasons"], ["top_weighted_cpi_contributor"])
        self.assertEqual(candidates[1]["reasons"], ["slice_cpi_degradation"])
        self.assertNotIn("reasons", slices[0])
        self.assertEqual(
            [row["slice_id"] for row in select_slice_candidates(slices, False, 0.5, 1)],
            ["large-drop"],
        )

    def test_counter_pairing_respects_window_semantics_and_availability(self):
        base = {
            "run_id": "a", "metric_id": "stall", "semantic_version": "v1",
            "value": 100, "availability": "available", "window_id": "perf_final_dump",
            "display_name": "Stall", "category": "backend", "unit": "cycles",
            "direction": "lower_is_better",
        }
        target = {**base, "run_id": "b", "value": 120}
        clues = counter_clues([base, target], "a", "b", 5, 5)
        self.assertEqual(len(clues), 1)
        self.assertAlmostEqual(clues[0]["directional_degradation_percent"], 20)
        for changes in (
            {"window_id": "roi_summary"},
            {"semantic_version": "v2"},
            {"availability": "missing"},
            {"value": None},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(counter_clues([base, {**target, **changes}], "a", "b", 5, 5), [])
        self.assertEqual(counter_clues([{**base, "value": 0}, target], "a", "b", 5, 5), [])
        self.assertEqual(counter_clues([base, target], "a", "b", 5, 0), [])


if __name__ == "__main__":
    unittest.main()
