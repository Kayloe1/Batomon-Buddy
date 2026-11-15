r"""Tests for the screen reader, run on saved captures.

    .venv\Scripts\python.exe -m unittest discover tests -v

Every capture in captures/ that has a "<name>.expected.json" beside it is read
and compared with that file. To add a test: save a capture, check the output of
"python reader.py <capture>" by eye, and save it as the .expected.json.
"""
import json
import sys
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import reader  # noqa: E402
import window  # noqa: E402

CAPTURES = ROOT / "captures"


_comparable = reader.comparable


class PictureGeometry(unittest.TestCase):
    def test_bars_above_and_below(self):
        p = window.picture_rect(window.Rect(75, 200, 2560, 1600))
        self.assertEqual((p.left, p.top, p.width, p.height), (75, 280, 2560, 1440))

    def test_bars_left_and_right(self):
        p = window.picture_rect(window.Rect(0, 0, 2000, 900))
        self.assertEqual((p.left, p.top, p.width, p.height), (200, 0, 1600, 900))

    def test_exact_16_9_has_no_bars(self):
        p = window.picture_rect(window.Rect(10, 20, 1920, 1080))
        self.assertEqual((p.left, p.top, p.width, p.height), (10, 20, 1920, 1080))

    def test_fractions_skip_the_bars_and_round_trip(self):
        r = window.Rect(75, 200, 2560, 1600)
        self.assertEqual(window.frac_to_screen(r, 0, 0), (75, 280))
        self.assertEqual(window.frac_to_screen(r, 1, 1), (75 + 2559, 280 + 1439))
        for fx, fy in [(0.1, 0.9), (0.5, 0.5), (0.93, 0.07)]:
            x, y = window.frac_to_screen(r, fx, fy)
            bx, by = window.screen_to_frac(r, x, y)
            self.assertAlmostEqual(bx, fx, delta=0.001)
            self.assertAlmostEqual(by, fy, delta=0.001)

    def test_button_centre_lands_on_the_button(self):
        fx, fy = reader.box_to_frac(reader.BATTLE_BUTTON)
        x, y = window.frac_to_client(window.Rect(0, 0, 2560, 1600), fx, fy)
        img = Image.open(CAPTURES / "capture_20261111_101154_435562.png").convert("RGB")
        r, g, b = img.getpixel((x, y - 80))      # above the white "BATTLE!" letters
        self.assertTrue(r > 200 and g < 110 and b < 150, (r, g, b))


class SavedCaptures(unittest.TestCase):
    def test_every_labelled_capture(self):
        expected_files = sorted(CAPTURES.glob("*.expected.json"))
        self.assertTrue(expected_files, "no labelled captures found")
        for exp in expected_files:
            png = exp.with_name(exp.name.replace(".expected.json", ".png"))
            with self.subTest(capture=png.name):
                state = reader.read_state(Image.open(png))
                self.assertEqual(_comparable(state), json.loads(exp.read_text(encoding="utf-8")))

    def test_same_shop_with_a_different_background_frame(self):
        # Taken 5 minutes after the taught capture: same shop, the animated
        # background and the monsters' idle animation are in another frame.
        a = reader.read_state(Image.open(CAPTURES / "capture_20261111_101154_435562.png"))
        b = reader.read_state(Image.open(CAPTURES / "capture_20261111_101711_342762.png"))
        self.assertEqual(b["problems"], [])
        self.assertEqual(_comparable(a), _comparable(b))

    def test_covered_window_is_not_mistaken_for_a_screen(self):
        state = reader.read_state(Image.open(CAPTURES / "capture_20261111_101127_605046.png"))
        self.assertEqual(state["screen"], "unknown")
        self.assertTrue(state["problems"])

    def test_other_window_size_is_reported_as_a_problem(self):
        # The templates were cut at 2560x1440. At any other size the reader must
        # say so instead of returning numbers it cannot vouch for.
        img = Image.open(CAPTURES / "capture_20261111_101154_435562.png").convert("RGB")
        small = reader.read_state(img.resize((1920, 1200), Image.LANCZOS))
        self.assertEqual(small["screen"], "shop")
        self.assertTrue(any("templates need" in p for p in small["problems"]))


