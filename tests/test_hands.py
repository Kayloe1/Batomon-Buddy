r"""Tests for the checks in hands.py. No input is sent and the game is not needed."""
import copy
import json
import sys
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hands  # noqa: E402
import reader  # noqa: E402
import window  # noqa: E402

CAPTURE = ROOT / "captures" / "capture_20261111_101154_435562.png"
BEFORE = json.loads(CAPTURE.with_name(CAPTURE.stem + ".expected.json").read_text(encoding="utf-8"))


def after_buying(i, into=("team", 3)):
    s = copy.deepcopy(BEFORE)
    card = s["shop"][i]
    s["money"] -= card["price"]
    if card["kind"] == "monster":
        s[into[0]][into[1]] = {"name": None, "level": 1, "stats": card["stats"]}
    s["shop"][i] = None
    return s


class Checks(unittest.TestCase):
    def test_buy_ok(self):
        self.assertIsNone(hands.check_buy(BEFORE, after_buying(2), 2))

    def test_buy_item_ok(self):
        self.assertIsNone(hands.check_buy(BEFORE, after_buying(4), 4))

    def test_buy_nothing_happened(self):
        self.assertIsNotNone(hands.check_buy(BEFORE, BEFORE, 2))

    def test_buy_wrong_money(self):
        s = after_buying(2)
        s["money"] += 5
        self.assertIn("money", hands.check_buy(BEFORE, s, 2))

    def test_buy_left_the_shop(self):
        self.assertIn("screen", hands.check_buy(BEFORE, {"screen": "unknown"}, 2))

    def test_move_to_empty_slot(self):
        s = copy.deepcopy(BEFORE)
        s["team"][0], s["team"][4] = s["team"][4], None
        self.assertIsNone(hands.check_move(BEFORE, s, ("team", 4), ("team", 0)))
        self.assertIsNotNone(hands.check_move(BEFORE, BEFORE, ("team", 4), ("team", 0)))

    def test_move_allows_aura_stat_change_in_target_slot(self):
        s = copy.deepcopy(BEFORE)
        moved = copy.deepcopy(s["team"][4])
        moved["stats"] = [{"value": 999, "raw": "999", "stat": "damage", "color": "#ef426b"}]
        s["team"][0], s["team"][4] = moved, None
        self.assertIsNone(hands.check_move(BEFORE, s, ("team", 4), ("team", 0)))

        wrong = copy.deepcopy(BEFORE)
        other = copy.deepcopy(wrong["team"][4])
        other["name"] = "Othermon"
        wrong["team"][0], wrong["team"][4] = other, None
        self.assertIn("different name or level", hands.check_move(BEFORE, wrong, ("team", 4), ("team", 0)))

    def test_swap_two_monsters(self):
        s = copy.deepcopy(BEFORE)
        s["team"][4], s["team"][5] = s["team"][5], s["team"][4]
        self.assertIsNone(hands.check_move(BEFORE, s, ("team", 4), ("team", 5)))

    def test_sell(self):
        s = copy.deepcopy(BEFORE)
        moved = copy.deepcopy(BEFORE)                   # dragged onto the bench, not sold
        moved["bench"][moved["bench"].index(None)], moved["team"][5] = moved["team"][5], None
        self.assertIn("money", hands.check_sell(BEFORE, moved, ("team", 5)))
        s["team"][5] = None
        self.assertIsNone(hands.check_sell(BEFORE, s, ("team", 5)))   # a $0 monster sells for $0
        s["money"] += 5
        self.assertIsNone(hands.check_sell(BEFORE, s, ("team", 5)))

    def test_reroll(self):
        s = copy.deepcopy(BEFORE)
        self.assertIsNotNone(hands.check_reroll(BEFORE, s))
        s["money"] -= 3
        self.assertIsNone(hands.check_reroll(BEFORE, s))


