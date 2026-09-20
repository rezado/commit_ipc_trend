from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.plot_mcf_commit_ipc_demo import build_dataset, valid_mcf_slices


class McfCommitIpcDemoTest(unittest.TestCase):
    def test_valid_mcf_slices_uses_last_summary_and_skips_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            good = run / "mcf_10_0.25"
            good.mkdir()
            (good / "simulator_out.txt").write_text(
                "Core-0 instrCnt = 10, cycleCnt = 20, IPC = 0.5\n"
                "Core-0 instrCnt = 40,000,000, cycleCnt = 50,000,000, IPC = 0.8\n",
                encoding="utf-8",
            )
            failed = run / "mcf_20_0.75"
            failed.mkdir()
            (failed / "simulator_out.txt").write_text("assertion failed\n", encoding="utf-8")
            self.assertEqual(
                valid_mcf_slices(run),
                {"mcf_10_0.25": ("40,000,000", "50,000,000", "0.8")},
            )

    @mock.patch("tools.plot_mcf_commit_ipc_demo.git_metadata")
    def test_build_dataset_intersects_slices_sorts_commits_and_weights(self, metadata):
        metadata.side_effect = lambda sha, _repos: {
            "commit": sha * 4,
            "commit_epoch": {"aaaaaaaaa": 2, "bbbbbbbbb": 1}[sha],
            "commit_time": "2026-09-01T00:00:00+08:00",
            "subject": sha,
            "git_repo": "/repo",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = (
                "cr260901-aaaaaaaaa-DefaultConfig",
                "cr260901-bbbbbbbbb-DefaultConfig",
            )
            for directory in directories:
                for name, ipc in (
                    ("mcf_10_0.2", "0.8"),
                    ("mcf_20_0.7", "1.2"),
                ):
                    target = root / directory / name
                    target.mkdir(parents=True)
                    (target / "simulator_out.txt").write_text(
                        f"Core-0 instrCnt = 40,000,000, cycleCnt = 50,000,000, IPC = {ipc}\n",
                        encoding="utf-8",
                    )
            extra = root / directories[0] / "mcf_30_0.9"
            extra.mkdir()
            (extra / "simulator_out.txt").write_text(
                "Core-0 instrCnt = 40, cycleCnt = 50, IPC = 0.8\n", encoding="utf-8"
            )

            rows, runs, manifest = build_dataset(root, directories, 2, (Path("/repo"),))

            self.assertEqual([run["short_commit"] for run in runs], ["bbbbbbbbb", "aaaaaaaaa"])
            selection = manifest["selection"]
            self.assertEqual(selection["selected_slices"], ["mcf_20_0.7", "mcf_10_0.2"])
            self.assertAlmostEqual(selection["selected_weight_sum"], 0.9)
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["short_commit"], "bbbbbbbbb")


if __name__ == "__main__":
    unittest.main()