class ScreenDetection(unittest.TestCase):
    EXPECTED = {
        "matchmaking_day2.png": "matchmaking",
        "vs_day2.png": "unknown",              # VS animation: deliberately not a screen to act on
        "battle_day2.png": "battle",
        "result_win_day2.png": "result",
        "result_loss_day3.png": "result",
        "result_loss_day8.png": "result",
        "stats_day2.png": "stats",
        "event_dragon_hoard.png": "event",
        "event_baby_basilisk.png": "event",
        "event_mysterious_trainer.png": "event",
        "run_over_day8.png": "run_over",
        "shop_day7_swapped.png": "shop",
        "new_run_default.png": "new_run",
        "new_run_ranked_selected.png": "new_run",
        "trainer_pick.png": "trainer_pick",
        "gift_box.png": "gift_box",
        "gift_box_after_event.png": "gift_box",
        "title_screen_no_label.png": "title",
        "trinket_pick_after_event.png": "trinket_pick",
        "trinket_pick_single.png": "trinket_pick",
        "trinket_pick.png": "trinket_pick",
        "trinket_pick_take_all.png": "trinket_pick",
        "trinket_take_all_confirm.png": "take_one_confirm",
        "shop_day1_broke.png": "shop",          # REROLL button is grey here ($0)
    }

    def test_each_screen(self):
        for name, screen in self.EXPECTED.items():
            with self.subTest(capture=name):
                state = reader.read_state(Image.open(CAPTURES / name))
                self.assertEqual(state["screen"], screen)
                if screen not in ("unknown",):
                    self.assertEqual(state["problems"], [])

    def test_take_all_button_is_seen_only_where_it_is(self):
        with_button = reader.read_state(Image.open(CAPTURES / "trinket_pick_take_all.png"))
        self.assertTrue(with_button["take_all"])
        self.assertEqual(with_button["trinkets"], ["Power Bell", "Speed Crest", "Silver Watch"])
        for name in ("trinket_pick.png", "trinket_pick_single.png", "trinket_pick_after_event.png"):
            with self.subTest(capture=name):
                self.assertFalse(reader.read_state(Image.open(CAPTURES / name))["take_all"])

    def test_capital_k_on_a_card_is_not_an_h(self):
        # the foreign Kickrane was read as "Hickrane" and the run stopped (2026-11-14)
        st = reader.read_state(Image.open(CAPTURES / "shop_kickrane_card.png"))
        self.assertEqual(st["problems"], [])
        self.assertEqual((st["shop"][4]["name"], st["shop"][4]["price"]), ("Kickrane", 40))

    def test_near_miss_never_guesses_between_two_names(self):
        # whatever "close" means, two equally close names must give None
        self.assertIsNone(reader.near_miss_name("Xat", ["Bat", "Cat"]))
        self.assertIsNone(reader.near_miss_name("", ["Bat"]))

    def test_near_miss_takes_one_wrong_letter_only(self):
        names = reader.MONSTER_NAMES + reader.ITEM_NAMES
        self.assertEqual(reader.near_miss_name("Hickrane", names), "Kickrane")     # the real K-as-H stop
        self.assertEqual(reader.near_miss_name("Hickrane", ["Kickrane"]), "Kickrane")
        self.assertIsNone(reader.near_miss_name("Hickrame", ["Kickrane"]))       # two letters off
        self.assertIsNone(reader.near_miss_name("H?ckrane", ["Kickrane"]))       # unknown + wrong
        self.assertIsNone(reader.near_miss_name("Kickranes", ["Kickrane"]))      # length differs
        self.assertIsNone(reader.near_miss_name("Kickrane", ["Kickrane"]))       # exact is resolve()'s job
        self.assertIsNone(reader.near_miss_name("Xabbit", ["Rabbit", "Babbit"]))  # two names fit
        self.assertIsNone(reader.near_miss_name("Bxat", ["Boat"]))               # too short to guess

    def test_near_miss_reads_l_as_capital_i(self):
        self.assertEqual(reader.near_miss_name("lronclax", ["Ironclaw"]), "Ironclaw")

    def test_no_two_known_names_are_one_letter_apart(self):
        # near_miss_name relies on this; a new name that breaks it needs a look at the rule
        names = reader.MONSTER_NAMES + reader.ITEM_NAMES
        for name in names:
            with self.subTest(name=name):
                self.assertIsNone(reader.near_miss_name(name, [n for n in names if n != name]))

    def test_rank_max_is_read(self):
        st = reader.read_state(Image.open(CAPTURES / "shop_rank_max.png"))
        self.assertEqual(st["problems"], [])
        self.assertEqual(st["shop_rank"], reader.RANK_MAX)
        self.assertEqual(list(st["shop_odds"].values()), [1, 2, 20, 47, 30])

    def test_end_of_run_screen_gives_the_badges(self):
        for name, badges in (("manual_run_won_champion.png", 10), ("run_over_day8.png", 3)):
            with self.subTest(capture=name):
                st = reader.read_state(Image.open(CAPTURES / name))
                self.assertEqual((st["screen"], st["badges"]), ("run_over", badges))

    def test_events_have_three_choices(self):
        for name in ("event_dragon_hoard.png", "event_baby_basilisk.png", "event_mysterious_trainer.png"):
            self.assertEqual(reader.read_state(Image.open(CAPTURES / name))["choices"], 3, name)


