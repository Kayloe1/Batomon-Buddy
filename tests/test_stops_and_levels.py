"""Any crash is a clean stop; batch.py tells scripts whether it needed help; no level 4 is ever created."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import batch  # noqa: E402
import hands  # noqa: E402
import play  # noqa: E402
from engine import data, events, model, opponents  # noqa: E402
from engine.evaluate import Evaluator  # noqa: E402
from engine.model import RunState, new_unit  # noqa: E402
from engine.planner import Params, Planner  # noqa: E402


class FailSafeException(Exception):
    """Stands in for pyautogui's emergency stop (play.py recognises it by its name)."""


class Image:
    def save(self, path):
        Path(path).write_bytes(b"png")


class Broken:
    """Hands whose first look fails with the given error."""
    dry_run = True

    def __init__(self, error):
        self.error, self.last_image = error, Image()

    def look(self, save_if_unreadable=True):
        raise self.error


class Control(play.Control):
    def __init__(self):
        self.events = []

    def event(self, kind, **data_):
        self.events.append((kind, data_))


class CrashesAreStops(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs, self.caps = Path(self.tmp.name) / "logs", Path(self.tmp.name) / "captures"
        self.logs.mkdir()
        for p in (mock.patch.object(hands, "LOG_DIR", self.logs), mock.patch.object(hands, "CAPTURE_DIR", self.caps),
                  mock.patch("winsound.Beep"), mock.patch("sys.stdout", io.StringIO())):
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, error):
        control = Control()
        code = play.main(["--simple"], control=control, hands=Broken(error))
        return code, control

    def test_an_unexpected_error_becomes_a_stop_with_a_note_and_a_picture(self):
        code, control = self.run_with(RuntimeError("The window 'Notes' covers the game. Nothing was clicked."))
        self.assertEqual(code, 1)
        kind, info = [e for e in control.events if e[0] == "needs_you"][-1]
        self.assertIn("covers the game", info["why"])
        self.assertTrue(info["capture"] and (self.caps / info["capture"]).exists())
        self.assertIn("covers the game", (self.logs / "last_stop.txt").read_text(encoding="utf-8"))
        stop = [json.loads(l) for l in play._CURRENT_LOG.read_text(encoding="utf-8").splitlines()][-1]
        self.assertEqual(stop["kind"], "stop")
        self.assertIn("RuntimeError", stop["traceback"])

    def test_the_mouse_corner_emergency_stop_is_a_stop_too(self):
        code, control = self.run_with(FailSafeException("corner"))
        self.assertEqual(code, 1)
        why = [e for e in control.events if e[0] == "needs_you"][-1][1]["why"]
        self.assertTrue(why.startswith("Emergency stop"))

    def test_a_missing_game_window_says_to_open_the_game(self):
        with mock.patch.object(hands, "Hands", side_effect=RuntimeError("Batomon Showdown window not found")):
            control = Control()
            self.assertEqual(play.main(["--simple"], control=control), 1)
        self.assertIn("Open the game first", [e for e in control.events if e[0] == "needs_you"][-1][1]["why"])


class BatchExitCodes(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(play, "LAST_LOG", None), mock.patch("sys.stdout", io.StringIO())):
            p.start()
            self.addCleanup(p.stop)

    def test_zero_when_every_run_finished_one_when_it_needed_help(self):
        with mock.patch.object(batch.play, "main", return_value=0) as m:
            self.assertEqual(batch.main(["--runs", "2"]), 0)
            self.assertEqual(m.call_count, 2)
        with mock.patch.object(batch.play, "main", return_value=1) as m:
            self.assertEqual(batch.main(["--runs", "3"]), 1)
            self.assertEqual(m.call_count, 1)               # it stops at the first run that needs help

    def test_options_are_passed_through_and_new_run_always_is(self):
        with mock.patch.object(batch.play, "main", return_value=0) as m:
            batch.main(["--runs", "1", "--learned"])
        self.assertEqual(m.call_args[0][0], ["--new-run", "--learned"])


class NeverCreateAnUnreadableLevel(unittest.TestCase):
    def planner(self):
        return Planner(Evaluator(opponents.pools(n=4, seed=1, recorded=False)),
                       Params(k_final=3, k_quick=2, reroll_samples=1, greedy_steps=1, final_rounds=1))

    def test_the_stopgap_is_level_3(self):
        self.assertEqual(data.MAX_READABLE_LEVEL, 3)

    def test_a_level_3_monster_is_never_levelled_up(self):
        self.assertIsNone(events._level_up(new_unit("Panbud", 30, level=3)))
        self.assertIsNotNone(events._level_up(new_unit("Panbud", 30, level=2)))

    def test_ultra_candy_is_not_used(self):
        card = model.Card("Ultra Candy", 60, "item")
        self.assertFalse(model.item_is_worth_using(RunState(), card))

    def test_the_event_target_avoids_level_3_when_it_can(self):
        s = RunState(day=6, money=0)
        s.team[1] = new_unit("Panbud", 30, level=3)
        s.team[2] = new_unit("Mosslug", 10, level=1)
        unit, _ = events.best_target(self.planner(), s, "Level up a monster (max level 4)")
        self.assertLess(unit.level, 3)

    def test_with_only_level_3_monsters_it_still_answers_and_says_why(self):
        s = RunState(day=6, money=0)
        s.team[1] = new_unit("Panbud", 30, level=3)
        unit, why = events.best_target(self.planner(), s, "Level up a monster (max level 4)")
        self.assertIsNotNone(unit)
        self.assertIn("level-4", why)


if __name__ == "__main__":
    unittest.main()
