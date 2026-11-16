"""scorecard.py groups logs into runs and writes the page (no game needed)."""
import json
import tempfile
import unittest
from pathlib import Path

import scorecard


def write(d: Path, name: str, recs: list[dict]) -> Path:
    p = d / name
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return p


def shop(day, badges, lives=3, trainer="Chef"):
    return {"kind": "shop", "state": {"day": day, "badges": badges, "lives": lives, "trainer": trainer}}


class Scorecard(unittest.TestCase):
    def test_runs_across_logs_and_modes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            a = write(d, "run_20261115_010000.jsonl", [
                {"time": "2026-11-15T01:00:00", "kind": "new_run", "mode": "ranked", "ok": True},
                {"kind": "trainer", "offered": ["Chef", "Witch"], "picked": 0, "ok": True},
                shop(1, 0), {"kind": "battle"}, {"kind": "stop", "why": "unread: RANK label"}])
            b = write(d, "run_20261115_013000.jsonl", [          # the same run, continued after a stop
                shop(5, 4), {"kind": "battle"}, {"kind": "run_over", "badges": 10}])
            c = write(d, "run_20261115_030000.jsonl", [
                {"time": "2026-11-15T03:00:00", "kind": "new_run", "mode": "casual", "ok": True},
                {"kind": "trainer", "offered": ["Witch"], "picked": 0, "ok": True},
                shop(1, 0, trainer="Witch"), {"kind": "battle"}])
            rs = scorecard.rows([c, a, b])
        self.assertEqual(len(rs), 2)
        ranked = next(r for r in rs if r["mode"] == "ranked")
        self.assertEqual((ranked["result"], ranked["badges"], ranked["stops"]), ("won", 10, 1))
        self.assertEqual(ranked["logs"], [a.name, b.name])
        casual = next(r for r in rs if r["mode"] == "casual")
        self.assertEqual((casual["trainer"], casual["result"]), ("Witch", "unfinished"))
        t = scorecard.totals([ranked])
        self.assertEqual((t["runs"], t["won"], t["mean_badges"]), (1, 1, 10.0))
        page = scorecard.page(rs)
        self.assertIn("<h2>Ranked</h2>", page)
        self.assertIn("RANK label", page)

    def test_a_run_started_by_hand_has_no_mode(self):
        self.assertIsNone(scorecard.run_mode([{"kind": "trainer", "ok": True}]))

    def test_the_page_escapes_text(self):
        r = {"date": None, "mode": None, "trainer": "<b>", "result": "lost", "badges": 3, "last_day": 5,
             "lives_lost_on": [], "stopped": "<script>", "stops": 0, "logs": ["x"]}
        page = scorecard.page([r])
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;b&gt;", page)

    def test_trainer_totals_and_page_breakdown(self):
        rs = [
            {"date": "2026-11-15T01:00", "mode": "ranked", "trainer": "Chef", "result": "won",
             "badges": 10, "last_day": 14, "lives_lost_on": [], "stopped": None, "stops": 0, "logs": ["a"]},
            {"date": "2026-11-15T02:00", "mode": "ranked", "trainer": "Chef", "result": "lost",
             "badges": 6, "last_day": 10, "lives_lost_on": [5], "stopped": None, "stops": 0, "logs": ["b"]},
            {"date": "2026-11-15T03:00", "mode": "ranked", "trainer": "Witch", "result": "unfinished",
             "badges": 2, "last_day": 3, "lives_lost_on": [], "stopped": None, "stops": 1, "logs": ["c"]},
        ]
        tt = scorecard.trainer_totals(rs)
        self.assertEqual(tt[0], {"trainer": "Chef", "runs": 2, "finished": 2, "won": 1, "winrate": 50, "mean_badges": 8.0})
        self.assertEqual(tt[1], {"trainer": "Witch", "runs": 1, "finished": 0, "won": 0, "winrate": None, "mean_badges": 2.0 if False else None})
        page = scorecard.page(rs)
        self.assertIn("<th>Winrate</th>", page)
        self.assertIn("<td>50%</td>", page)
        self.assertIn("<td>8.0</td>", page)


if __name__ == "__main__":
    unittest.main()
