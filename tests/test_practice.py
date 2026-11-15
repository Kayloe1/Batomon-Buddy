import unittest

from engine import practice


TEAM = {"trainer": "", "trinkets": [], "slots": [
    {"name": "Pebbler", "level": 1, "stats": {"damage": 5, "shield": 40}},
    {"name": "Panbud", "level": 1, "stats": {"damage": 25}},
    {"name": "Spinarai", "level": 1, "stats": {"damage": 3, "poison": 1}},
    None, None, None]}


class HiddenFactor(unittest.TestCase):
    def test_a_worse_real_result_means_a_stronger_opponent(self):
        easy = practice.hidden_factor(TEAM, TEAM, 3, 0.9)      # we really won with 90% HP left
        hard = practice.hidden_factor(TEAM, TEAM, 3, -0.9)     # we really lost badly
        self.assertIn(easy, practice.GRID)
        self.assertIn(hard, practice.GRID)
        self.assertLess(easy, hard)

    def test_scaled_multiplies_every_number(self):
        t = practice.scaled(TEAM, 2.0)
        self.assertEqual(t["slots"][0]["stats"], {"damage": 10, "shield": 80})
        self.assertIsNone(t["slots"][3])
        self.assertEqual(TEAM["slots"][0]["stats"]["damage"], 5)    # the original is untouched


class Field(unittest.TestCase):
    def test_every_day_has_opponents(self):
        pools = practice.gold_pools()
        for day in range(1, 16):
            self.assertGreaterEqual(len(pools[day]), practice.MIN_REAL, day)

    def test_the_saved_field_has_gold_teams(self):
        teams = practice.load()
        self.assertGreater(len(teams), 50)
        self.assertTrue(all(t["skill"] == "practice_gold" for t in teams))


if __name__ == "__main__":
    unittest.main()
