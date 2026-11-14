"""teach.py must add to the learned glyphs, never wipe them (it once deleted glyphs.json first)."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import glyphs  # noqa: E402
import teach  # noqa: E402


class TeachIsAdditive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "glyphs.json"
        seed = glyphs.GlyphStore(self.path)
        seed.add("title", "7", np.array([[1, 1, 1], [0, 0, 1], [0, 1, 0]], dtype=bool))
        seed.save()
        patch = mock.patch.object(glyphs, "TEMPLATE_FILE", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def run_teach(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            teach.main(**kw)
        return out.getvalue()

    def known(self):
        return {lab for lab, _ in glyphs.GlyphStore(self.path).fonts.get("title", [])}

    def test_existing_glyphs_survive_and_a_backup_is_kept(self):
        self.run_teach(lessons=[])
        self.assertEqual(self.known(), {"7"})
        self.assertTrue(self.path.with_name("glyphs.json.bak").exists())

    def test_rebuild_starts_empty_but_keeps_the_backup(self):
        self.run_teach(lessons=[], rebuild=True)
        self.assertEqual(self.known(), set())
        bak = glyphs.GlyphStore(self.path.with_name("glyphs.json.bak"))
        self.assertEqual({lab for lab, _ in bak.fonts["title"]}, {"7"})

    def test_a_missing_screenshot_is_skipped_not_fatal(self):
        text = self.run_teach(lessons=[{"file": "captures/this_does_not_exist.png"}])
        self.assertIn("skipped", text)
        self.assertEqual(self.known(), {"7"})


if __name__ == "__main__":
    unittest.main()
