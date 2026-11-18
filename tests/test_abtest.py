import json
import os
import tempfile
import unittest

from engine import abtest


class Report(unittest.TestCase):
    def test_pairs_by_seed_and_skips_cut_lines(self):
        rows = [{"arm": "base", "seed": 1, "badges": 5, "won": False},
                {"arm": "base", "seed": 2, "badges": 10, "won": True},
                {"arm": "x", "seed": 1, "badges": 7, "won": False},
                {"arm": "x", "seed": 2, "badges": 10, "won": True},
                {"arm": "x", "seed": 3, "badges": 1, "won": False}]      # no base run yet: left out
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.jsonl")
            with open(p, "w") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + '\n{"arm": "x", "se')   # a cut-off line
            self.assertEqual(len(abtest._load(p)), 5)
            text = abtest.report(p)
        self.assertIn("base: 2 runs, 1 won, 7.50 badges", text)
        self.assertIn("x: 2 runs, won 1 vs 1, badges 8.50 vs 7.50, difference +1.00", text)


if __name__ == "__main__":
    unittest.main()
