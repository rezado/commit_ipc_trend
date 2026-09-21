from __future__ import annotations

import unittest

from commit_ipc_trend.metrics import aggregate_cpi, degradation_percent


class MetricsTest(unittest.TestCase):
    def test_weighted_cpi_requires_complete_coverage(self):
        rows = [
            {"status": "valid", "weight": 0.7, "cpi": 2.0},
            {"status": "missing", "weight": 0.3, "cpi": None},
        ]
        result = aggregate_cpi(rows, 1.0)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["weighted_cpi"])
        self.assertAlmostEqual(result["coverage_weight"], 0.7)

    def test_weighted_cpi_tolerates_rounded_weight_sum(self):
        rows = [
            {"status": "valid", "weight": 0.6, "cpi": 1.0},
            {"status": "valid", "weight": 0.4000002, "cpi": 2.0},
        ]
        result = aggregate_cpi(rows, 1.0000002)
        self.assertAlmostEqual(result["weighted_cpi"], 1.4000004)
        self.assertAlmostEqual(result["coverage_weight"], 1.0)

    def test_degradation_direction_and_near_zero(self):
        self.assertAlmostEqual(degradation_percent(10, 8, "higher_is_better"), 25.0)
        self.assertAlmostEqual(degradation_percent(10, 12, "lower_is_better"), 20.0)
        self.assertIsNone(degradation_percent(0, 1, "lower_is_better"))


if __name__ == "__main__":
    unittest.main()