class NewRunSafety(unittest.TestCase):
    """Rule 1: never start a Ranked run. The mode must be read correctly."""

    def test_casual_selected(self):
        s = reader.read_state(Image.open(CAPTURES / "new_run_default.png"))
        self.assertEqual((s["mode"], s["region"], s["problems"]), ("casual", "Pantra", []))

    def test_ranked_selected_is_reported_as_ranked(self):
        s = reader.read_state(Image.open(CAPTURES / "new_run_ranked_selected.png"))
        self.assertEqual(s["mode"], "ranked")

    def test_unclear_mode_is_none_with_a_problem(self):
        import numpy as np
        pic = np.asarray(window.canonical_picture(Image.open(CAPTURES / "new_run_default.png"))).copy()
        pic[1150:1320, 1290:1900] = (200, 200, 60)        # paint over the Casual button
        s = reader.read_new_run(pic)
        self.assertIsNone(s["mode"])
        self.assertTrue(s["problems"])

    def test_ranked_only_on_request(self):
        """The Ranked button is clicked only by start_run("ranked"), and play.py asks for that only
        with --ranked (Ed, 2026-11-14)."""
        import inspect
        import re
        import hands
        self.assertNotIn("NEW_RUN_RANKED", inspect.getsource(__import__("play")))
        self.assertEqual(inspect.getsource(hands).count("NEW_RUN_RANKED"), 1)
        play_src = inspect.getsource(__import__("play"))
        self.assertIn('mode = "ranked" if args.ranked else "casual"', play_src)
        self.assertEqual(re.findall(r"start_(?:casual|ranked)_run\(", play_src), [])


class OtherScreens(unittest.TestCase):
    def test_trainer_names(self):
        s = reader.read_state(Image.open(CAPTURES / "trainer_pick.png"))
        self.assertEqual(s["trainers"], ["Witch", "Painter", "Monster Ranger"])

    def test_trinket_names(self):
        s = reader.read_state(Image.open(CAPTURES / "trinket_pick.png"))
        self.assertEqual(s["trinkets"], ["Fire Bell", "Poison Bell", "Power Crown"])

    def test_levels_two_and_three(self):
        s = reader.read_state(Image.open(CAPTURES / "shop_r2_day2_level2.png"))
        self.assertEqual(s["team"][5]["level"], 2)
        s = reader.read_state(Image.open(CAPTURES / "shop_r2_day7.png"))
        self.assertEqual(s["team"][5]["level"], 3)