class ClickTargets(unittest.TestCase):
    """The centre of every box the hands click must be on the right thing."""

    @classmethod
    def setUpClass(cls):
        cls.pic = window.canonical_picture(Image.open(CAPTURE))

    def centre(self, box):
        return self.pic.getpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))

    def near(self, box, colour, tol=40, dy=0):
        x, y = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2 + dy
        px = self.pic.getpixel((x, y))
        self.assertTrue(all(abs(a - b) <= tol for a, b in zip(px, colour)), (box, px, colour))

    def test_buttons(self):
        self.near(reader.REROLL_BUTTON, (255, 206, 0), dy=-95)      # yellow, above the letters
        self.near(reader.BATTLE_BUTTON, (239, 66, 107), dy=-80)     # red
        self.near(reader.LOCK_BUTTON, (255, 206, 0), dy=-28)        # yellow

    def test_park_spot_is_plain_background(self):
        r, g, b = self.centre(hands.PARK)
        self.assertTrue(r < 80 and 80 < g < 180 and 90 < b < 200, (r, g, b))

    def test_slot_centres_are_inside_white_slots(self):
        for i in (0, 2, 3):                 # empty team slots in this capture
            self.assertEqual(self.centre(reader.team_slot(i)), (255, 255, 255))
        for i in range(reader.BENCH_SLOTS):
            self.assertEqual(self.centre(reader.bench_slot(i)), (255, 255, 255))

    def test_cards_do_not_overlap_and_sit_in_the_shop_bar(self):
        boxes = [reader.shop_card(i) for i in range(reader.SHOP_SLOTS)]
        for a, b in zip(boxes, boxes[1:]):
            self.assertLess(a[2], b[0])
        bar = reader.SELL_BAR
        for x0, y0, x1, y1 in boxes:
            self.assertTrue(bar[0] <= x0 and x1 <= bar[2] and bar[1] <= y0 and y1 <= bar[3] + 8)


if __name__ == "__main__":
    unittest.main()


class CoveredGame(unittest.TestCase):
    """A window kept on top over the game must not receive the bot's clicks (2026-11-13)."""

    def hands_with(self, cover):
        import types
        h = hands.Hands.__new__(hands.Hands)
        h.dry_run, h.hwnd = False, 1
        h._focus = lambda: None
        h.screen_point = lambda box: (100, 200) if box is not hands.PARK else (5, 5)
        h._covering = lambda x, y: cover
        sent = []
        fake = types.SimpleNamespace(moveTo=lambda *a, **k: sent.append("move"),
                                     mouseDown=lambda *a, **k: sent.append("down"),
                                     mouseUp=lambda *a, **k: sent.append("up"),
                                     scroll=lambda *a, **k: sent.append("scroll"))
        old = sys.modules.get("pyautogui")
        sys.modules["pyautogui"] = fake
        self.addCleanup(lambda: sys.modules.__setitem__("pyautogui", old) if old else sys.modules.pop("pyautogui"))
        return h, sent

    def test_nothing_is_sent_where_another_window_covers_the_game(self):
        h, sent = self.hands_with("Claude")
        for act in (lambda: h._click(reader.STATS_CLOSE),
                    lambda: h._drag(reader.team_slot(0), reader.SELL_BAR),
                    lambda: h._scroll(reader.STATS_SCROLL_POINT, -3)):
            with self.assertRaises(RuntimeError) as e:
                act()
            self.assertIn("'Claude' covers the game", str(e.exception))
        self.assertEqual(sent, [])

    def test_a_visible_game_is_clicked(self):
        h, sent = self.hands_with(None)
        h._click(reader.STATS_CLOSE)
        self.assertEqual(sent[:3], ["move", "down", "up"])


