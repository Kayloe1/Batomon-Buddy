"""Failures must be visible or harmless: damaged log lines, warnings, a launch crash."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gui  # noqa: E402
import play  # noqa: E402
from engine import opponents  # noqa: E402
from sim import from_logs  # noqa: E402


class DamagedLines(unittest.TestCase):
    def test_a_half_written_recorded_opponent_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rec.jsonl"
            path.write_text(json.dumps({"day": 3, "slots": [None] * 6}) + "\n" + '{"day": 4, "sl', encoding="utf-8")
            with mock.patch.object(opponents, "RECORDED", path):
                rec = opponents.load_recorded()
        self.assertEqual(list(rec), [3])

    def test_a_half_written_run_log_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.jsonl"
            path.write_text(json.dumps({"kind": "trainer", "ok": True}) + "\n" + '{"kind": "bat', encoding="utf-8")
            self.assertEqual(from_logs.battles_from_log(path), [])


class Warnings(unittest.TestCase):
    def test_a_warning_goes_to_the_sink_when_there_is_one(self):
        seen = []
        with mock.patch.object(play, "_warn_sink", seen.append), mock.patch("builtins.print") as pr:
            play.warn("card sprites not saved: boom")
        self.assertEqual(seen, ["card sprites not saved: boom"])
        pr.assert_not_called()

    def test_without_a_sink_it_is_printed(self):
        with mock.patch.object(play, "_warn_sink", None), mock.patch("builtins.print") as pr:
            play.warn("hello")
        pr.assert_called_once_with("hello")


class LaunchCrash(unittest.TestCase):
    def test_a_startup_error_is_written_down_and_shown(self):
        with mock.patch("pathlib.Path.write_text") as wt, mock.patch("ctypes.windll.user32.MessageBoxW") as box:
            try:
                raise ValueError("names.json is damaged")
            except ValueError:
                with self.assertRaises(SystemExit):
                    gui._startup_failed()
        self.assertIn("names.json is damaged", wt.call_args[0][0])
        self.assertIn("names.json is damaged", box.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
