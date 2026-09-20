#!/usr/bin/env python3
"""Build a reproducible mcf slice IPC trend demo from CI perf reports."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("/nfs/home/cirunner/perf-report")
DEFAULT_OUTPUT_DIR = ROOT / "outputs/mcf-commit-ipc-demo"
DEFAULT_COMMITS = (
    "cr260901-a355c5525-DefaultConfig",
    "cr260904-017a698e0-DefaultConfig",
    "cr260907-df157ddd3-DefaultConfig",
    "cr260910-50b9c3387-DefaultConfig",
    "cr260913-151e60593-DefaultConfig",
    "cr260916-b625e1616-DefaultConfig",
    "cr260918-575aef181-DefaultConfig",
)
DEFAULT_SLICE_COUNT = 5
SUMMARY_RE = re.compile(
    r"instrCnt\s*=\s*([0-9,]+),\s*cycleCnt\s*=\s*([0-9,]+),\s*IPC\s*=\s*([0-9.]+)"
)
DIR_RE = re.compile(r"^cr\d{6}-([0-9a-f]{9})-([A-Za-z0-9]+Config)$")
SLICE_RE = re.compile(r"^mcf_(\d+)_([0-9.eE+-]+)$")

BG = (247, 249, 252)
WHITE = (255, 255, 255)
INK = (34, 43, 56)
MUTED = (96, 110, 130)
GRID = (210, 219, 229)
AXIS = (107, 120, 137)
COLORS = (
    (7, 124, 181),
    (230, 159, 0),
    (0, 158, 119),
    (204, 80, 62),
    (126, 88, 163),
)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
        if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--commit-dir",
        action="append",
        dest="commit_dirs",
        help="result directory name; repeat to override the seven demo commits",
    )
    parser.add_argument(
        "--slice-count",
        type=int,
        default=DEFAULT_SLICE_COUNT,
        help="select this many highest-weight mcf slices in the strict intersection",
    )
    parser.add_argument(
        "--git-repo",
        type=Path,
        help="XiangShan Git repository used to resolve commit time and subject",
    )
    return parser.parse_args()


def candidate_git_repos(explicit: Path | None) -> Iterable[Path]:
    if explicit is not None:
        yield explicit
        return
    preferred = Path(
        "/nfs/home/cirunner/ci-runner-xs/xs-perf-node030-0/_work/XiangShan/XiangShan"
    )
    if (preferred / ".git").exists():
        yield preferred
    runner_root = Path("/nfs/home/cirunner/ci-runner-xs")
    for repo in sorted(runner_root.glob("xs-perf-node*/_work/XiangShan/XiangShan")):
        if repo != preferred and (repo / ".git").exists():
            yield repo
    local = Path("/nfs/home/wujiabin/work/XiangShan")
    if (local / ".git").exists():
        yield local


def git_metadata(short_sha: str, repos: tuple[Path, ...]) -> dict[str, str | int]:
    for repo in repos:
        command = (
            "git",
            "-c",
            f"safe.directory={repo}",
            "-C",
            str(repo),
            "show",
            "-s",
            "--format=%H%x00%ct%x00%cI%x00%s",
            short_sha,
        )
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            continue
        parts = result.stdout.rstrip("\n").split("\0", 3)
        if len(parts) == 4:
            return {
                "commit": parts[0],
                "commit_epoch": int(parts[1]),
                "commit_time": parts[2],
                "subject": parts[3],
                "git_repo": str(repo),
            }
    raise RuntimeError(f"cannot resolve Git metadata for {short_sha}")


def valid_mcf_slices(run_dir: Path) -> dict[str, tuple[str, str, str]]:
    result: dict[str, tuple[str, str, str]] = {}
    for slice_dir in sorted(run_dir.glob("mcf_*")):
        match = SLICE_RE.fullmatch(slice_dir.name)
        output = slice_dir / "simulator_out.txt"
        if not match or not output.is_file():
            continue
        text = output.read_text(encoding="utf-8", errors="replace")
        summaries = SUMMARY_RE.findall(text)
        if summaries:
            result[slice_dir.name] = summaries[-1]
    return result


def build_dataset(
    report_root: Path,
    commit_dirs: tuple[str, ...],
    slice_count: int,
    repos: tuple[Path, ...],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    if slice_count < 1:
        raise ValueError("--slice-count must be positive")
    runs: list[dict[str, object]] = []
    slice_maps: dict[str, dict[str, tuple[str, str, str]]] = {}
    configs: set[str] = set()

    for directory in commit_dirs:
        match = DIR_RE.fullmatch(directory)
        if not match:
            raise ValueError(f"unexpected report directory name: {directory}")
        short_sha, config = match.groups()
        run_path = report_root / directory
        if not run_path.is_dir():
            raise FileNotFoundError(run_path)
        slices = valid_mcf_slices(run_path)
        if not slices:
            raise RuntimeError(f"no valid mcf IPC results in {run_path}")
        metadata = git_metadata(short_sha, repos)
        metadata.update(
            {
                "directory": directory,
                "short_commit": short_sha,
                "config": config,
                "valid_mcf_slice_count": len(slices),
            }
        )
        runs.append(metadata)
        slice_maps[directory] = slices
        configs.add(config)

    if len(configs) != 1:
        raise RuntimeError(f"mixed configurations are not comparable: {sorted(configs)}")
    runs.sort(key=lambda row: (int(row["commit_epoch"]), str(row["short_commit"])))
    shared = set.intersection(*(set(slice_maps[str(run["directory"])]) for run in runs))
    if len(shared) < slice_count:
        raise RuntimeError(
            f"only {len(shared)} valid mcf slices are shared; requested {slice_count}"
        )
    selected = sorted(
        shared,
        key=lambda name: (-float(SLICE_RE.fullmatch(name).group(2)), name),  # type: ignore[union-attr]
    )[:slice_count]

    rows: list[dict[str, object]] = []
    for order, run in enumerate(runs, start=1):
        directory = str(run["directory"])
        for slice_name in selected:
            instructions_raw, cycles_raw, ipc_raw = slice_maps[directory][slice_name]
            slice_match = SLICE_RE.fullmatch(slice_name)
            assert slice_match is not None
            rows.append(
                {
                    "commit_order": order,
                    "commit": run["commit"],
                    "short_commit": run["short_commit"],
                    "commit_time": run["commit_time"],
                    "subject": run["subject"],
                    "directory": directory,
                    "config": run["config"],
                    "workload": "mcf",
                    "slice": slice_name,
                    "checkpoint": int(slice_match.group(1)),
                    "weight": float(slice_match.group(2)),
                    "instructions": int(instructions_raw.replace(",", "")),
                    "cycles": int(cycles_raw.replace(",", "")),
                    "ipc": float(ipc_raw),
                    "source": str(report_root / directory / slice_name / "simulator_out.txt"),
                }
            )

    manifest: dict[str, object] = {
        "report_root": str(report_root),
        "selection": {
            "directory_pattern": "cr2609*-DefaultConfig",
            "comparison_config": next(iter(configs)),
            "commit_directories": [run["directory"] for run in runs],
            "commit_order": "Git committer timestamp (%ct), then short SHA",
            "slice_rule": "highest-weight valid slices in the strict mcf intersection",
            "shared_valid_mcf_slice_count": len(shared),
            "selected_slices": selected,
            "selected_weight_sum": sum(
                float(SLICE_RE.fullmatch(name).group(2)) for name in selected  # type: ignore[union-attr]
            ),
        },
        "runs": runs,
    }
    return rows, runs, manifest


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = (
        "commit_order",
        "commit",
        "short_commit",
        "commit_time",
        "subject",
        "directory",
        "config",
        "workload",
        "slice",
        "checkpoint",
        "weight",
        "instructions",
        "cycles",
        "ipc",
        "source",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_chart(
    path: Path,
    rows: list[dict[str, object]],
    runs: list[dict[str, object]],
    selected_slices: list[str],
    selected_weight_sum: float,
) -> None:
    commit_count = len(runs)
    slice_count = len(selected_slices)
    width, height = 1900, 1320
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    draw.text((58, 34), "mcf 切片 IPC 随提交变化", fill=INK, font=font(38, True))
    draw.text(
        (60, 88),
        f"{commit_count} 个 DefaultConfig 提交 · 共同切片中权重最高的 {slice_count} 个（合计权重 {selected_weight_sum:.1%}）",
        fill=MUTED,
        font=font(20),
    )
    draw.text(
        (60, 121),
        "横轴严格按 Git committer time 排序；每条线代表同一个 SimPoint 切片",
        fill=MUTED,
        font=font(17),
    )
    draw.line((58, 158, width - 58, 158), fill=GRID, width=2)

    by_slice: dict[str, list[dict[str, object]]] = {name: [] for name in selected_slices}
    for row in rows:
        by_slice[str(row["slice"])].append(row)
    chart_left, chart_right = 205, width - 80
    chart_width = chart_right - chart_left
    panel_top, panel_height, panel_gap = 180, 144, 20
    x_positions = [
        chart_left + index * chart_width / max(len(runs) - 1, 1)
        for index in range(len(runs))
    ]
    for index, slice_name in enumerate(selected_slices):
        color = COLORS[index % len(COLORS)]
        series = sorted(by_slice[slice_name], key=lambda row: int(row["commit_order"]))
        block_y0 = panel_top + index * (panel_height + 45 + panel_gap)
        panel_y0 = block_y0 + 35
        panel_y1 = panel_y0 + panel_height
        draw.rounded_rectangle(
            (58, block_y0, width - 58, panel_y1 + 10),
            radius=11,
            fill=WHITE,
        )
        values = [float(row["ipc"]) for row in series]
        low, high = min(values), max(values)
        padding = max((high - low) * 0.22, max(abs(low), 1.0) * 0.0015)
        y_min, y_max = low - padding, high + padding
        middle = (y_min + y_max) / 2.0
        for tick in (y_min, middle, y_max):
            y = panel_y1 - (tick - y_min) / (y_max - y_min) * panel_height
            draw.line((chart_left, y, chart_right, y), fill=GRID, width=1)
            draw.text(
                (chart_left - 16, y),
                f"{tick:.3f}",
                fill=MUTED,
                font=font(13),
                anchor="rm",
            )
        for x in x_positions:
            draw.line((x, panel_y0, x, panel_y1), fill=(235, 239, 244), width=1)
        draw.line((chart_left, panel_y0, chart_left, panel_y1), fill=AXIS, width=1)
        match = SLICE_RE.fullmatch(slice_name)
        assert match is not None
        first, last = values[0], values[-1]
        delta = (last / first - 1.0) * 100.0
        draw.text(
            (78, block_y0 + 8),
            f"checkpoint {match.group(1)}",
            fill=color,
            font=font(17, True),
        )
        draw.text(
            (350, block_y0 + 9),
            f"weight {float(match.group(2)):.3f}",
            fill=MUTED,
            font=font(14),
        )
        draw.text(
            (width - 78, block_y0 + 9),
            f"首尾 {delta:+.2f}%",
            fill=color,
            font=font(15, True),
            anchor="ra",
        )
        points = [
            (
                x_positions[point_index],
                panel_y1
                - (float(row["ipc"]) - y_min) / (y_max - y_min) * panel_height,
            )
            for point_index, row in enumerate(series)
        ]
        draw.line(points, fill=color, width=5, joint="curve")
        for x, y in points:
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=WHITE, outline=color, width=4)

    last_panel_bottom = panel_top + (len(selected_slices) - 1) * (
        panel_height + 45 + panel_gap
    ) + 35 + panel_height
    label_y = last_panel_bottom + 31
    for x, run in zip(x_positions, runs):
        timestamp = datetime.fromisoformat(str(run["commit_time"]))
        draw.text(
            (x, label_y),
            timestamp.strftime("%m-%d %H:%M"),
            fill=MUTED,
            font=font(14),
            anchor="ma",
        )
        draw.text(
            (x, label_y + 28),
            str(run["short_commit"]),
            fill=INK,
            font=font(16, True),
            anchor="ma",
        )
    draw.text((30, 665), "IPC", fill=INK, font=font(19, True), anchor="mm")
    draw.line((58, height - 40, width - 58, height - 40), fill=GRID, width=1)
    draw.text(
        (60, height - 28),
        "Source: /nfs/home/cirunner/perf-report/<commit>/mcf_<checkpoint>_<weight>/simulator_out.txt",
        fill=MUTED,
        font=font(13),
    )
    image.save(path)


def main() -> int:
    args = parse_args()
    commits = tuple(args.commit_dirs or DEFAULT_COMMITS)
    repos = tuple(candidate_git_repos(args.git_repo))
    if not repos:
        raise RuntimeError("no XiangShan Git repository found; pass --git-repo")
    rows, runs, manifest = build_dataset(
        args.report_root.resolve(), commits, args.slice_count, repos
    )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "mcf-ipc-trend.csv", rows)
    (output_dir / "selection.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    selection = manifest["selection"]
    assert isinstance(selection, dict)
    render_chart(
        output_dir / "mcf-ipc-trend.png",
        rows,
        runs,
        list(selection["selected_slices"]),
        float(selection["selected_weight_sum"]),
    )
    for name in ("mcf-ipc-trend.png", "mcf-ipc-trend.csv", "selection.json"):
        print(output_dir / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
