"""Teaching from a stop (stop_lessons.py): the red box's "Teach it" without a coding session.

The trick of these tests: take a capture the reader reads perfectly, delete one glyph from a copy of the
glyph file, and the same capture becomes a stop. Teaching it back must fix it, and nothing may be
taught that is wrong or half done.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import glyphs  # noqa: E402
import reader  # noqa: E402
import stop_lessons  # noqa: E402
import teach  # noqa: E402
import window  # noqa: E402

CAPTURE = ROOT / "captures" / "shop_kickrane_card.png"      # shop card 4 is "Kickrane $40"


class TeachFromAStop(unittest.TestCase):
    """Shop card 4 of the capture is "Kickrane $40" (a foreign monster); lives is "3"."""

    FORGET = {"card": "K", "chunky": "3"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.glyph_file = tmp / "glyphs.json"
        raw = json.loads(glyphs.TEMPLATE_FILE.read_text(encoding="utf-8"))
        for font, letters in self.FORGET.items():
            raw[font] = [e for e in raw[font] if e["label"] not in letters]
        self.glyph_file.write_text(json.dumps(raw), encoding="utf-8")
        self.user_lessons = tmp / "user_lessons.json"
        no_card_sprites = tmp / "no_card_sprites"     # the person teaches here, not the card pictures
        no_card_sprites.mkdir()
        for target, name, value in ((glyphs, "TEMPLATE_FILE", self.glyph_file),
                                    (stop_lessons, "USER_LESSONS", self.user_lessons),
                                    (reader, "CARD_SPRITE_DIR", no_card_sprites)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        reader.reload_templates()
        self.addCleanup(reader.reload_templates)

    def test_what_the_reader_cannot_read_is_offered(self):
        keys = [i.key for i in stop_lessons.items_for(CAPTURE)]
        self.assertIn("lives", keys)
        item = next(i for i in stop_lessons.items_for(CAPTURE) if i.key == "lives")
        self.assertIn("?", item.guess)
        self.assertGreater(item.picture.size[0], 50)

    def test_typing_the_truth_fixes_it_and_is_remembered(self):
        before = self.glyph_file.read_bytes()
        r = stop_lessons.apply(CAPTURE, {"lives": "3"})
        self.assertFalse(r.ok)                       # the Kickrane card is still a stop of its own
        self.assertEqual(r.remaining, ["shop 4: unknown name 'Hickrane'"])
        self.assertNotEqual(self.glyph_file.read_bytes(), before)
        self.assertEqual(self.glyph_file.with_name("glyphs.json.bak").read_bytes(), before)
        self.assertEqual([i.key for i in stop_lessons.items_for(CAPTURE)], ["shop4"])
        saved = json.loads(self.user_lessons.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["text"], {"lives": "3"})
        self.assertTrue(saved[0]["file"].endswith("shop_kickrane_card.png"))

    def test_teach_py_replays_the_lessons(self):
        stop_lessons.apply(CAPTURE, {"lives": "3"})
        raw = json.loads(self.glyph_file.read_text(encoding="utf-8"))
        raw["chunky"] = [e for e in raw["chunky"] if e["label"] != "3"]
        self.glyph_file.write_text(json.dumps(raw), encoding="utf-8")        # forget it again
        reader.reload_templates()
        self.assertTrue(stop_lessons.items_for(CAPTURE))
        with mock.patch("builtins.print"):
            teach.main(lessons=stop_lessons.load_user_lessons())
        self.assertEqual([i.key for i in stop_lessons.items_for(CAPTURE)], ["shop4"])

    def test_a_card_with_a_letter_the_bot_gets_wrong_needs_a_second_click(self):
        # the real stop of 2026-11-14: a capital K read as H with confidence
        r = stop_lessons.apply(CAPTURE, {"shop4": "Kickrane $40"})     # the bot reads "Hickrane"
        self.assertTrue(r.override_needed)
        self.assertIn("Teach anyway", r.message)
        r = stop_lessons.apply(CAPTURE, {"shop4": "Kickrane $40"}, override=True)
        self.assertFalse(any(p.startswith("shop 4") for p in r.remaining), r.message)   # the K is a K now
        self.assertNotIn("shop4", [i.key for i in stop_lessons.items_for(CAPTURE)])

    def test_the_wrong_number_of_characters_changes_nothing(self):
        before = self.glyph_file.read_bytes()
        r = stop_lessons.apply(CAPTURE, {"lives": "33"})
        self.assertFalse(r.ok)
        self.assertIn("Nothing was changed", r.message)
        self.assertEqual(self.glyph_file.read_bytes(), before)
        self.assertFalse(self.user_lessons.exists())

    def test_a_typo_against_a_sure_digit_is_refused_until_overridden(self):
        before = self.glyph_file.read_bytes()
        r = stop_lessons.apply(CAPTURE, {"hp": "17400"})          # the bot reads 17300 and is sure
        self.assertFalse(r.ok)
        self.assertTrue(r.override_needed)
        self.assertEqual(self.glyph_file.read_bytes(), before)

    def test_nothing_typed_and_a_bad_form_do_nothing(self):
        self.assertFalse(stop_lessons.apply(CAPTURE, {"lives": "  "}).ok)
        r = stop_lessons.apply(CAPTURE, {"shop4": "Kickrane"})            # no price
        self.assertFalse(r.ok)
        self.assertIn("price", r.message)

    def test_a_name_missing_from_the_game_data_is_said_out_loud(self):
        with mock.patch.object(reader, "MONSTER_NAMES", [n for n in reader.MONSTER_NAMES if n != "Kickrane"]):
            r = stop_lessons.apply(CAPTURE, {"shop4": "Kickrane $40"}, override=True)
        self.assertIn("not in the game data", r.message)

    def test_a_screen_that_is_not_the_shop_offers_nothing(self):
        other = ROOT / "captures" / "trainer_pick.png"
        self.assertEqual(stop_lessons.items_for(other), [])
        self.assertIn("trainer_pick", stop_lessons.unteachable(other)[0])


class SelfTaughtCardNames(unittest.TestCase):
    """The real stop of 2026-11-14 ("Hickrane"), solved without a person: the card's picture is
    Kickrane's and the text fits Kickrane, so the bot names it and teaches itself the K."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.glyph_file = tmp / "glyphs.json"
        raw = json.loads(glyphs.TEMPLATE_FILE.read_text(encoding="utf-8"))
        raw["card"] = [e for e in raw["card"] if e["label"] != "K"]
        self.glyph_file.write_text(json.dumps(raw), encoding="utf-8")
        self.user_lessons = tmp / "user_lessons.json"
        self.captures = tmp / "captures"
        for target, name, value in ((glyphs, "TEMPLATE_FILE", self.glyph_file),
                                    (stop_lessons, "USER_LESSONS", self.user_lessons),
                                    (stop_lessons, "CAPTURE_DIR", self.captures)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        reader.reload_templates()
        self.addCleanup(reader.reload_templates)
        self.image = Image.open(CAPTURE)

    def test_the_card_is_named_by_its_picture_instead_of_stopping(self):
        st = reader.read_state(self.image)
        self.assertEqual(st["problems"], [])
        card = st["shop"][4]
        self.assertEqual((card["name"], card["price"], card["raw"], card.get("name_from")),
                         ("Kickrane", 40, "Hickrane", "sprite"))
        self.assertTrue(any("taken as Kickrane" in n for n in st["notes"]))
        self.assertTrue(all(c.get("name_from") is None for c in st["shop"][:4]))   # read as before

    def test_the_bot_then_teaches_itself_the_letter(self):
        before = self.glyph_file.read_bytes()
        lines = stop_lessons.self_teach_cards(self.image, reader.read_state(self.image))
        self.assertEqual(len(lines), 1)
        self.assertIn("Taught myself", lines[0])
        self.assertNotEqual(self.glyph_file.read_bytes(), before)
        card = reader.read_state(self.image)["shop"][4]
        self.assertEqual((card["raw"], card.get("name_from")), ("Kickrane", None))   # read from the text now
        saved = json.loads(self.user_lessons.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["text"], {"shop4": "Kickrane|$40"})
        self.assertEqual(len(list(self.captures.glob("self_taught_*.png"))), 1)      # kept for teach.py
        # nothing left to teach: a second look does nothing
        self.assertEqual(stop_lessons.self_teach_cards(self.image, reader.read_state(self.image)), [])

    def test_without_a_picture_match_it_still_stops(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        with mock.patch.object(reader, "CARD_SPRITE_DIR", empty):
            st = reader.read_state(self.image)
        self.assertEqual(st["problems"], ["shop 4: unknown name 'Hickrane'"])

    def test_a_picture_match_needs_text_that_fits(self):
        pic = np.asarray(window.canonical_picture(self.image))
        self.assertEqual(reader.card_sprite_match(pic, 4)[0], "Kickrane")
        self.assertEqual(reader.name_by_card_sprite(pic, 4, "Hickrane"), "Kickrane")
        self.assertIsNone(reader.name_by_card_sprite(pic, 4, "Mosslug"))
        self.assertIsNone(reader.name_by_card_sprite(pic, 4, "Pickrage"))


class TextFitsName(unittest.TestCase):
    def test_rules(self):
        f = reader.text_fits_name
        self.assertTrue(f("Hickrane", "Kickrane"))          # one confident letter wrong
        self.assertTrue(f("?ickrane", "Kickrane"))          # one unknown letter
        self.assertTrue(f("Kickrane", "Kickrane"))
        self.assertTrue(f("Rare Candy", "Rare Candy"))
        self.assertFalse(f("Pickrage", "Kickrane"))         # two letters wrong
        self.assertFalse(f("Kickran", "Kickrane"))          # wrong length
        self.assertFalse(f("??c?rane", "Kickrane"))         # too little of it read
        self.assertFalse(f("", "Kickrane"))


class TypedFormsBecomeLessonText(unittest.TestCase):
    def test_forms(self):
        t = stop_lessons._truth
        self.assertEqual(t("shop0", "Recruiting Flyer $90"), "Recruiting Flyer|$90")
        self.assertEqual(t("shop0", "Cake  $ 0"), "Cake|$0")
        self.assertEqual(t("day", "day 9"), "DAY9")
        self.assertEqual(t("rank_bar", "Rank 4  67% 30% 3% 0% 0%"), "Rank4*67%*30%*3%*0%*0%")
        self.assertEqual(t("rank_bar", "rank max, 5% 10% 20% 40% 25%"), "RankMax*5%*10%*20%*40%*25%")
        self.assertEqual(t("trainer", "bug catcher"), "BUG CATCHER")
        self.assertEqual(t("money", "$ 316"), "$316")
        with self.assertRaises(ValueError):
            t("day", "nine")


if __name__ == "__main__":
    unittest.main()