class NameResolution(unittest.TestCase):
    def test_unknown_glyphs_resolve_only_when_unique(self):
        self.assertEqual(reader.resolve("Dri??let", reader.MONSTER_NAMES), "Dribblet")
        self.assertIsNone(reader.resolve("????????", reader.MONSTER_NAMES))
        self.assertIsNone(reader.resolve("Dribblex", reader.MONSTER_NAMES))
        self.assertEqual(reader.resolve("RICH LADY", reader.TRAINER_NAMES, ignore_case=True), "Rich Lady")


def _canonical(name: str):
    import numpy as np
    path = CAPTURES / name
    if not path.exists():
        return None
    return np.asarray(window.canonical_picture(Image.open(path).convert("RGB")))


class BattleScreen(unittest.TestCase):
    """Both teams read from the first frames of the Day 2 battle (checked by eye)."""

    def test_both_teams_at_battle_start(self):
        pic = _canonical("battle_day2.png")
        if pic is None:
            self.skipTest("captures/battle_day2.png not here")
        seen = reader.read_battle(pic)
        self.assertEqual(seen["problems"], [])

        def short(slot):
            return slot and (slot["name"], slot["level"], {b["stat"]: b["value"] for b in slot["stats"]})

        self.assertEqual([short(s) for s in seen["my_team"]], [
            ("Brawlmantis", 1, {"damage": 50}),
            ("Venopuff", 1, {"damage": 5, "poison": 4}),
            ("Dribblet", 1, {"heal": 20}),
            None,
            ("Mosslug", 1, {"damage": 5, "heal": 30}),
            ("Panbud", 1, {"damage": 25}),
        ])
        # The Dragon Egg (event reward) is never in the shop, so it has no card sprite to name it.
        self.assertEqual([short(s) for s in seen["opponent_team"]], [
            (None, 1, {"damage": 1}), None, None, ("Venopuff", 1, {"poison": 4}), None, None,
        ])


class CardSprites(unittest.TestCase):
    def test_every_shop_card_gives_a_sprite(self):
        pic = _canonical("shop_day1.png")
        if pic is None:
            self.skipTest("captures/shop_day1.png not here")
        state = reader.read_state(Image.open(CAPTURES / "shop_day1.png").convert("RGB"))
        for i, card in enumerate(state["shop"]):
            if card and card.get("name"):
                got = reader.card_sprite(pic, i)
                self.assertIsNotNone(got, card["name"])
                rgb, mask = got
                self.assertGreater(mask.mean(), 0.2, card["name"])

    def test_tall_sprite_keeps_its_inside(self):
        # Electranade's fuse pokes above the sprite rows; the panel fill used to eat its body
        name = "unread_20261112_210439_659871.png"
        pic = _canonical(name)
        if pic is None:
            self.skipTest(f"captures/{name} not here")
        rgb, mask = reader.card_sprite(pic, 0)
        self.assertTrue(reader._looks_like_sprite(rgb, mask))
        self.assertLess((rgb[mask].max(axis=1) < 70).mean(), 0.5)

    def test_outline_and_grey_cutouts_are_not_learned(self):
        import numpy as np
        mask = np.ones((20, 20), bool)
        outline = np.zeros((20, 20, 3), np.uint8)
        grey = np.full((20, 20, 3), 128, np.uint8)
        colour = grey.copy()
        colour[..., 0] = 200
        self.assertFalse(reader._looks_like_sprite(outline, mask))
        self.assertFalse(reader._looks_like_sprite(grey, mask))
        self.assertTrue(reader._looks_like_sprite(colour, mask))


