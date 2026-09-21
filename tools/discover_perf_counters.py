#!/usr/bin/env python3
"""Inventory PERF counters from the final complete dump of slice runs."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
import re


PERF_RE = re.compile(
    r"^\[PERF \]\[time=(?P<time>\d+)\] (?P<path>.*): "
    r"(?P<name>[^,]+), (?P<value>[+-]?\d+(?:\.\d+)?)$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path, help="run or slice directories")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--glob", default="*_*_*/simulator_err.txt")
    return parser.parse_args()


def find_logs(roots: list[Path], pattern: str) -> list[Path]:
    result: set[Path] = set()
    for root in roots:
        if root.is_file():
            result.add(root.resolve())
        else:
            direct = root / "simulator_err.txt"
            if direct.is_file():
                result.add(direct.resolve())
            result.update(path.resolve() for path in root.glob(pattern) if path.is_file())
    return sorted(result)


def final_complete_dump(path: Path) -> tuple[int | None, Counter[tuple[str, str]], str]:
    dumps: list[tuple[int, Counter[tuple[str, str]], tuple[str, str], tuple[str, str]]] = []
    current_time: int | None = None
    current: Counter[tuple[str, str]] = Counter()
    first: tuple[str, str] | None = None
    last: tuple[str, str] | None = None
    with path.open(encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            match = PERF_RE.fullmatch(raw_line.rstrip("\n"))
            if not match:
                continue
            dump_time = int(match.group("time"))
            key = (match.group("path"), match.group("name"))
            if current_time is not None and dump_time != current_time:
                assert first is not None and last is not None
                dumps.append((current_time, current, first, last))
                current = Counter()
                first = None
            current_time = dump_time
            first = first or key
            last = key
            current[key] += 1
    if current_time is not None:
        assert first is not None and last is not None
        dumps.append((current_time, current, first, last))
    if not dumps:
        return None, Counter(), "no_perf_dump"
    final = dumps[-1]
    if len(dumps) == 1:
        return final[0], final[1], "single_dump_unverified"
    reference = max(dumps[:-1], key=lambda item: sum(item[1].values()))
    complete = (
        sum(final[1].values()) == sum(reference[1].values())
        and final[2] == reference[2]
        and final[3] == reference[3]
    )
    return final[0], final[1], "complete" if complete else "truncated_final_dump"


def main() -> int:
    args = parse_args()
    logs = find_logs(args.roots, args.glob)
    if not logs:
        raise SystemExit("no simulator_err.txt files found")
    presence: Counter[tuple[str, str]] = Counter()
    occurrences: dict[tuple[str, str], list[int]] = defaultdict(list)
    statuses: Counter[str] = Counter()
    for index, path in enumerate(logs, 1):
        _time, keys, status = final_complete_dump(path)
        statuses[status] += 1
        if status != "complete":
            continue
        for key, count in keys.items():
            presence[key] += 1
            occurrences[key].append(count)
        print(f"[{index}/{len(logs)}] {path.parent.name}: {status}", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["source_path", "source_name", "files_present", "complete_files", "coverage_pct",
             "min_occurrences_per_dump", "max_occurrences_per_dump", "unique_per_dump"]
        )
        complete_files = statuses["complete"]
        for path, name in sorted(presence, key=lambda key: (key[0], key[1])):
            values = occurrences[(path, name)]
            writer.writerow(
                [path, name, presence[(path, name)], complete_files,
                 f"{presence[(path, name)] / complete_files * 100:.3f}" if complete_files else "0.000",
                 min(values), max(values), min(values) == max(values) == 1]
            )
    print(f"logs={len(logs)} statuses={dict(statuses)} counters={len(presence)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