class LookRereadsUnnamed(unittest.TestCase):
    """A one-frame sprite miss on an owned monster is read again (Runerock, 2026-11-13)."""

    def _look(self, reads):
        from unittest import mock
        calls = []

        def fake_read(img):
            calls.append(img)
            return copy.deepcopy(reads[min(len(calls) - 1, len(reads) - 1)])

        h = hands.Hands.__new__(hands.Hands)
        h.hwnd = 1
        with mock.patch.object(window, "is_minimized", return_value=False), \
             mock.patch.object(window, "capture_client", side_effect=lambda hwnd: object()), \
             mock.patch.object(reader, "read_state", side_effect=fake_read), \
             mock.patch.object(hands.time, "sleep"):
            state = h.look(save_if_unreadable=False)
        return state, len(calls)

    def _shop(self, name):
        s = copy.deepcopy(BEFORE)
        s["problems"] = []
        s["team"][0] = {"name": name, "level": 1, "stats": []}
        return s

    def test_unnamed_then_named_keeps_named(self):
        state, n = self._look([self._shop(None), self._shop("Runerock")])
        self.assertEqual(state["team"][0]["name"], "Runerock")
        self.assertEqual(n, 2)

    def test_named_first_read_is_not_repeated(self):
        state, n = self._look([self._shop("Runerock")])
        self.assertEqual(n, 1)

    def test_truly_unknown_monster_stops_after_three_reads(self):
        state, n = self._look([self._shop(None)])
        self.assertIsNone(state["team"][0]["name"])
        self.assertEqual(n, 3)

    def test_unreadable_non_shop_screen_retries_and_keeps_clean_read(self):
        bad_event = {"screen": "event", "problems": ["event 0: cannot read line"]}
        clean_event = {"screen": "event", "problems": [], "choices": ["Heal all"]}
        state, n = self._look([bad_event, bad_event, clean_event])
        self.assertEqual(state["screen"], "event")
        self.assertEqual(state["problems"], [])
        self.assertEqual(n, 3)

    def test_unknown_screen_retries_and_recovers_recognized_screen(self):
        unknown = {"screen": "unknown", "problems": ["unrecognized screen"]}
        clean_trinket = {"screen": "trinket_pick", "problems": [], "trinkets": ["Coupon"]}
        state, n = self._look([unknown, clean_trinket])
        self.assertEqual(state["screen"], "trinket_pick")
        self.assertEqual(state["problems"], [])
        self.assertEqual(n, 2)

    def test_clean_non_shop_screen_is_not_repeated(self):
        clean_event = {"screen": "event", "problems": [], "choices": ["Heal all"]}
        state, n = self._look([clean_event])
        self.assertEqual(n, 1)

    def test_persistently_unreadable_non_shop_stops_after_four_reads(self):
        bad_two = {"screen": "event", "problems": ["p1", "p2"]}
        bad_one = {"screen": "event", "problems": ["p1"]}
        state, n = self._look([bad_two, bad_one, bad_two, bad_two])
        self.assertEqual(state["problems"], ["p1"])
        self.assertEqual(n, 4)



class FastForwardIsLeftAlone(unittest.TestCase):
    """The game remembers fast forward, and the button is grey for about a second at the start
    of every battle: clicking a grey button turned it off (Ed, 2026-11-14). Nothing is clicked
    while waiting for a battle to end."""

    def test_no_click_during_a_battle(self):
        from unittest import mock
        import hands as hands_mod

        h = hands_mod.Hands.__new__(hands_mod.Hands)
        h.dry_run = False
        h.last_image = None
        clock = {"t": 0.0}
        clicks = []
        h.look = lambda save_if_unreadable=True: {"screen": "battle" if clock["t"] < 8 else "result", "problems": []}
        h._click = lambda box: clicks.append(box)
        with mock.patch.object(hands_mod.time, "time", lambda: clock["t"]),                 mock.patch.object(hands_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s)):
            state = h.wait_for({"result"}, 60)
        self.assertEqual(state["screen"], "result")
        self.assertEqual(clicks, [])


