"""report.py reads the run logs (the saved Chef run is the fixture)."""
import unittest
from pathlib import Path

import report

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "runs" / "2026-11-12-chef" / "run_20261112_090412.jsonl"


class Report(unittest.TestCase):
    def test_run_summary(self):
        s = report.summarize_run(report.load(LOG))
        self.assertEqual(s["trainer"], "Chef")
        self.assertTrue(s["ended"])
        self.assertEqual(s["battles"], 2)

    def test_a_won_run_counts_ten_badges(self):
        # 2026-11-14: the badge case on the last result box hides the HP bars; the end screen's count decides
        recs = [{"kind": "shop", "state": {"day": 14, "badges": 9, "lives": 1, "trainer": "Bug Catcher"}},
                {"kind": "battle"}, {"kind": "battle_stats", "end_hp_fraction": {"me": 0.5, "opponent": 0.3}},
                {"kind": "run_over", "badges": 10}]
        s = report.summarize_run(recs)
        self.assertEqual((s["badges"], s["final_battle"]), (10, "win"))
        recs[-1] = {"kind": "run_over", "badges": 9}
        self.assertEqual(report.summarize_run(recs)["final_battle"], "loss")

    def test_log_without_a_battle_is_skipped(self):
        self.assertIsNone(report.summarize_run([{"kind": "stop", "why": "x"}]))

    def test_unseen_list_and_odds(self):
        rep = report.report([LOG])
        self.assertIn("never_seen", rep)
        self.assertTrue(rep["shop_offers"])
        self.assertTrue(rep["income"])

    def test_mode_breakdown_and_stop_reasons(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p1 = d / "run_1.jsonl"
            p1.write_text("\n".join(json.dumps(r) for r in [
                {"kind": "new_run", "mode": "ranked", "ok": True},
                {"kind": "shop", "state": {"day": 1, "badges": 0, "lives": 5, "trainer": "Chef"}},
                {"kind": "battle"},
                {"kind": "stop", "why": "shop 1: cannot read '?' (capture: unread_1.png)"},
                {"kind": "run_over", "badges": 10},
            ]) + "\n", encoding="utf-8")
            p2 = d / "run_2.jsonl"
            p2.write_text("\n".join(json.dumps(r) for r in [
                {"kind": "new_run", "mode": "casual", "ok": True},
                {"kind": "shop", "state": {"day": 1, "badges": 0, "lives": 5, "trainer": "Witch"}},
                {"kind": "battle"},
                {"kind": "stop", "why": "shop 1: cannot read '?' (capture: unread_2.png)"},
                {"kind": "stop", "why": "stopped from the control window"},
            ]) + "\n", encoding="utf-8")
            rep = report.report([p1, p2])
        self.assertEqual(rep["by_mode"]["ranked"], {"runs": 1, "finished": 1, "won": 1, "mean_badges": 10.0})
        self.assertEqual(rep["by_mode"]["casual"], {"runs": 1, "finished": 0, "won": 0, "mean_badges": None})
        self.assertEqual(rep["stop_reasons"], {"shop 1: cannot read '?'": 2})


class Events(unittest.TestCase):
    def test_events_and_unknowns_are_listed(self):
        recs = [
            {"kind": "event", "choice": 2, "effects": ["Gain Ignit", None, "Gain $40"], "raw": ["Gain Ignit", "G?t a", "Gain $40"],
             "why": ["Event choices:", "   +0.05  Gain Ignit: 'Ignit' is not in my data yet [guess]", "   +0.12  Gain $40: money"]},
            {"kind": "event_outcome", "effects": ["Gain Ignit", None, "Gain $40"], "picked": "Gain $40", "day": 4,
             "money_before": 5, "money_after": 95, "lives_before": 8, "lives_after": 8,
             "monsters_new_or_changed": [], "monsters_gone_or_changed": []},
            {"kind": "event", "choice": 0, "effects": ["Gain Moon Cheese"], "raw": ["Gain Moon Cheese"], "why": []},
        ]
        ev = report.events_seen(recs)
        self.assertEqual(len(ev["events"]), 2)
        self.assertEqual(ev["events"][0]["picked"], {"Gain $40": 1})
        self.assertEqual(ev["events"][0]["outcomes"][0]["money_after"], 95)
        self.assertIn("Gain Ignit", ev["scored_by_guess"])
        self.assertIn("G?t a", ev["unreadable_lines"])
        self.assertIn("Gain Moon Cheese", ev["new_effect_lines"])

    def test_outcome_diff(self):
        import play
        m = lambda n, v: {"name": n, "level": 1, "stats": [{"stat": "damage", "value": v}]}
        before = {"money": 5, "lives": 8, "team": [m("Panbud", 25), None], "bench": []}
        after = {"day": 4, "money": 60, "lives": 8, "team": [m("Panbud", 25), m("Ignit", 10)], "bench": []}
        out = play.event_outcome(before, after)
        self.assertEqual(out["monsters_new_or_changed"], ["Ignit Lv1 [damage 10]"])
        self.assertEqual(out["monsters_gone_or_changed"], [])


if __name__ == "__main__":
    unittest.main()
