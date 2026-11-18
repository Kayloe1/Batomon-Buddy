"""window.fit_to_canonical: putting the game window back to the reader's size.

Overnight on 2026-11-17 the monitor turned off; Windows moved the game to a 1920x1080
screen and shrank it to 1706x1000, so nothing could be read. These tests check the
decision part with a pretend window (no real window is touched).
"""
import unittest
from unittest import mock

import window
from window import Rect


class FitToCanonical(unittest.TestCase):
    def _run(self, client, monitors):
        calls = []
        now = {"rect": client}

        def set_pos(*a):
            calls.append(("pos",) + a)
            now["rect"] = Rect(a[2], a[3], a[4], a[5])

        patches = [
            mock.patch.object(window, "client_rect", side_effect=lambda h: now["rect"]),
            mock.patch.object(window, "is_minimized", return_value=False),
            mock.patch("win32api.EnumDisplayMonitors",
                       return_value=[(i, None, None) for i in range(len(monitors))]),
            mock.patch("win32api.GetMonitorInfo", side_effect=lambda i: {"Monitor": monitors[i]}),
            mock.patch.object(window.win32gui, "GetWindowLong", return_value=0x16cf0000),
            mock.patch.object(window.win32gui, "SetWindowLong", side_effect=lambda *a: calls.append(("style",) + a)),
            mock.patch.object(window.win32gui, "SetWindowPos", side_effect=set_pos),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return window.fit_to_canonical(1), calls

    def test_right_size_is_left_alone(self):
        for client in (Rect(0, 0, 2560, 1440), Rect(0, 0, 2560, 1600)):   # 2560x1600 = 16:9 picture plus bars
            msg, calls = self._run(client, [(0, 0, 2560, 1440)])
            self.assertIsNone(msg)
            self.assertEqual(calls, [])

    def test_shrunk_window_goes_to_the_big_monitor(self):
        msg, calls = self._run(Rect(4, 45, 1706, 1000), [(3840, 432, 5760, 1512), (0, 0, 2560, 1440)])
        self.assertIn("1706x1000", msg)
        self.assertIn("2560x1440", msg)
        pos = [c for c in calls if c[0] == "pos"][0]
        self.assertEqual(pos[3:7], (0, 0, 2560, 1440))

    def test_no_big_monitor_changes_nothing(self):
        msg, calls = self._run(Rect(4, 45, 1706, 1000), [(0, 0, 1920, 1080)])
        self.assertIsNone(msg)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