if __name__ == "__main__":
    unittest.main()


class Shiny(unittest.TestCase):
    """Shiny copies are told apart by their colour palette ("Name@shiny" sprite templates)."""

    def _read(self, name):
        path = CAPTURES / name
        if not path.exists():
            self.skipTest(f"captures/{name} not here")
        return reader.read_state(Image.open(path).convert("RGB"))

    def test_shiny_stingarde_and_magmite(self):
        st = self._read("manual_shop_day5_shiny_magmite_bench2_fumungus_team0.png")
        self.assertEqual((st["team"][2]["name"], st["team"][2].get("shiny")), ("Stingarde", True))
        self.assertEqual((st["bench"][2]["name"], st["bench"][2].get("shiny")), ("Magmite", True))
        self.assertFalse(st["team"][4].get("shiny"))          # a plain Magmite next to it

    def test_plain_stingarde_is_not_shiny(self):
        st = self._read("shop_r2_day3.png")
        self.assertEqual(st["team"][4]["name"], "Stingarde")
        self.assertFalse(st["team"][4].get("shiny"))


class ShinyShopCards(unittest.TestCase):
    def test_glimmer_shop_cards_are_shiny(self):
        path = CAPTURES / "manual_shop_glimmer_all_shiny_cards.png"
        if not path.exists():
            self.skipTest("captures/manual_shop_glimmer_all_shiny_cards.png not here")
        st = reader.read_state(Image.open(path).convert("RGB"))
        self.assertEqual([bool(c and c.get("shiny")) for c in st["shop"]], [True, False, True, True, True])

    def test_plain_shop_cards_are_not_shiny(self):
        path = CAPTURES / "shop_r2_day3.png"
        if not path.exists():
            self.skipTest("captures/shop_r2_day3.png not here")
        st = reader.read_state(Image.open(path).convert("RGB"))
        self.assertFalse(any(c and c.get("shiny") for c in st["shop"]))


class OverflowScreen(unittest.TestCase):
    def test_overflow_screen(self):
        path = CAPTURES / "stuck_20261112_211945.png"
        if not path.exists():
            self.skipTest("captures/stuck_20261112_211945.png not here")
        st = reader.read_state(Image.open(path).convert("RGB"))
        self.assertEqual(st["screen"], "overflow")
        self.assertEqual([b["name"] for b in st["bench"]], ["Cicadence", "Cicadence", "Frizzly", "Frizzly"])
        self.assertEqual(st["overflow"]["level"], 1)
        self.assertEqual(st["problems"], [])


class CardSeven(unittest.TestCase):
    def test_coupon_price_seven_is_read(self):
        # a Coupon took Mosslug to $7; the card font had no 7 until 2026-11-13
        p = Path(__file__).resolve().parent.parent / "captures" / "unread_20261113_084204_238777.png"
        if not p.exists():
            self.skipTest("capture not here")
        st = reader.read_state(Image.open(p))
        self.assertEqual(st["problems"], [])
        self.assertEqual([s["price"] for s in st["shop"]], [0, 12, 7, 12, 15])



class CrownInEmptySlot(unittest.TestCase):
    def test_a_crown_marker_does_not_make_a_monster(self):
        from PIL import Image
        p = Path(__file__).resolve().parent.parent / "captures" / "shop_crown_in_empty_slot.png"
        if not p.exists():
            self.skipTest("capture not here")
        st = reader.read_state(Image.open(p))
        self.assertEqual(st["problems"], [])
        self.assertIsNone(st["team"][1])


class TooltipOverLock(unittest.TestCase):
    """A hovered shop card's 'RIGHT CLICK TO INSPECT' tooltip crosses the LOCK button (2026-11-13)."""

    def test_lock_still_read(self):
        from PIL import Image
        s = reader.read_state(Image.open(ROOT / "captures" / "shop_tooltip_over_lock.png"))
        self.assertEqual(s["screen"], "shop")
        self.assertIs(s["locked"], False)
        self.assertEqual([p for p in s["problems"] if "lock" in p], [])


