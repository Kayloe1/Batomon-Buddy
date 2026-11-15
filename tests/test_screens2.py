"""Main menu and the Cash In trinket dialog."""
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import glyphs
import reader
import window

CAP = Path(__file__).resolve().parent.parent / "captures"


def state(name):
    return reader.read_state(Image.open(CAP / name))


class NewScreens(unittest.TestCase):
    def test_main_menu(self):
        self.assertEqual(state("main_menu.png")["screen"], "main_menu")

    def test_menu_with_a_run_open_is_its_own_screen(self):
        self.assertEqual(state("main_menu_continue.png")["screen"], "main_menu_continue")
        self.assertEqual(state("main_menu.png")["screen"], "main_menu")

    def test_new_run_box_is_never_on_a_button_of_the_continue_menu(self):
        # on the five-button menu the New Run box of the four-button menu must not hit New Run
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "main_menu_continue.png")))
        x0, y0, x1, y1 = reader.MAIN_MENU_CONTINUE
        self.assertGreater(reader._near(pic[y0:y1, x0:x1], (239, 66, 107), 40).mean(), 0.5)
        self.assertLess(y1, 796)            # the Continue box ends above the New Run button (starts at y 796)

    def test_continue_panel_reads_casual(self):
        st = state("continue_confirm_casual.png")
        self.assertEqual(st["screen"], "continue_confirm")
        self.assertEqual(st["mode"], "casual")

    def test_continue_panel_refuses_any_other_label(self):
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "continue_confirm_casual.png"))).copy()
        x0, y0, x1, y1 = reader.CONTINUE_MODE_LABEL
        label = pic[y0:y1, x0:x1].copy()
        # a longer label (as "RANKED MODE" or anything unseen would be): not casual
        pic[y0:y1, x0 + 60:x1] = label[:, :-60]
        self.assertIsNone(reader.continue_mode(pic))
        # no label at all: not casual
        pic[y0:y1, x0:x1] = 255
        self.assertIsNone(reader.continue_mode(pic))

    def test_continue_panel_reads_ranked_only_from_a_saved_label(self):
        import tempfile
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "continue_confirm_casual.png"))).copy()
        x0, y0, x1, y1 = reader.CONTINUE_MODE_LABEL
        label = pic[y0:y1, x0:x1].copy()
        pic[y0:y1, x0 + 60:x1] = label[:, :-60]          # stands in for a "RANKED MODE" label
        old = reader.RANKED_LABEL_FILE
        self.addCleanup(lambda: setattr(reader, "RANKED_LABEL_FILE", old))
        with tempfile.TemporaryDirectory() as d:
            reader.RANKED_LABEL_FILE = Path(d) / "continue_ranked_mode.png"
            self.assertIsNone(reader.continue_mode(pic))     # nothing saved yet: refused
            Image.fromarray((reader.continue_mode_mask(pic) * 255).astype(np.uint8)).save(reader.RANKED_LABEL_FILE)
            self.assertEqual(reader.continue_mode(pic), "ranked")
            self.assertEqual(reader.continue_mode(np.asarray(window.canonical_picture(
                Image.open(CAP / "continue_confirm_casual.png")))), "casual")

    def test_continue_buttons(self):
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "continue_confirm_casual.png")))
        for box in (reader.CONTINUE_CONFIRM, reader.CONTINUE_CANCEL):
            x0, y0, x1, y1 = box
            self.assertGreater(reader._near(pic[y0:y1, x0:x1], (239, 66, 107), 40).mean(), 0.5)
        self.assertGreater(reader.CONTINUE_CONFIRM[0], reader.CONTINUE_CANCEL[2])

    def test_select_target_screen(self):
        st = state("select_target.png")
        self.assertEqual(st["screen"], "select_target")
        self.assertFalse(st["confirm_enabled"])
        self.assertIsNone(st["team"][0])
        self.assertEqual([b["value"] for b in st["team"][1]["stats"]], [150])
        self.assertEqual(st["bench"], [None] * 4)
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "select_target.png")))
        x0, y0, x1, y1 = reader.TARGET_BACK
        self.assertGreater(reader._near(pic[y0:y1, x0:x1], (239, 66, 107), 40).mean(), 0.5)
        self.assertLess(reader.TARGET_CONFIRM[2], reader.TARGET_BACK[0])     # Confirm never overlaps Back

    def test_title(self):
        self.assertEqual(state("title_screen.png")["screen"], "title")
        self.assertEqual(state("title_screen.png")["problems"], [])

    def test_stolen_trinket(self):
        # Burglar (2026-11-15, stuck_20261114_232733.png): one card, "Stolen!" above it, Confirm under it.
        # Rebuilt from a single-card trinket pick; it read as a trinket pick with Take All before.
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "trinket_pick_single.png"))).copy()
        names = [t for t in reader.read_state(Image.fromarray(pic))["trinkets"] if t]
        pic[192:228, 1208:1348] = (255, 211, 14)
        pic[940:1020, 1068:1492] = (239, 66, 107)
        pic[1020:1044, 1068:1492] = (189, 41, 74)
        pic[1196:1318, 20:396] = (49, 198, 247)
        pic[1290:1310, 452:470] = (255, 74, 107)
        st = reader.read_state(Image.fromarray(pic))
        self.assertEqual(st["screen"], "trinket_stolen")
        self.assertEqual([t for t in st["trinkets"] if t], names)
        pic[192:228, 1208:1348] = (255, 239, 98)            # the text shimmers (2026-11-15)
        self.assertEqual(reader.read_state(Image.fromarray(pic))["screen"], "trinket_stolen")
        x0, y0, x1, y1 = reader.STOLEN_CONFIRM
        self.assertGreater(reader._near(pic[y0:y1, x0:x1], (239, 66, 107), 40).mean(), 0.9)

    def test_trainer_ability_choice(self):
        # Mysterious Mask (2026-11-15, stuck_20261114_201501.png): the trainer cards 28 px higher, a blue
        # HIDE bottom left, a pink-framed banner. Rebuilt from a trainer pick capture.
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "trainer_pick.png"))).copy()
        names = reader.read_state(Image.fromarray(pic))["trainers"]
        pic[:-28] = pic[28:].copy()
        pic[1196:1318, 20:396] = (49, 198, 247)
        pic[1100:1404, 452:2108] = (255, 74, 107)
        pic[1110:1394, 470:2090] = (255, 255, 255)
        st = reader.read_state(Image.fromarray(pic))
        self.assertEqual(st["screen"], "trainer_ability")
        self.assertEqual(st["trainers"], names)
        x0, y0, x1, y1 = reader.trainer_ability_card(1)
        self.assertLess(y0, reader.trainer_card(1)[1])

    def test_ranked_rank_card(self):
        # 2026-11-15, measured on stuck_20261114_194622.png (Rank Up!, Silver 8): rebuilt from its colours
        pic = np.full((1440, 2560, 3), (20, 54, 52), np.uint8)
        pic[308:988, 996:1596] = (231, 231, 231)
        pic[312:984, 1000:1592] = (156, 156, 173)
        pic[320:975, 1008:1584] = (231, 231, 231)
        pic[350:960, 1030:1560] = (255, 255, 255)
        pic[1040:1136, 1032:1560] = (239, 66, 107)
        pic[1136:1160, 1032:1560] = (189, 41, 74)
        self.assertEqual(reader.read_state(Image.fromarray(pic))["screen"], "rank_result")
        x0, y0, x1, y1 = reader.RANK_CONFIRM
        self.assertGreater(reader._near(pic[y0:y1, x0:x1], (239, 66, 107), 40).mean(), 0.9)

    def test_cash_in(self):
        self.assertEqual(state("cash_in_fake_diamond.png")["screen"], "cash_in")

    def test_two_trinket_cards_with_one_button_are_the_same_screen(self):
        # doubled Locked Box (Scavenger, 2026-11-15, stuck_20261115_005359.png): two cards, the Cash In button
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "cash_in_fake_diamond.png"))).copy()
        pic[150:950, 600:1960] = (42, 42, 42)
        for x0 in (636, 1308):
            pic[236:888, x0:x0 + 616] = (212, 124, 0)
            pic[300:880, x0 + 4:x0 + 612] = (255, 255, 255)
            pic[580:620, x0 + 10:x0 + 606] = (231, 231, 231)
        self.assertEqual(reader.read_state(Image.fromarray(pic))["screen"], "cash_in")

    def test_a_common_trinket_box_with_one_button_is_the_same_screen(self):
        # Locked Box (2026-11-15): same card and button, grey (112, 112, 122) header instead of blue
        pic = np.asarray(window.canonical_picture(Image.open(CAP / "cash_in_fake_diamond.png"))).copy()
        pic[236:300, 972:1588] = (112, 112, 122)
        self.assertEqual(reader.read_state(Image.fromarray(pic))["screen"], "cash_in")

    def test_buttons_land_on_their_buttons(self):
        for name, box in (("main_menu.png", reader.MAIN_MENU_NEW_RUN), ("cash_in_fake_diamond.png", reader.CASH_IN_BUTTON),
                          ("main_menu_continue.png", reader.MAIN_MENU_CONTINUE)):
            pic = np.asarray(window.canonical_picture(Image.open(CAP / name)))
            x0, y0, x1, y1 = box
            red = reader._near(pic[y0:y1, x0:x1], (239, 66, 107), 40).mean()
            self.assertGreater(red, 0.5, name)      # the box sits on the red button (text aside)

    def test_event_effect_lines_are_read(self):
        self.assertEqual(state("event_dragon_hoard.png")["effects"],
                         ["Gain Dragon Egg", "Gain Hero's Sword", "Gain $20"])
        self.assertEqual(state("event_20261112_132759.png")["effects"],
                         ["Gain a foreign Rare monster. It can now appear in your shop.", "Gain Kaleidoscope",
                          "Gain Fake Diamond"])
        self.assertEqual(state("event_20261112_090216.png")["effects"][1],
                         "Give a monster Electric typing and +5 Shock")
        self.assertEqual(state("event_20261112_133833.png")["choices"], 3)

    def test_other_screens_not_mistaken(self):
        for name in ("stats_day2.png", "result_loss_day3.png"):
            self.assertNotIn(state(name)["screen"], ("main_menu", "cash_in"))




