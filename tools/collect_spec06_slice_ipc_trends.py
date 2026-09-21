#!/usr/bin/env python3
"""Shared SPEC06 slice parsing helpers for the database dashboard builder.

This module is intentionally library-only. Dashboard data is produced by
``build_mainline_september_dashboard.py`` and stored in SQLite; it does not
provide the removed standalone plotting workflow.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_COMMITS = (
    "cr260901-a355c5525-DefaultConfig",
    "cr260904-017a698e0-DefaultConfig",
    "cr260907-df157ddd3-DefaultConfig",
    "cr260910-50b9c3387-DefaultConfig",
    "cr260913-151e60593-DefaultConfig",
    "cr260916-b625e1616-DefaultConfig",
    "cr260918-575aef181-DefaultConfig",
)


def candidate_git_repos(explicit: Path | None) -> Iterable[Path]:
    if explicit is not None:
        yield explicit
        return
    local = Path("/nfs/home/wujiabin/work/XiangShan")
    if (local / ".git").exists():
        yield local


def git_metadata(short_sha: str, repos: Iterable[Path]) -> dict[str, object]:
    for repo in repos:
        result = subprocess.run(
            ("git", "-c", f"safe.directory={repo}", "-C", str(repo), "show", "-s",
             "--format=%H%x00%ct%x00%cI%x00%s", short_sha),
            check=False, capture_output=True, text=True,
        )
        if result.returncode == 0:
            full_sha, epoch, commit_time, subject = result.stdout.rstrip("\n").split("\0", 3)
            return {
                "commit": full_sha,
                "commit_epoch": int(epoch),
                "commit_time": commit_time,
                "subject": subject,
            }
    raise ValueError(f"cannot resolve Git commit: {short_sha}")


DEFAULT_CHECKPOINTS = ROOT / "spec06_gcc16_rva23_novec_260820-checkpoints.txt"
DEFAULT_REPORT_ROOT = Path("/nfs/home/cirunner/perf-report")
DEFAULT_OUTPUT_DIR = ROOT / "outputs/mainline-september"
SLICE_RE = re.compile(
    r"^(?P<workload>.+)_(?P<checkpoint>\d+)_(?P<weight>[0-9]+(?:\.[0-9]+)?)$"
)
SUMMARY_RE = re.compile(
    r"instrCnt\s*=\s*([0-9,]+),\s*cycleCnt\s*=\s*([0-9,]+),\s*IPC\s*=\s*([0-9.eE+-]+)"
)
RUN_RE = re.compile(r"^cr\d{6}-(?P<sha>[0-9a-f]{7,40})-(?P<config>.+Config)$")

BG = (247, 249, 252)
INK = (34, 43, 56)
MUTED = (96, 110, 130)
GRID = (218, 225, 233)
WHITE = (255, 255, 255)
RED = (191, 62, 53)
BLUE = (36, 108, 168)


@dataclass(frozen=True)
class SliceSpec:
    order: int
    name: str
    workload: str
    checkpoint: int
    weight: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoints", type=Path, default=DEFAULT_CHECKPOINTS)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--commit-dir", action="append", dest="commit_dirs")
    parser.add_argument("--git-repo", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return a non-zero status when any listed slice lacks a valid IPC",
    )
    return parser.parse_args()


def load_slices(path: Path) -> list[SliceSpec]:
    slices: list[SliceSpec] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        name = raw.strip().removesuffix("/")
        if not name:
            continue
        match = SLICE_RE.fullmatch(name)
        if not match:
            raise ValueError(f"invalid checkpoint entry at {path}:{line_number}: {raw!r}")
        if name in seen:
            raise ValueError(f"duplicate checkpoint entry at {path}:{line_number}: {name}")
        seen.add(name)
        slices.append(
            SliceSpec(
                order=len(slices) + 1,
                name=name,
                workload=match.group("workload"),
                checkpoint=int(match.group("checkpoint")),
                weight=float(match.group("weight")),
            )
        )
    if not slices:
        raise ValueError(f"no checkpoints in {path}")
    return slices


def parse_ipc(path: Path) -> tuple[int, int, float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = SUMMARY_RE.findall(text)
    if not matches:
        raise ValueError("missing ROI IPC summary")
    instructions_raw, cycles_raw, ipc_raw = matches[-1]
    return (
        int(instructions_raw.replace(",", "")),
        int(cycles_raw.replace(",", "")),
        float(ipc_raw),
    )


def load_runs(
    report_root: Path, commit_dirs: Iterable[str], repos: tuple[Path, ...]
) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    configs: set[str] = set()
    for directory in commit_dirs:
        match = RUN_RE.fullmatch(directory)
        if not match:
            raise ValueError(f"unexpected report directory name: {directory}")
        path = report_root / directory
        if not path.is_dir():
            raise FileNotFoundError(path)
        metadata = git_metadata(match.group("sha"), repos)
        metadata.update(
            {
                "directory": directory,
                "short_commit": match.group("sha"),
                "config": match.group("config"),
            }
        )
        runs.append(metadata)
        configs.add(match.group("config"))
    if len(configs) != 1:
        raise ValueError(f"mixed configurations are not comparable: {sorted(configs)}")
    runs.sort(key=lambda row: (int(row["commit_epoch"]), str(row["short_commit"])))
    for index, run in enumerate(runs, 1):
        run["commit_order"] = index
    return runs


def collect_observations(
    report_root: Path, slices: list[SliceSpec], runs: list[dict[str, object]]
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for spec in slices:
        previous_ipc: float | None = None
        previous_order: int | None = None
        for run in runs:
            source = report_root / str(run["directory"]) / spec.name / "simulator_out.txt"
            try:
                instructions, cycles, ipc = parse_ipc(source)
            except (OSError, ValueError) as error:
                errors.append(
                    {
                        "slice": spec.name,
                        "short_commit": str(run["short_commit"]),
                        "source": str(source),
                        "error": str(error),
                    }
                )
                continue
            commit_order = int(run["commit_order"])
            change = (
                (ipc / previous_ipc - 1.0) * 100.0
                if previous_ipc is not None and previous_order == commit_order - 1
                else None
            )
            rows.append(
                {
                    "slice_order": spec.order,
                    "slice": spec.name,
                    "workload": spec.workload,
                    "checkpoint": spec.checkpoint,
                    "weight": spec.weight,
                    "commit_order": run["commit_order"],
                    "commit": run["commit"],
                    "short_commit": run["short_commit"],
                    "commit_time": run["commit_time"],
                    "subject": run["subject"],
                    "directory": run["directory"],
                    "config": run["config"],
                    "instructions": instructions,
                    "cycles": cycles,
                    "ipc": ipc,
                    "change_from_previous_pct": change,
                    "source": str(source),
                }
            )
            previous_ipc = ipc
            previous_order = commit_order
    return rows, errors


def rows_by_slice(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["slice"]), []).append(row)
    return grouped


def summarize_slices(
    slices: list[SliceSpec], rows: list[dict[str, object]], expected_count: int
) -> list[dict[str, object]]:
    grouped = rows_by_slice(rows)
    summaries: list[dict[str, object]] = []
    for spec in slices:
        series = sorted(grouped.get(spec.name, []), key=lambda row: int(row["commit_order"]))
        if not series:
            summaries.append(
                {
                    "slice_order": spec.order,
                    "slice": spec.name,
                    "workload": spec.workload,
                    "checkpoint": spec.checkpoint,
                    "weight": spec.weight,
                    "observations": 0,
                    "status": "missing",
                }
            )
            continue
        values = [float(row["ipc"]) for row in series]
        steps = [
            (float(row["change_from_previous_pct"]), series[index - 1], row)
            for index, row in enumerate(series[1:], 1)
            if row["change_from_previous_pct"] is not None
            and int(series[index - 1]["commit_order"]) == int(row["commit_order"]) - 1
        ]
        largest = max(steps, key=lambda item: abs(item[0])) if steps else None
        minimum = min(series, key=lambda row: float(row["ipc"]))
        maximum = max(series, key=lambda row: float(row["ipc"]))
        summaries.append(
            {
                "slice_order": spec.order,
                "slice": spec.name,
                "workload": spec.workload,
                "checkpoint": spec.checkpoint,
                "weight": spec.weight,
                "observations": len(series),
                "status": "complete" if len(series) == expected_count else "partial",
                "first_commit": series[0]["short_commit"],
                "first_ipc": values[0],
                "last_commit": series[-1]["short_commit"],
                "last_ipc": values[-1],
                "first_to_last_pct": (values[-1] / values[0] - 1.0) * 100.0,
                "min_ipc": minimum["ipc"],
                "min_commit": minimum["short_commit"],
                "max_ipc": maximum["ipc"],
                "max_commit": maximum["short_commit"],
                "range_pct_of_min": (max(values) / min(values) - 1.0) * 100.0,
                "largest_step_pct": largest[0] if largest else None,
                "largest_step_from": largest[1]["short_commit"] if largest else None,
                "largest_step_to": largest[2]["short_commit"] if largest else None,
            }
        )
    return summaries


def workload_trends(
    slices: list[SliceSpec], rows: list[dict[str, object]], runs: list[dict[str, object]]
) -> list[dict[str, object]]:
    weights = {spec.name: spec.weight for spec in slices}
    expected_names: dict[str, set[str]] = {}
    for spec in slices:
        expected_names.setdefault(spec.workload, set()).add(spec.name)
    expected_weights = {
        workload: math.fsum(weights[name] for name in names)
        for workload, names in expected_names.items()
    }
    grouped: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row["workload"]), int(row["commit_order"])), []).append(row)
    workloads = list(dict.fromkeys(spec.workload for spec in slices))
    output: list[dict[str, object]] = []
    baseline: dict[str, float] = {}
    baseline_complete: dict[str, bool] = {}
    for workload in workloads:
        for run in runs:
            series = grouped.get((workload, int(run["commit_order"])), [])
            weight_sum = math.fsum(weights[str(row["slice"])] for row in series)
            weighted_cpi = (
                math.fsum(
                    weights[str(row["slice"])] / float(row["ipc"]) for row in series
                )
                / weight_sum
                if weight_sum
                else None
            )
            ipc = 1.0 / weighted_cpi if weighted_cpi else None
            if int(run["commit_order"]) == 1 and ipc is not None:
                baseline[workload] = ipc
                baseline_complete[workload] = len(series) == len(expected_names[workload])
            current_complete = len(series) == len(expected_names[workload])
            output.append(
                {
                    "workload": workload,
                    "commit_order": run["commit_order"],
                    "short_commit": run["short_commit"],
                    "commit_time": run["commit_time"],
                    "slice_count": len(series),
                    "expected_slice_count": len(expected_names[workload]),
                    "weight_sum": weight_sum,
                    "expected_weight_sum": expected_weights[workload],
                    "weight_coverage_pct": weight_sum / expected_weights[workload] * 100.0,
                    "status": "complete" if current_complete else "partial",
                    "comparison_status": (
                        "complete"
                        if current_complete and baseline_complete.get(workload, False)
                        else "partial"
                    ),
                    "weighted_ipc": ipc,
                    "change_from_first_pct": (
                        (ipc / baseline[workload] - 1.0) * 100.0
                        if ipc is not None and workload in baseline
                        else None
                    ),
                }
            )
    return output


def transition_summaries(
    rows: list[dict[str, object]], runs: list[dict[str, object]]
) -> list[dict[str, object]]:
    grouped = rows_by_slice(rows)
    changes: dict[int, list[float]] = {index: [] for index in range(2, len(runs) + 1)}
    for series in grouped.values():
        for row in series:
            order = int(row["commit_order"])
            change = row["change_from_previous_pct"]
            if order > 1 and change is not None:
                changes[order].append(float(change))
    output: list[dict[str, object]] = []
    for order in range(2, len(runs) + 1):
        values = changes[order]
        output.append(
            {
                "from_commit": runs[order - 2]["short_commit"],
                "to_commit": runs[order - 1]["short_commit"],
                "comparable_slice_count": len(values),
                "median_change_pct": statistics.median(values) if values else None,
                "mean_change_pct": statistics.fmean(values) if values else None,
                "improved_over_0_5pct": sum(value > 0.5 for value in values),
                "stable_within_0_5pct": sum(abs(value) <= 0.5 for value in values),
                "regressed_over_0_5pct": sum(value < -0.5 for value in values),
                "minimum_change_pct": min(values) if values else None,
                "maximum_change_pct": max(values) if values else None,
            }
        )
    return output


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    if not rows:
        return
    names = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_wide_csv(
    path: Path,
    slices: list[SliceSpec],
    rows: list[dict[str, object]],
    runs: list[dict[str, object]],
) -> None:
    grouped = rows_by_slice(rows)
    commit_names = [str(run["short_commit"]) for run in runs]
    fields = ["slice_order", "slice", "workload", "checkpoint", "weight"]
    for index, commit in enumerate(commit_names):
        fields.append(f"ipc_{commit}")
        if index:
            fields.append(f"delta_pct_{commit_names[index - 1]}_to_{commit}")
    fields.append("first_to_last_pct")
    output: list[dict[str, object]] = []
    for spec in slices:
        by_commit = {str(row["short_commit"]): row for row in grouped.get(spec.name, [])}
        item: dict[str, object] = {
            "slice_order": spec.order,
            "slice": spec.name,
            "workload": spec.workload,
            "checkpoint": spec.checkpoint,
            "weight": spec.weight,
        }
        values: list[float | None] = []
        for index, commit in enumerate(commit_names):
            row = by_commit.get(commit)
            value = float(row["ipc"]) if row else None
            values.append(value)
            item[f"ipc_{commit}"] = value
            if index:
                previous = values[index - 1]
                item[f"delta_pct_{commit_names[index - 1]}_to_{commit}"] = (
                    (value / previous - 1.0) * 100.0
                    if value is not None and previous is not None
                    else None
                )
        item["first_to_last_pct"] = (
            (values[-1] / values[0] - 1.0) * 100.0
            if values and values[0] is not None and values[-1] is not None
            else None
        )
        output.append(item)
    write_csv(path, output, fields)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
        if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in paths:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def heat_color(change: float, limit: float) -> tuple[int, int, int]:
    strength = min(abs(change) / limit, 1.0)
    target = BLUE if change >= 0 else RED
    return tuple(round(255 + (channel - 255) * strength) for channel in target)


def render_heatmap(
    path: Path,
    trends: list[dict[str, object]],
    runs: list[dict[str, object]],
) -> None:
    workloads = list(dict.fromkeys(str(row["workload"]) for row in trends))
    values = {
        (str(row["workload"]), str(row["short_commit"])): float(row["change_from_first_pct"])
        for row in trends
        if row["change_from_first_pct"] is not None
    }
    nonzero = [abs(value) for value in values.values() if value]
    scale = sorted(nonzero)[max(0, math.ceil(len(nonzero) * 0.95) - 1)] if nonzero else 1.0
    scale = max(scale, 0.1)
    width = 1780
    left, top, cell_w, row_h = 380, 210, 184, 29
    height = top + len(workloads) * row_h + 120
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    draw.text((45, 28), "SPEC06 workload 加权 IPC 趋势", fill=INK, font=font(34, True))
    draw.text(
        (47, 78),
        f"基于清单内 1094 个切片；相对首个提交的变化；色阶在 ±{scale:.2f}% 饱和",
        fill=MUTED,
        font=font(18),
    )
    draw.text(
        (47, 112),
        "蓝色为提升，红色为下降；聚合公式为 Σweight / Σ(weight × CPI)",
        fill=MUTED,
        font=font(16),
    )
    for column, run in enumerate(runs):
        x = left + column * cell_w
        draw.text((x + cell_w / 2, 158), str(run["short_commit"]), fill=INK, font=font(15, True), anchor="mm")
        draw.text((x + cell_w / 2, 184), str(run["commit_time"])[5:10], fill=MUTED, font=font(13), anchor="mm")
    for row_index, workload in enumerate(workloads):
        y = top + row_index * row_h
        if row_index % 2 == 0:
            draw.rectangle((35, y, width - 35, y + row_h), fill=WHITE)
        draw.text((left - 15, y + row_h / 2), workload, fill=INK, font=font(14), anchor="rm")
        for column, run in enumerate(runs):
            change = values.get((workload, str(run["short_commit"])))
            x = left + column * cell_w
            fill = (240, 243, 247) if change is None else heat_color(change, scale)
            draw.rectangle((x + 2, y + 2, x + cell_w - 2, y + row_h - 2), fill=fill)
            trend = next(
                (
                    row
                    for row in trends
                    if row["workload"] == workload
                    and row["short_commit"] == run["short_commit"]
                ),
                None,
            )
            partial = trend is not None and trend["comparison_status"] != "complete"
            label = "NA" if change is None else f"{change:+.2f}%{'*' if partial else ''}"
            draw.text((x + cell_w / 2, y + row_h / 2), label, fill=INK, font=font(13), anchor="mm")
    draw.line((35, height - 54, width - 35, height - 54), fill=GRID, width=1)
    draw.text(
        (45, height - 42),
        "* 表示当前点或比较基线有缺测；详细到每个切片的 IPC 和相邻变化见 slice-ipc-wide.csv",
        fill=MUTED,
        font=font(14),
    )
    image.save(path)


def fmt_pct(value: object) -> str:
    return f"{float(value):+.3f}%"


def write_report(
    path: Path,
    slices: list[SliceSpec],
    rows: list[dict[str, object]],
    summaries: list[dict[str, object]],
    trends: list[dict[str, object]],
    transitions: list[dict[str, object]],
    runs: list[dict[str, object]],
    errors: list[dict[str, str]],
) -> None:
    complete = [row for row in summaries if row["status"] == "complete"]
    ranked = sorted(complete, key=lambda row: float(row["first_to_last_pct"]))
    step_ranked = sorted(complete, key=lambda row: abs(float(row["largest_step_pct"])), reverse=True)
    workload_last = [
        row
        for row in trends
        if int(row["commit_order"]) == len(runs)
        and row["change_from_first_pct"] is not None
        and row["comparison_status"] == "complete"
    ]
    workload_ranked = sorted(workload_last, key=lambda row: float(row["change_from_first_pct"]))
    lines = [
        "# SPEC06 切片 IPC 变化趋势",
        "",
        f"本次按 checkpoint 清单采集 **{len(slices)} 个切片 × {len(runs)} 个 demo commit**，"
        f"得到 **{len(rows)} 条有效 IPC 观测**；完整切片 {len(complete)} 个，缺测 {len(errors)} 条。",
        "",
        "## Commit 顺序",
        "",
        "| 顺序 | commit | 时间 | subject |",
        "| ---: | --- | --- | --- |",
    ]
    for run in runs:
        lines.append(
            f"| {run['commit_order']} | `{run['short_commit']}` | {run['commit_time']} | {str(run['subject']).replace('|', '\\|')} |"
        )
    lines.extend(
        [
            "",
            "## Commit 区间总览",
            "",
            "按切片统计相邻 commit 的 IPC 变化；`稳定`表示变化在 ±0.5% 内。均值易受少数大幅变化切片影响，"
            "判断整体位置更适合看中位数。",
            "",
            "| 区间 | 可比切片 | 提升 >0.5% | 稳定 | 下降 >0.5% | 中位数 | 均值 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in transitions:
        lines.append(
            f"| `{row['from_commit']}` → `{row['to_commit']}` | {row['comparable_slice_count']} | "
            f"{row['improved_over_0_5pct']} | {row['stable_within_0_5pct']} | "
            f"{row['regressed_over_0_5pct']} | {fmt_pct(row['median_change_pct'])} | "
            f"{fmt_pct(row['mean_change_pct'])} |"
        )
    lines.extend(
        [
            "",
            "## 首尾变化最大的切片",
            "",
            "正值表示 IPC 提升，负值表示下降。这里分别列出首尾变化最大的 10 个切片。",
            "",
            "| 方向 | slice | workload | weight | 首个 IPC | 末个 IPC | 首尾变化 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for direction, selected in (("下降", ranked[:10]), ("提升", list(reversed(ranked[-10:])))):
        for row in selected:
            lines.append(
                f"| {direction} | `{row['slice']}` | {row['workload']} | {float(row['weight']):.6g} | "
                f"{float(row['first_ipc']):.6f} | {float(row['last_ipc']):.6f} | {fmt_pct(row['first_to_last_pct'])} |"
            )
    lines.extend(
        [
            "",
            "## 最大相邻跳变",
            "",
            "| slice | 跳变区间 | 变化 | 首尾变化 |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for row in step_ranked[:15]:
        lines.append(
            f"| `{row['slice']}` | `{row['largest_step_from']}` → `{row['largest_step_to']}` | "
            f"{fmt_pct(row['largest_step_pct'])} | {fmt_pct(row['first_to_last_pct'])} |"
        )
    lines.extend(
        [
            "",
            "## Workload 加权趋势",
            "",
            "各 workload 用目录中的 SimPoint weight 做 CPI 加权，再换算为 IPC。以下为首尾两点数据均完整的末个相对首个提交变化。",
            "",
            "| 排名 | workload | 首尾变化 | 末点覆盖 |",
            "| ---: | --- | ---: | ---: |",
        ]
    )
    for index, row in enumerate(reversed(workload_ranked), 1):
        lines.append(
            f"| {index} | {row['workload']} | {fmt_pct(row['change_from_first_pct'])} | "
            f"{float(row['weight_coverage_pct']):.3f}% |"
        )
    partial_points = [row for row in trends if row["comparison_status"] != "complete"]
    if partial_points:
        lines.extend(
            [
                "",
                "`omnetpp` 末点和 `sjeng` 首点不完整，未纳入上述完整首尾排名。热力图中 `*` 表示"
                "当前点或比较基线部分覆盖；数值仍按已有切片权重归一化计算，分析时应结合缺测清单。",
                "",
                "## 缺测清单",
                "",
                "| slice | commit | 原因 |",
                "| --- | --- | --- |",
            ]
        )
        for error in errors:
            reason = "缺少 simulator_out.txt" if "No such file" in error["error"] else error["error"]
            lines.append(f"| `{error['slice']}` | `{error['short_commit']}` | {reason} |")
    lines.extend(
        [
            "",
            "## 产物说明",
            "",
            "- `slice-ipc-line-chart.html`：可按 workload 筛选的交互折线图；横轴 commit、纵轴 IPC，每条线代表一个切片。",
            "- `slice-ipc-line-overview.png`：全部 1094 个切片的静态折线总览，红线为 IPC 中位数。",
            "- `slice-ipc-long.csv`：每个切片、每个 commit 一行，含 instructions、cycles、IPC、相邻变化和原始文件路径。",
            "- `slice-ipc-wide.csv`：每个切片一行，横向查看 7 个 IPC、6 段相邻变化和首尾变化。",
            "- `slice-summary.csv`：每个切片的首尾变化、波动区间、最大相邻跳变及对应 commit。",
            "- `workload-weighted-trend.csv`：按 SimPoint weight 聚合后的 workload IPC 趋势。",
            "- `commit-transition-summary.csv`：每段相邻 commit 中提升、稳定、下降切片数及变化均值/中位数。",
            "- `workload-ipc-heatmap.png`：workload 相对首个 commit 的加权 IPC 热力图。",
            "- `manifest.json`：输入目录、commit 元数据、覆盖率和错误记录。",
            "",
            "IPC 取自每个 `simulator_out.txt` 中最后一条 ROI 汇总。趋势按 Git committer time 排序；"
            "这些测试点未必组成严格的祖先链，因此变化可用于定位回归或提升，不能单凭相邻折线归因到某一个 commit。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