class HoveringSprites(unittest.TestCase):
    """Team and bench monsters bob a few px; matching slides the saved sprite (2026-11-13)."""

    def test_shifted_monsters_are_named(self):
        from PIL import Image
        s = reader.read_state(Image.open(ROOT / "captures" / "shop_chef_day9.png"))
        self.assertEqual(s["team"][4]["name"], "Runerock")     # unnamed before (43 unshifted)
        self.assertEqual(s["bench"][3]["name"], "Stingarde")   # unnamed before (35)
        s = reader.read_state(Image.open(ROOT / "captures" / "shop_day11_hp.png"))
        self.assertEqual(s["team"][3]["name"], "Joltail")      # unnamed before (43)


class Specks(unittest.TestCase):
    """2026-11-15: a sprite's head under the money digits read "$8" as "$8?"."""

    def test_a_speck_below_the_text_is_dropped_and_the_money_reads(self):
        import numpy as np
        st = reader.read_state(Image.open(CAPTURES / "capture_20261111_101154_435562.png"))
        pic = np.asarray(window.canonical_picture(Image.open(CAPTURES / "capture_20261111_101154_435562.png"))).copy()
        x0, y0, x1, y1 = reader.MONEY
        pic[1152:1155, 1308:1312] = 255                      # the 4x3 blob on the box's bottom edge
        self.assertEqual(reader.read_state(Image.fromarray(pic))["money"], st["money"])

    def test_a_speck_inside_the_rows_is_kept(self):
        import numpy as np
        m = np.zeros((40, 60), bool)
        m[5:35, 5:15] = True
        m[18:21, 40:43] = True                                # level with the text: may be part of a glyph
        self.assertTrue(reader.drop_specks(m)[19, 41])
        m[37:40, 40:43] = True                                # under the text: dropped
        self.assertFalse(reader.drop_specks(m)[38, 41])


class NewNames(unittest.TestCase):
    """2026-11-15 (Smuggler): 'Lamplet' read letter for letter but is in no list."""

    def test_a_clean_new_name_is_an_unknown_card_not_a_stop(self):
        self.assertIn("Lamplet", reader.MONSTER_NAMES)        # a Jinto monster since 2026-11-15, no longer new
        self.assertTrue(reader._clean_new_name("Zorbwick"))
        for raw in ("Zorb?ick", "Zo", "zorbwick", "ZORBWICK", "Zorb wick", "Zorb1ick", "Hickrane"):
            self.assertFalse(reader._clean_new_name(raw), raw)

    def test_the_engine_never_buys_an_unknown_card(self):
        from engine import model
        st = json.loads((CAPTURES / "capture_20261111_101154_435562.expected.json").read_text(encoding="utf-8"))
        st["shop"][0] = {"name": "Lamplet", "price": 3, "kind": "unknown", "stats": [], "rarity": None}
        s = model.from_reader(st)
        self.assertFalse(model.can_buy(s, 0))


class JintoSprites(unittest.TestCase):
    """Templates made from the batomon.com pictures (build_jinto_sprites.py) name Jinto monsters on the team."""

    def test_made_templates_name_themselves_even_when_the_sprite_bobs(self):
        import numpy as np
        from PIL import Image
        for name in ("Cinnabark", "Drumire", "Miasmaw", "Quillustrous"):
            patch = np.asarray(Image.open(reader.SPRITE_DIR / f"{name}.png").convert("RGB"))
            bobbed = np.full_like(patch, 255)
            bobbed[:-3, 4:] = patch[3:, :-4]
            slot = np.full((200, 260, 3), 255, np.uint8)
            slot[36:140, 40:200] = bobbed
            self.assertEqual(reader.species_of(reader.match_sprite(slot)[0]), name)
