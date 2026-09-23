"""Page-independent trend and A/B query service."""

from __future__ import annotations

import math
import sqlite3
from typing import Any, Iterable

from .metrics import cpi_contributions, degradation_percent
from .store import Store


SLICE_METRICS = {
    "ipc": "sr.ipc_computed",
    "ipc_reported": "sr.ipc_reported",
    "cpi": "sr.cpi",
    "cycles": "sr.cycles",
    "instructions": "sr.instructions",
}
PUBLISHED_STATUSES = ("published",)


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


class TrendService:
    def __init__(self, store: Store):
        self.store = store
        self.connection = store.connection

    def list_filters(self) -> dict[str, list[str]]:
        return {
            "configs": [
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT config FROM runs ORDER BY config"
                )
            ],
            "comparison_keys": [
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT comparison_key FROM runs ORDER BY comparison_key"
                )
            ],
            "slice_sets": [
                row[0]
                for row in self.connection.execute(
                    "SELECT slice_set_id FROM slice_sets ORDER BY name, version"
                )
            ],
            "slices": [
                row[0]
                for row in self.connection.execute(
                    "SELECT slice_name FROM slices ORDER BY checkpoint"
                )
            ],
            "counters": [
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT metric_id FROM metric_definitions ORDER BY metric_id"
                )
            ],
        }

    def get_trend(
        self,
        level: str,
        object_ids: str | Iterable[str],
        metric_id: str,
        comparison_key: str | None = None,
        include_non_published: bool = False,
    ) -> list[dict[str, Any]]:
        objects = [object_ids] if isinstance(object_ids, str) else list(object_ids)
        if not objects:
            return []
        placeholders = ",".join("?" for _ in objects)
        object_params: list[Any] = list(objects)
        run_filter = ""
        if not include_non_published:
            run_filter += " AND r.status = 'published'"
        if comparison_key:
            run_filter += " AND r.comparison_key = ?"

        if level in {"suite", "benchmark", "workload"}:
            params: list[Any] = [level, *object_params, metric_id]
            if comparison_key:
                params.append(comparison_key)
            cursor = self.connection.execute(
                f"""
                SELECT a.object_id, a.value, a.status, a.coverage_weight,
                       a.formula_version AS metric_version, a.source_uri,
                       r.run_id, r.status AS run_status, r.comparison_key,
                       r.parser_version, r.source_uri AS run_source_uri,
                       r.experiment_json, c.commit_sha, c.short_sha,
                       c.commit_time, c.commit_epoch, c.subject
                  FROM aggregate_results a
                  JOIN runs r ON r.run_id = a.run_id
                  JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE a.level = ? AND a.object_id IN ({placeholders})
                   AND a.metric_id = ? {run_filter}
                 ORDER BY a.object_id, c.commit_epoch, r.snapshot_at
                """,
                params,
            )
        elif level == "slice" and metric_id in SLICE_METRICS:
            params = list(object_params)
            if comparison_key:
                params.append(comparison_key)
            cursor = self.connection.execute(
                f"""
                SELECT s.slice_name AS object_id, {SLICE_METRICS[metric_id]} AS value,
                       sr.status, NULL AS coverage_weight,
                       'slice-core/v1' AS metric_version, sr.source_out_uri AS source_uri,
                       r.run_id, r.status AS run_status, r.comparison_key,
                       r.parser_version, r.source_uri AS run_source_uri,
                       r.experiment_json, c.commit_sha, c.short_sha,
                       c.commit_time, c.commit_epoch, c.subject
                  FROM slice_results sr
                  JOIN slices s ON s.slice_id = sr.slice_id
                  JOIN runs r ON r.run_id = sr.run_id
                  JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE s.slice_name IN ({placeholders}) {run_filter}
                 ORDER BY s.slice_name, c.commit_epoch, r.snapshot_at
                """,
                params,
            )
        elif level == "counter":
            params = [*object_params, metric_id]
            if comparison_key:
                params.append(comparison_key)
            cursor = self.connection.execute(
                f"""
                SELECT s.slice_name AS object_id, cv.value, cv.availability AS status,
                       NULL AS coverage_weight, cv.semantic_version AS metric_version,
                       cv.source_uri, cv.window_id, cv.dump_time,
                       r.run_id, r.status AS run_status, r.comparison_key,
                       r.parser_version, r.source_uri AS run_source_uri,
                       r.experiment_json, c.commit_sha, c.short_sha,
                       c.commit_time, c.commit_epoch, c.subject
                  FROM counter_values cv
                  JOIN slices s ON s.slice_id = cv.slice_id
                  JOIN runs r ON r.run_id = cv.run_id
                  JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE s.slice_name IN ({placeholders}) AND cv.metric_id = ? {run_filter}
                 ORDER BY s.slice_name, c.commit_epoch, r.snapshot_at
                """,
                params,
            )
        else:
            raise ValueError(f"unsupported trend query: level={level}, metric={metric_id}")

        result = _rows(cursor)
        previous: dict[str, dict[str, Any]] = {}
        for row in result:
            row["experiment"] = json_loads(row.pop("experiment_json"))
            prior = previous.get(row["object_id"])
            reasons = []
            if prior:
                if prior["comparison_key"] != row["comparison_key"]:
                    reasons.append("comparison_key_changed")
                if prior["metric_version"] != row["metric_version"]:
                    reasons.append("metric_version_changed")
                if prior["status"] not in {"valid", "available"}:
                    reasons.append("previous_point_invalid")
                if row["status"] not in {"valid", "available"}:
                    reasons.append("current_point_invalid")
            row["break_before"] = bool(prior and reasons)
            row["break_reasons"] = reasons
            previous[row["object_id"]] = row
        return result

    def compare_points(
        self,
        run_a: str,
        run_b: str,
        level: str = "workload",
        object_id: str = "mcf",
        top_n: int | None = None,
    ) -> dict[str, Any]:
        if level != "workload":
            raise ValueError("comparison supports workload level only")
        a = self._resolve_run(run_a)
        b = self._resolve_run(run_b)
        reasons = []
        if a["comparison_key"] != b["comparison_key"]:
            reasons.append("comparison_key_mismatch")
        if a["slice_set_id"] != b["slice_set_id"]:
            reasons.append("slice_set_mismatch")
        if a["status"] != "published" or b["status"] != "published":
            reasons.append("run_not_published")
        if reasons:
            return {
                "status": "incomparable",
                "reasons": reasons,
                "run_a": self._public_run(a),
                "run_b": self._public_run(b),
            }

        rows_a = self._slice_rows(a["run_id"], object_id)
        rows_b = self._slice_rows(b["run_id"], object_id)
        expected = {
            row[0] for row in self.connection.execute(
                """SELECT s.slice_id FROM slice_set_members m
                     JOIN slices s ON s.slice_id = m.slice_id
                    WHERE m.slice_set_id = ? AND s.workload = ?""",
                (a["slice_set_id"], object_id),
            )
        }
        if not expected:
            raise ValueError(f"workload not found in slice set: {object_id}")
        if {row["slice_id"] for row in rows_a} != expected or {row["slice_id"] for row in rows_b} != expected:
            return {
                "status": "incomparable",
                "reasons": ["missing_slice_result"],
                "run_a": self._public_run(a),
                "run_b": self._public_run(b),
            }
        if top_n is not None:
            if top_n < 1:
                raise ValueError("top_n must be positive")
            selected = {
                row["slice_id"]
                for row in sorted(rows_a, key=lambda row: (-row["weight"], row["slice"]))[:top_n]
            }
            rows_a = [row for row in rows_a if row["slice_id"] in selected]
            rows_b = [row for row in rows_b if row["slice_id"] in selected]
        if any(row["status"] != "valid" for row in [*rows_a, *rows_b]):
            return {
                "status": "incomparable",
                "reasons": ["invalid_slice_result"],
                "run_a": self._public_run(a),
                "run_b": self._public_run(b),
            }

        total_weight = math.fsum(
            row["weight"] for row in self._slice_rows(a["run_id"], object_id)
        )
        if math.isclose(total_weight, 1.0, abs_tol=1e-5):
            total_weight = 1.0
        selected_weight = math.fsum(row["weight"] for row in rows_a)
        weighted_sum_a = math.fsum(row["weight"] * row["cpi"] for row in rows_a)
        weighted_sum_b = math.fsum(row["weight"] * row["cpi"] for row in rows_b)
        contributions = cpi_contributions(rows_a, rows_b)
        contributions.sort(
            key=lambda row: row["weighted_cpi_contribution"], reverse=True
        )
        contribution_sum = math.fsum(
            row["weighted_cpi_contribution"] for row in contributions
        )
        weighted_sum_delta = weighted_sum_b - weighted_sum_a
        coverage = selected_weight / total_weight
        if math.isclose(coverage, 1.0, abs_tol=1e-5):
            coverage = 1.0
        diagnostic = (top_n is not None or not math.isclose(coverage, 1.0, abs_tol=1e-5)
                      or not math.isclose(total_weight, 1.0, abs_tol=1e-5))
        diagnostic_cpi_a = weighted_sum_a / selected_weight if selected_weight else None
        diagnostic_cpi_b = weighted_sum_b / selected_weight if selected_weight else None
        comparison_cpi_a = diagnostic_cpi_a if diagnostic else weighted_sum_a
        comparison_cpi_b = diagnostic_cpi_b if diagnostic else weighted_sum_b
        warnings = []
        if json_loads(a["experiment_json"]).get("comparison_status") == "provisional":
            warnings.append("comparison_key_is_provisional")
        return {
            "status": "comparable",
            "mode": "diagnostic_partial_subset" if diagnostic else "full_slice_set",
            "run_a": self._public_run(a),
            "run_b": self._public_run(b),
            "object_id": object_id,
            "coverage_weight": selected_weight,
            "selected_weight": selected_weight,
            "selected_slice_count": len(rows_a),
            "total_slice_count": len(expected),
            "weighted_cpi_a": None if diagnostic else weighted_sum_a,
            "weighted_cpi_b": None if diagnostic else weighted_sum_b,
            "weighted_cpi_delta": None if diagnostic else weighted_sum_delta,
            "weighted_cpi_numerator_a": weighted_sum_a,
            "weighted_cpi_numerator_b": weighted_sum_b,
            "weighted_cpi_numerator_delta": weighted_sum_delta,
            "equivalent_ipc_a": None if diagnostic else 1.0 / weighted_sum_a,
            "equivalent_ipc_b": None if diagnostic else 1.0 / weighted_sum_b,
            "diagnostic_subset_cpi_a": diagnostic_cpi_a if diagnostic else None,
            "diagnostic_subset_cpi_b": diagnostic_cpi_b if diagnostic else None,
            "diagnostic_subset_cpi_delta": (
                diagnostic_cpi_b - diagnostic_cpi_a if diagnostic else None
            ),
            "cpi_degradation_percent": degradation_percent(
                comparison_cpi_a, comparison_cpi_b, "lower_is_better"
            ),
            "contribution_sum": contribution_sum,
            "contribution_identity_error": contribution_sum - weighted_sum_delta,
            "slices": contributions,
            "warnings": warnings,
        }

    def get_children(
        self, object_id: str, run_a: str, run_b: str, top_n: int | None = None
    ) -> list[dict[str, Any]]:
        return self.compare_points(run_a, run_b, "workload", object_id, top_n).get(
            "slices", []
        )

    def get_artifacts(self, run_id: str, slice_id: str | None = None) -> list[dict[str, Any]]:
        if slice_id is None:
            return _rows(
                self.connection.execute(
                    "SELECT * FROM artifacts WHERE run_id = ? ORDER BY kind, uri", (run_id,)
                )
            )
        return _rows(
            self.connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND slice_id = ? ORDER BY kind, uri",
                (run_id, slice_id),
            )
        )

    def _resolve_run(self, identifier: str) -> dict[str, Any]:
        rows = _rows(
            self.connection.execute(
                """
                SELECT r.*, c.short_sha, c.commit_time, c.subject
                  FROM runs r JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE r.run_id = ? OR r.commit_sha = ? OR c.short_sha = ?
                 ORDER BY r.snapshot_at DESC
                """,
                (identifier, identifier, identifier),
            )
        )
        if not rows:
            raise ValueError(f"run not found: {identifier}")
        if len(rows) > 1:
            raise ValueError(f"run identifier is ambiguous; use run_id: {identifier}")
        return rows[0]

    def _slice_rows(self, run_id: str, workload: str) -> list[dict[str, Any]]:
        return _rows(
            self.connection.execute(
                """
                SELECT sr.*, s.slice_name AS slice, s.checkpoint, m.weight
                  FROM slice_results sr
                  JOIN slices s ON s.slice_id = sr.slice_id
                  JOIN runs r ON r.run_id = sr.run_id
                  JOIN slice_set_members m
                    ON m.slice_set_id = r.slice_set_id AND m.slice_id = sr.slice_id
                 WHERE sr.run_id = ? AND s.workload = ?
                 ORDER BY m.ordinal
                """,
                (run_id, workload),
            )
        )

    @staticmethod
    def _public_run(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: run[key]
            for key in (
                "run_id",
                "commit_sha",
                "short_sha",
                "commit_time",
                "subject",
                "status",
                "source_uri",
                "parser_version",
                "comparison_key",
                "slice_set_id",
            )
        }


def json_loads(value: str) -> dict[str, Any]:
    import json

    return json.loads(value)