class ShopAfterReroll(unittest.TestCase):
    """The day 10 shop the bot stopped on: '$40' and 'Onsetra' (capital O is the glyph of zero)."""

    def test_reads_cleanly(self):
        state = reader.read_state(Image.open(CAP / "shop_day10_reroll.png"))
        self.assertEqual(state["problems"], [])
        self.assertEqual([(c["name"], c["price"]) for c in state["shop"]],
                         [("Humbolt", 25), ("Fumungus", 40), ("Aristobat", 25), ("Onsetra", 50), ("Basic Candy", 30)])
        self.assertEqual((state["day"], state["money"]), (10, 97))

    def test_capital_o_is_zero(self):
        self.assertEqual(reader.resolve("0nsetra", reader.MONSTER_NAMES), "Onsetra")


class CompletedNumbers(unittest.TestCase):
    """A number with an unknown digit is completed only when the game data agrees with what was read."""

    def test_fill_number(self):
        self.assertEqual(reader.fill_number("24?00", 24900), 24900)
        self.assertIsNone(reader.fill_number("24?00", 34700))      # a read digit disagrees
        self.assertIsNone(reader.fill_number("24?0", 24900))       # wrong length
        self.assertIsNone(reader.fill_number("???00", 24900))      # too little was read
        self.assertIsNone(reader.fill_number("24900", 24900))      # nothing unknown: not this function's job

    def test_day11_shop(self):
        state = reader.read_state(Image.open(CAP / "shop_day11_hp.png"))
        self.assertEqual(state["problems"], [])
        self.assertEqual((state["day"], state["trainer_hp"], state["money"]), (11, 24900, 87))
        self.assertEqual([(c["name"], c["price"]) for c in state["shop"]],
                         [("Geminiss", 50), ("Electranade", 25), ("Aristobat", 25), ("Sirenade", 40), ("Voucher", 5)])

    def test_unknown_hp_digit_is_completed_from_the_day(self):
        old = reader._store
        try:
            st = glyphs.GlyphStore()
            st.fonts["hp"] = [e for e in st.fonts["hp"] if e[0] != "9"]
            reader._store = st
            state = reader.read_state(Image.open(CAP / "shop_day11_hp.png"))
        finally:
            reader._store = old
        self.assertEqual(state["problems"], [])
        self.assertEqual(state["trainer_hp"], 24900)
        self.assertEqual(len(state["notes"]), 1)

    def test_unknown_price_digit_is_completed_from_the_list_price(self):
        old = reader._store
        try:
            st = glyphs.GlyphStore()
            st.fonts["card"] = [e for e in st.fonts["card"] if e[0] != "4"]
            reader._store = st
            state = reader.read_state(Image.open(CAP / "shop_day11_hp.png"))
        finally:
            reader._store = old
        self.assertEqual(state["problems"], [])
        self.assertEqual(state["shop"][3]["price"], 40)


if __name__ == "__main__":
    unittest.main()
