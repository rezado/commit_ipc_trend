import argparse
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from commit_ipc_trend.rolling import analyze, extract_database, inspect_database


class RollingAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = self.root / 'simulator.db'

    def database(self, coordinates=(1000, 2000, 3000)):
        with sqlite3.connect(self.db) as connection:
            connection.execute('CREATE TABLE ipc_rolling_0(ID INTEGER PRIMARY KEY, XAXISPT INT, YAXISPT INT)')
            connection.executemany('INSERT INTO ipc_rolling_0 VALUES(?,?,?)',
                                   [(i, x, 2000) for i, x in enumerate(coordinates)])
            connection.execute('CREATE TABLE td_cycle_LoadMemStall_rolling_0 AS SELECT * FROM ipc_rolling_0')

    def test_missing_database_is_not_created(self):
        with self.assertRaises(FileNotFoundError):
            inspect_database(self.db)
        self.assertFalse(self.db.exists())

    def test_fixed_windows_and_unavailable_hart(self):
        self.database()
        report = inspect_database(self.db)
        self.assertEqual(report['ipc'], 2)
        self.assertTrue(report['fixed_cycle_windows_from_zero'])
        with self.assertRaisesRegex(ValueError, 'missing ipc_rolling_1'):
            inspect_database(self.db, 1)

    def test_variable_windows_are_not_reported_as_rate_correlation(self):
        self.database((1000, 2000, 3500))
        report = inspect_database(self.db)
        self.assertFalse(report['fixed_cycle_windows_from_zero'])
        self.assertIsNone(report['ipc'])

    def test_reset_axis_rejected(self):
        self.database((1000, 2000, 1000))
        with self.assertRaisesRegex(ValueError, 'not strictly increasing'):
            inspect_database(self.db)

    def test_extract_exact_member_and_protect_existing_output(self):
        self.database()
        archive = self.root / 'run.tar'
        with tarfile.open(archive, 'w') as stream:
            stream.add(self.db, arcname='run/slice/simulator.db')
            unrelated = tarfile.TarInfo('../unrelated')
            unrelated.size = 4
            stream.addfile(unrelated, io.BytesIO(b'junk'))
        output = self.root / 'extracted.db'
        extract_database(archive, 'run/slice/simulator.db', output)
        self.assertEqual(inspect_database(output)['ipc'], 2)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            extract_database(archive, 'run/slice/simulator.db', output)
        with self.assertRaises(ValueError):
            extract_database(archive, '../bad.db', self.root / 'bad.db')
        with self.assertRaises(subprocess.CalledProcessError):
            extract_database(archive, 'missing.db', self.root / 'missing.db')
        self.assertFalse((self.root / 'missing.db').exists())

    def test_tool_failure_is_recorded(self):
        self.database()
        script = self.root / 'scripts/rolling.py'
        script.parent.mkdir()
        script.write_text('# test tool\n')
        args = argparse.Namespace(db=self.db, hart=0, xiangshan=self.root,
                                  output_dir=self.root / 'out', python=sys.executable,
                                  perf_name=None, aggregate=1, prefetch_progress=False,
                                  timeout=10, source_run='test')
        with patch('commit_ipc_trend.rolling.subprocess.run', return_value=subprocess.CompletedProcess([], 2, '', 'tool failed')):
            self.assertEqual(analyze(args), 1)
        report = json.loads((args.output_dir / 'analysis.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['commands'][0]['returncode'], 2)
        self.assertEqual((args.output_dir / 'list.stderr.txt').read_text(), 'tool failed')

    def test_cli_runs_all_steps_and_records_artifacts(self):
        self.database()
        script = self.root / 'scripts/rolling.py'
        script.parent.mkdir()
        script.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "for flag in ('--csv', '--output'):\n"
            "    if flag in sys.argv:\n"
            "        Path(sys.argv[sys.argv.index(flag) + 1]).write_text('fixture artifact')\n"
        )
        output = self.root / 'analysis'
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / 'tools/analyze_rolling.py'),
            'run', '--db', str(self.db), '--xiangshan', str(self.root),
            '--output-dir', str(output), '--aggregate', '1',
        ]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((output / 'analysis.json').read_text())
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(
            [step['name'] for step in report['commands']],
            ['list', 'cycle-correlation', 'plot'],
        )
        self.assertTrue((output / 'rolling.png').is_file())
        self.assertTrue((output / 'correlation-cycle.csv').is_file())

        repeated = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn('choose a new output directory', repeated.stderr)


if __name__ == '__main__':
    unittest.main()