class StartRun(unittest.TestCase):
    """start_run(mode) presses CONFIRM only after two clean reads of the wanted mode (Ranked: Ed, 2026-11-14)."""

    def hands_on(self, reads):
        h = hands.Hands.__new__(hands.Hands)
        h.dry_run = False
        clicks = []
        seq = iter(reads)
        last = {"v": None}

        def look():
            last["v"] = next(seq, last["v"])
            return copy.deepcopy(last["v"])
        h.look = look
        h._click = lambda box: clicks.append(box) or [box]
        h.wait_for = lambda wanted, t: {"screen": "trainer_pick", "problems": []}
        h._log = lambda name, r: r
        old = hands.SETTLE_S
        hands.SETTLE_S = 0
        self.addCleanup(lambda: setattr(hands, "SETTLE_S", old))
        return h, clicks

    @staticmethod
    def dialog(mode):
        return {"screen": "new_run", "mode": mode, "region": "Pantra", "problems": []}

    def test_ranked_selects_ranked_then_confirms(self):
        h, clicks = self.hands_on([self.dialog("casual"), self.dialog("ranked")])
        r = h.start_run("ranked")
        self.assertTrue(r.ok, r.message)
        self.assertEqual(clicks, [reader.NEW_RUN_RANKED, reader.NEW_RUN_CONFIRM])

    def test_casual_never_touches_ranked(self):
        h, clicks = self.hands_on([self.dialog("ranked"), self.dialog("casual")])
        self.assertTrue(h.start_casual_run().ok)
        self.assertEqual(clicks, [reader.NEW_RUN_CASUAL, reader.NEW_RUN_CONFIRM])

    def test_unclear_mode_is_never_confirmed(self):
        h, clicks = self.hands_on([self.dialog("casual"), self.dialog(None)])
        r = h.start_run("ranked")
        self.assertFalse(r.ok)
        self.assertNotIn(reader.NEW_RUN_CONFIRM, clicks)

    def test_continue_panel_ranked_only_when_allowed(self):
        panel = {"screen": "continue_confirm", "mode": "ranked", "problems": []}
        h, clicks = self.hands_on([panel])
        self.assertFalse(h.confirm_continue().ok)
        self.assertEqual(clicks, [])
        h, clicks = self.hands_on([panel])
        self.assertTrue(h.confirm_continue(allow_ranked=True).ok)
        self.assertEqual(clicks, [reader.CONTINUE_CONFIRM])


class OverflowTwice(unittest.TestCase):
    """2026-11-15: after selling for one waiting monster, a second one arrived in the overflow slot."""

    def hands_on(self, reads):
        h = hands.Hands.__new__(hands.Hands)
        h.dry_run = False
        seq = iter(reads)
        last = {"v": None}

        def look(save_if_unreadable=True):
            last["v"] = next(seq, last["v"])
            return copy.deepcopy(last["v"])
        h.look = look
        h._drag = lambda a, b: [a, b]
        h.wait_for = lambda wanted, t: look()
        h._log = lambda name, r: r
        old = hands.time.sleep
        hands.time.sleep = lambda s: None
        self.addCleanup(lambda: setattr(hands.time, "sleep", old))
        return h

    @staticmethod
    def screen(bench0, waiting):
        mon = lambda n: {"name": n, "level": 1, "stats": {"attack": 1}}
        return {"screen": "overflow", "problems": [], "bench": [mon(bench0), mon("A"), mon("B"), mon("C")],
                "overflow": mon(waiting)}

    def test_a_second_waiting_monster_counts_as_done(self):
        first, second = self.screen("Venopuff", "Saberhorn"), self.screen("Saberhorn", "Berroon")
        r = self.hands_on([first, second, second]).resolve_overflow(0)
        self.assertTrue(r.ok, r.message)
        self.assertIn("another monster", r.message)

    def test_nothing_changed_is_a_failure(self):
        first = self.screen("Venopuff", "Saberhorn")
        h = self.hands_on([first])
        t = iter(range(0, 1000, 5))
        old = hands.time.time
        hands.time.time = lambda: next(t)
        self.addCleanup(lambda: setattr(hands.time, "time", old))
        self.assertFalse(h.resolve_overflow(0).ok)

    def test_the_shop_still_counts_as_done(self):
        r = self.hands_on([self.screen("Venopuff", "Saberhorn"), {"screen": "shop", "problems": []}]).resolve_overflow(0)
        self.assertTrue(r.ok, r.message)



class EndBadges(unittest.TestCase):
    """2026-11-15: confetti hid one gold coin in one frame of a won run (9 read, 10 shown)."""

    def test_the_most_over_a_few_frames(self):
        import play
        reads = iter([{"screen": "run_over", "badges": b} for b in (10, 9, 10, 10, 10)])
        h = types_ns(dry_run=False, look=lambda save_if_unreadable=True: next(reads))
        old = play.time.sleep
        play.time.sleep = lambda s: None
        self.addCleanup(lambda: setattr(play.time, "sleep", old))
        badges, seen = play.end_badges(h, {"screen": "run_over", "badges": 9})
        self.assertEqual(badges, 10)
        self.assertEqual(seen[0], 9)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)
