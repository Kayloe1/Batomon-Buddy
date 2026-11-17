"""Mouse actions on the shop screen, each one checked against a fresh capture.

    hands = Hands(dry_run=True)       # dry_run: work out and log the clicks, send nothing
    result = hands.buy(2)             # Result(ok, message, before, after)

Rules built in:
- Besides the shop actions, this module presses the buttons of the other screens (BATTLE!,
  CONFIRM on the result box, events, gifts, trinkets, trainer pick, Continue, Cash In, the
  Stats window) and can start a run: start_run(mode) presses CONFIRM on the New Run dialog
  only after two fresh reads show that mode + Pantra. Ranked is used only when Ed asks for it
  (play.py --ranked). confirm_continue() uses the same two-read gate for a saved run's panel.
- Before a shop action the state must read cleanly (no "problems"); after it the
  screen is captured again and compared with what the action should have done.
  If anything is off, ok is False and the caller must stop.
- No click is sent while another window covers the game at that point (_ensure_visible).
- The fast-forward button is never clicked (the game remembers it between battles).
- pyautogui's fail-safe stays on: push the mouse into a screen corner to abort.
- Background mode (Hands(background=True), play.py --background): instead of moving the real
  mouse, the clicks, drags and wheel turns are posted straight to the game window as window
  messages, and the game is not brought to the front. Ed can keep using other windows meanwhile.
  This works because Godot (the game's engine) takes the pointer position from the message itself.
  The game must stay un-minimized; other windows may cover it. Hovering (inspect) is not supported
  there. The corner emergency stop still works. Every action is still checked by a fresh capture,
  so a message the game ignores stops the bot instead of going unnoticed.
- Every state, action and result is appended to logs/actions_<date>.jsonl.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

import reader
import window

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
CAPTURE_DIR = ROOT / "captures"

PARK = (2300, 640, 2340, 680)     # empty background: the pointer rests here, hovering nothing
SETTLE_S = 0.6                    # wait after an action before capturing again
ITEM_STILL_THERE = "the item card is still there"     # check_buy: an item click changed nothing


@dataclass
class Result:
    ok: bool
    message: str
    before: dict | None = None
    after: dict | None = None
    clicks: list = field(default_factory=list)
    battle: dict | None = None        # battle(): both teams as read at the start


# ------------------------------------------------- checks (pure functions) ----

def _slots(state: dict, where: str) -> list:
    return state[where]


def _owned_count(state: dict) -> int:
    return sum(s is not None for s in state["team"] + state["bench"])


def check_buy(before: dict, after: dict, i: int) -> str | None:
    """None if 'after' looks like 'before' plus buying shop slot i, else what is wrong."""
    card = before["shop"][i]
    if after.get("screen") != "shop":
        return f"screen became '{after.get('screen')}'"
    if after["money"] is None:
        return "money cannot be read after the click"
    if card["kind"] == "item":
        # items can pay out (Lucky Coin gave +$3), so only the card itself must be gone
        if after["money"] < before["money"] - card["price"]:
            return f"money went {before['money']} -> {after['money']}, more than the price"
        if after["shop"][i] == card:
            return ITEM_STILL_THERE
        return None
    if after["money"] != before["money"] - card["price"]:
        return f"money went {before['money']} -> {after['money']}, expected -{card['price']}"
    if card["kind"] == "monster":
        # one more monster owned, or the same number if it merged into a copy
        gained = _owned_count(after) - _owned_count(before)
        if gained not in (0, 1):
            return f"owned monsters changed by {gained}"
        if gained == 0 and after["team"] == before["team"] and after["bench"] == before["bench"]:
            return "nothing changed on team or bench"
    return None


def check_move(before: dict, after: dict, src: tuple[str, int], dst: tuple[str, int]) -> str | None:
    if after.get("screen") != "shop":
        return f"screen became '{after.get('screen')}'"
    if after["money"] != before["money"]:
        return "money changed during a move"
    moved = _slots(before, src[0])[src[1]]
    target = _slots(before, dst[0])[dst[1]]
    now_src = _slots(after, src[0])[src[1]]
    now_dst = _slots(after, dst[0])[dst[1]]
    if now_dst is None:
        return "target slot is still empty"
    if target is None and now_src is not None:
        return "source slot is still occupied"
    if target is not None and now_src is None and now_dst["level"] == target["level"]:
        return "source emptied but target did not level up"
    if target is None and (moved.get("name") != now_dst.get("name") or moved.get("level") != now_dst.get("level")):
        return "monster in the target slot has different name or level"
    return None


def check_sell(before: dict, after: dict, src: tuple[str, int]) -> str | None:
    if after.get("screen") != "shop":
        return f"screen became '{after.get('screen')}'"
    if _slots(after, src[0])[src[1]] is not None:
        return "slot is still occupied"
    # a monster bought for $0 (Bug Catcher's first Bug of the day) sells for $0
    # so equal money is fine as long as one monster really left (a mis-drag only moves it)
    if after["money"] < before["money"]:
        return f"money went down ({before['money']} -> {after['money']})"
    if after["money"] == before["money"] and _owned_count(after) != _owned_count(before) - 1:
        return f"money did not go up ({before['money']} -> {after['money']}) and no monster left"
    return None


def check_reroll(before: dict, after: dict) -> str | None:
    if after.get("screen") != "shop":
        return f"screen became '{after.get('screen')}'"
    if after["money"] != before["money"] - before["reroll_price"]:
        return f"money went {before['money']} -> {after['money']}, expected -{before['reroll_price']}"
    return None


def unnamed_owned(state: dict) -> int:
    """How many team and bench monsters on a shop read have no name."""
    return sum(1 for key in ("team", "bench") for m in (state.get(key) or [])
               if m and m.get("name") is None)


def _read_rank(state: dict) -> tuple[int, int, int]:
    """Order reads of one moment: recognized screen first, fewest problems, then fewest unnamed."""
    screen = state.get("screen")
    unrecognized = 1 if screen in (None, "unknown", "blank") else 0
    problems = len(state.get("problems") or [])
    unnamed = unnamed_owned(state) if screen == "shop" else 0
    return (unrecognized, problems, unnamed)


# ------------------------------------------------------------------ hands ----

class _WindowMouse:
    """Mouse input posted to one window as messages: the real pointer never moves and the window
    need not be in front. Positions are screen pixels, as everywhere else in Hands."""
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEWHEEL = 0x0200, 0x0201, 0x0202, 0x020A
    MK_LBUTTON = 0x0001

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.held = False

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return ((y & 0xFFFF) << 16) | (x & 0xFFFF)

    def _post(self, msg: int, wparam: int, lparam: int) -> None:
        import win32api
        win32api.PostMessage(self.hwnd, msg, wparam, lparam)

    def _client(self, x: int, y: int) -> tuple[int, int]:
        r = window.client_rect(self.hwnd)
        return x - r.left, y - r.top

    def move(self, x: int, y: int, duration: float = 0.0, steps: int = 1) -> None:
        """Pointer to screen point (x, y); with steps > 1 the path is walked in that many moves."""
        cx, cy = self._client(x, y)
        start = getattr(self, "_at", (cx, cy))
        steps = max(1, steps)
        for k in range(1, steps + 1):
            px = round(start[0] + (cx - start[0]) * k / steps)
            py = round(start[1] + (cy - start[1]) * k / steps)
            self._post(self.WM_MOUSEMOVE, self.MK_LBUTTON if self.held else 0, self._lparam(px, py))
            time.sleep(duration / steps)
        self._at = (cx, cy)

    def down(self) -> None:
        self.held = True
        self._post(self.WM_LBUTTONDOWN, self.MK_LBUTTON, self._lparam(*self._at))

    def up(self) -> None:
        self.held = False
        self._post(self.WM_LBUTTONUP, 0, self._lparam(*self._at))

    def wheel(self, x: int, y: int, delta: int) -> None:
        """One wheel turn of `delta` units (120 = one notch) at screen point (x, y)."""
        self._post(self.WM_MOUSEWHEEL, (delta & 0xFFFF) << 16, self._lparam(x, y))


def _emergency_check() -> None:
    """The corner emergency stop, for background mode where pyautogui itself never runs."""
    import pyautogui
    pyautogui.failSafeCheck()


class Hands:
    background = False                # see the module notes; set by __init__
    mouse = None

    def __init__(self, dry_run: bool = True, background: bool = False):
        self.dry_run = dry_run
        self.background = background
        self.hwnd = window.find_window()
        if self.hwnd is None:
            raise RuntimeError("Batomon Showdown window not found")
        self.mouse = _WindowMouse(self.hwnd) if background else None
        LOG_DIR.mkdir(exist_ok=True)
        self.log_path = LOG_DIR / f"actions_{datetime.now():%Y%m%d}.jsonl"

    # -- seeing --
    def look(self, save_if_unreadable: bool = True) -> dict:
        if window.is_minimized(self.hwnd):
            return {"screen": "minimized", "problems": ["game window is minimized"]}
        # A shine, fade, or idle animation can spoil one frame on any screen (shop
        # card text, Runerock's sprite, or an event/trinket/unknown frame), so any
        # unreadable or unrecognized read is retried across a few snapshots and the
        # cleanest read is kept.
        attempts = 1 if getattr(self, "_single_read", False) else 4
        best = None
        for attempt in range(attempts):
            # background: only the window's own drawing; screen pixels would show whatever covers it
            img = (window.capture_client(self.hwnd, "printwindow") if self.background
                   else window.capture_client(self.hwnd))
            state = reader.read_state(img)
            if best is None or _read_rank(state) < _read_rank(best[1]):
                best = (img, state)
            screen = state.get("screen")
            unrecognized = screen in (None, "unknown", "blank")
            need_unnamed = screen == "shop" and unnamed_owned(state) and attempt < 2
            if not unrecognized and not state.get("problems") and not need_unnamed:
                break
            if attempt + 1 < attempts:
                time.sleep(0.45)
        img, state = best
        self.last_image = img
        if save_if_unreadable and state.get("problems"):
            CAPTURE_DIR.mkdir(exist_ok=True)
            path = CAPTURE_DIR / f"unread_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
            img.save(path)
            state["saved_capture"] = path.name
        return state

    def _log(self, action: str, result: Result) -> Result:
        rec = {"time": datetime.now().isoformat(timespec="seconds"), "action": action,
               "dry_run": self.dry_run, "ok": result.ok, "message": result.message,
               "clicks": result.clicks, "before": result.before, "after": result.after}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return result

    # -- pointing --
    def screen_point(self, box: reader.Box) -> tuple[int, int]:
        fx, fy = reader.box_to_frac(box)
        return window.frac_to_screen(window.client_rect(self.hwnd), fx, fy)

    def _focus(self) -> None:
        """Bring the game window to the front, or raise if that fails."""
        import win32api
        import win32gui
        import win32process
        if win32gui.GetForegroundWindow() == self.hwnd:
            return
        # Windows only lets the thread that owns the foreground window hand focus
        # over, so attach to it for the duration of the call.
        fg = win32gui.GetForegroundWindow()
        me = win32api.GetCurrentThreadId()
        other = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        attached = False
        try:
            if other and other != me:
                try:
                    win32process.AttachThreadInput(me, other, True)
                    attached = True
                except Exception:
                    pass
            try:
                win32gui.BringWindowToTop(self.hwnd)
                win32gui.SetForegroundWindow(self.hwnd)
            except Exception:
                pass
        finally:
            if attached:
                win32process.AttachThreadInput(me, other, False)
        time.sleep(0.3)
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError("could not bring the game window to the front; click on the game once and retry")

    def _covering(self, x: int, y: int) -> str | None:
        """None when the screen point (x, y) shows the game window, else the title of the window
        that covers it there."""
        import win32gui
        top = win32gui.WindowFromPoint((x, y))
        root = win32gui.GetAncestor(top, 2) if top else 0          # GA_ROOT: the top-level window
        if root == self.hwnd:
            return None
        return win32gui.GetWindowText(root) or "another window"

    def _ensure_visible(self, *points: tuple[int, int]) -> None:
        """Refuse to send input where another window covers the game.

        Mouse input goes to whatever window is on top at that point. Bringing the game to the front
        is not enough when a window is kept always on top (2026-11-13: the Claude app covered the
        Stats window's close button, and the click landed on Claude). Nothing is clicked then."""
        for x, y in points:
            cover = self._covering(x, y)
            if cover is not None:
                raise RuntimeError(
                    f"The window '{cover}' covers the game where the bot has to click. Nothing was clicked. "
                    f"Move it off the game picture (or stop it staying on top), then press Start again.")

    def _click(self, box: reader.Box) -> list:
        x, y = self.screen_point(box)
        if not self.dry_run and self.background:
            _emergency_check()
            m = self.mouse
            m.move(x, y, duration=0.15, steps=4)
            m.down()
            time.sleep(0.06)
            m.up()
            m.move(*self.screen_point(PARK), duration=0.15, steps=4)
        elif not self.dry_run:
            import pyautogui
            self._focus()
            self._ensure_visible((x, y))
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.mouseDown()
            time.sleep(0.06)
            pyautogui.mouseUp()
            pyautogui.moveTo(*self.screen_point(PARK), duration=0.15)
        return [["click", x, y]]

    def inspect(self, where: str, i: int) -> dict | None:
        """Hover a team/bench monster and read the info panel that slides in on the right
        (name, rarity, type, cooldown, active numbers). The panel picture is saved in
        captures/info_panels/ so its passive can be read by a person. None if no panel showed."""
        import numpy as np
        box = reader.team_slot(i) if where == "team" else reader.bench_slot(i)
        if self.dry_run:
            return None
        if self.background:
            raise RuntimeError("hovering to read the info panel needs the real mouse; run without background mode")
        import pyautogui
        self._focus()
        self._ensure_visible(self.screen_point(box))
        pyautogui.moveTo(*self.screen_point(box), duration=0.15)
        time.sleep(0.8)
        self.look(save_if_unreadable=False)
        pic = np.asarray(window.canonical_picture(self.last_image))
        pyautogui.moveTo(*self.screen_point(PARK), duration=0.15)
        panel = reader.read_info_panel(pic)
        if panel is not None:
            folder = CAPTURE_DIR / "info_panels"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{panel.get('name') or 'unread'}_{datetime.now():%Y%m%d_%H%M%S}.png"
            Image.fromarray(pic).save(path)
            panel["capture"] = str(path.relative_to(CAPTURE_DIR.parent)).replace("\\", "/")
            panel["slot_rgb"] = reader.crop(pic, box)
        return panel

    def _drag(self, src: reader.Box, dst: reader.Box) -> list:
        x0, y0 = self.screen_point(src)
        x1, y1 = self.screen_point(dst)
        if not self.dry_run and self.background:
            _emergency_check()
            m = self.mouse
            m.move(x0, y0, duration=0.15, steps=4)
            m.down()
            time.sleep(0.12)
            m.move(x1, y1, duration=0.35, steps=12)
            time.sleep(0.12)
            m.up()
            m.move(*self.screen_point(PARK), duration=0.15, steps=4)
        elif not self.dry_run:
            import pyautogui
            self._focus()
            self._ensure_visible((x0, y0), (x1, y1))
            pyautogui.moveTo(x0, y0, duration=0.15)
            pyautogui.mouseDown()
            time.sleep(0.12)
            pyautogui.moveTo(x1, y1, duration=0.35)
            time.sleep(0.12)
            pyautogui.mouseUp()
            pyautogui.moveTo(*self.screen_point(PARK), duration=0.15)
        return [["drag", x0, y0, x1, y1]]

    def _scroll(self, box: reader.Box, clicks: int) -> list:
        """Turn the mouse wheel over a box (negative = scroll down)."""
        x, y = self.screen_point(box)
        if not self.dry_run and self.background:
            _emergency_check()
            self.mouse.move(x, y, duration=0.15, steps=4)
            step = 120 if clicks > 0 else -120
            for _ in range(abs(clicks)):
                self.mouse.wheel(x, y, step)
                time.sleep(0.04)
            time.sleep(0.3)
        elif not self.dry_run:
            import pyautogui
            self._focus()
            self._ensure_visible((x, y))
            pyautogui.moveTo(x, y, duration=0.15)
            # pyautogui on Windows passes the number straight to mouse_event as wheel units,
            # and one wheel notch is 120 units: scroll(-15) was 1/8 of a notch and the game
            # ignored it. Send whole notches, one event each.
            step = 120 if clicks > 0 else -120
            for _ in range(abs(clicks)):
                pyautogui.scroll(step)
                time.sleep(0.04)
            time.sleep(0.3)
        return [["scroll", x, y, clicks]]

    # -- the common shape of every action --
    def _act(self, name: str, precheck, do, check) -> Result:
        before = self.look()
        if before.get("screen") != "shop":
            return self._log(name, Result(False, f"not on the shop screen ({before.get('screen')})", before))
        if before["problems"]:
            return self._log(name, Result(False, "state not read cleanly: " + "; ".join(before["problems"]), before))
        why = precheck(before)
        if why:
            return self._log(name, Result(False, why, before))
        clicks = do()
        if self.dry_run:
            return self._log(name, Result(True, "dry run: nothing sent", before, None, clicks))
        time.sleep(SETTLE_S)
        after = self.look(save_if_unreadable=False)
        if after.get("screen") != "shop":
            # a third copy makes a monster level up, and a gift box slides in over the shop
            after = self.wait_for({"shop", "gift_box"}, 8)
        elif after.get("problems"):
            after = self.look()           # read again, saving the capture this time
        if after.get("screen") == "gift_box":
            return self._log(name, Result(True, "ok: a monster leveled up, gift box showing", before, after, clicks))
        if after.get("problems"):
            # an unreadable state cannot be checked; report that, not a guess
            why = "state after the action not read cleanly: " + "; ".join(after["problems"])
        else:
            why = check(before, after)
        return self._log(name, Result(why is None, why or "ok", before, after, clicks))

    @staticmethod
    def _slot_box(where: tuple[str, int]) -> reader.Box:
        kind, i = where
        return reader.team_slot(i) if kind == "team" else reader.bench_slot(i)

    # -- actions --
    def buy(self, i: int) -> Result:
        """Click shop card i: buys a monster, or uses an item."""
        def pre(s):
            card = s["shop"][i]
            if card is None:
                return f"shop slot {i} is empty"
            if card["price"] > s["money"]:
                return f"{card['name']} costs ${card['price']}, only ${s['money']}"
            if card["kind"] == "monster" and reader_free_slots(s) == 0 and not _has_copy(s, card["name"]):
                return "no free team or bench slot"
            return None
        result = self._act(f"buy {i}", pre, lambda: self._click(reader.shop_card(i)),
                           lambda b, a: check_buy(b, a, i))
        if result.ok and result.after and result.after.get("screen") == "shop":
            self._learn_new_sprite(result.before, result.after, i)
        return result

    def _learn_new_sprite(self, before: dict, after: dict, i: int) -> None:
        """Team slots show no name. Right after a buy we know which monster the
        newly filled slot holds, so save its sprite under that name."""
        import numpy as np
        card = before["shop"][i]
        if card["kind"] != "monster":
            return
        new = [(kind, k) for kind in ("team", "bench") for k in range(len(before[kind]))
               if before[kind][k] is None and after[kind][k] is not None]
        if len(new) != 1:
            return
        kind, k = new[0]
        slot = after[kind][k]
        if slot["name"] is None:
            pic = np.asarray(window.canonical_picture(self.last_image))
            # a Shiny card's sprite is saved as "Name@shiny" so it never becomes a normal template
            learn_as = card["name"] + (reader.SHINY_SUFFIX if card.get("shiny") else "")
            reader.learn_sprite(learn_as, reader.crop(pic, self._slot_box(new[0])))
            slot["name"] = card["name"]
            if card.get("shiny"):
                slot["shiny"] = True
        elif slot["name"] != card["name"]:
            raise RuntimeError(f"bought {card['name']} but the sprite in {kind} {k} matches {slot['name']}")

    use_item = buy

    def move(self, src: tuple[str, int], dst: tuple[str, int]) -> Result:
        """Drag a monster, e.g. move(("team", 4), ("bench", 0))."""
        def pre(s):
            if _slots(s, src[0])[src[1]] is None:
                return f"{src[0]} slot {src[1]} is empty"
            return None
        return self._act(f"move {src} -> {dst}", pre,
                         lambda: self._drag(self._slot_box(src), self._slot_box(dst)),
                         lambda b, a: check_move(b, a, src, dst))

    def sell(self, src: tuple[str, int]) -> Result:
        """Drag a monster onto the shop bar."""
        def pre(s):
            if _slots(s, src[0])[src[1]] is None:
                return f"{src[0]} slot {src[1]} is empty"
            return None
        return self._act(f"sell {src}", pre,
                         lambda: self._drag(self._slot_box(src), reader.SELL_BAR),
                         lambda b, a: check_sell(b, a, src))

    def reroll(self) -> Result:
        def pre(s):
            if s["reroll_price"] > s["money"]:
                return f"reroll costs ${s['reroll_price']}, only ${s['money']}"
            return None
        return self._act("reroll", pre, lambda: self._click(reader.REROLL_BUTTON), check_reroll)

    def lock(self) -> Result:
        """Press LOCK / UNLOCK. Locking puts a padlock on every shop card (it keeps
        the shop for the next day) and the button then reads UNLOCK."""
        def check(b, a):
            if a.get("screen") != "shop":
                return f"screen became '{a.get('screen')}'"
            if a["locked"] == b["locked"]:
                return "lock state did not change"
            return None
        return self._act("lock", lambda s: None, lambda: self._click(reader.LOCK_BUTTON), check)

    # -- screens after the shop --
    def wait_for(self, wanted: set[str], timeout: float = 120, unknown_limit: float = 25) -> dict:
        """Watch the game until one of the wanted screens shows (twice in a row).

        The fast-forward button is never touched: the game remembers it between battles
        and restarts, and it shows grey for about a second at the start of every battle,
        so a click on a "grey" button turned it off (Ed, 2026-11-14). Gives up at the
        timeout, or when an unrecognised screen stays for unknown_limit seconds
        (fades and the VS animation are shorter); the capture is then saved.
        """
        import numpy as np
        start = time.time()
        unknown_since = None
        hits = 0
        state: dict = {}
        while time.time() - start < timeout:
            prev_single = getattr(self, "_single_read", False)
            self._single_read = True
            try:
                state = self.look(save_if_unreadable=False)
            finally:
                self._single_read = prev_single
            screen = state["screen"]
            if screen == "battle":
                # keep an early frame of the battle: both teams with their starting badges
                self._battle_frames = getattr(self, "_battle_frames", 0) + 1
                if self._battle_frames == 2:
                    self.battle_start_image = self.last_image
            if screen in wanted:
                hits += 1
                if hits >= 2:
                    return self.look()
            else:
                hits = 0
            if screen == "unknown":
                unknown_since = unknown_since or time.time()
                if time.time() - unknown_since > unknown_limit:
                    break
            else:
                unknown_since = None
            time.sleep(0.4)
        path = CAPTURE_DIR / f"stuck_{datetime.now():%Y%m%d_%H%M%S}.png"
        self.last_image.save(path)
        state["saved_capture"] = path.name
        state.setdefault("problems", []).append(
            f"waited for {sorted(wanted)}, stuck on '{state.get('screen')}'")
        return state

    def _press_and_wait(self, name: str, on: str, box: reader.Box, wanted: set[str], timeout: float) -> Result:
        before = self.look()
        if before.get("screen") != on:
            return self._log(name, Result(False, f"not on the {on} screen ({before.get('screen')})", before))
        if before["problems"]:
            return self._log(name, Result(False, "state not read cleanly: " + "; ".join(before["problems"]), before))
        clicks = self._click(box)
        if self.dry_run:
            return self._log(name, Result(True, "dry run: nothing sent", before, None, clicks))
        after = self.wait_for(wanted, timeout)
        ok = after.get("screen") in wanted
        return self._log(name, Result(ok, f"now on '{after.get('screen')}'", before, after, clicks))

    def battle(self) -> Result:
        """Press BATTLE! and watch until the result box shows.

        An early frame of the battle is kept and read after the battle (reading both teams
        takes a few seconds): result.battle holds both teams, as reader.read_battle()
        returns them, and where the start and result captures were saved.
        """
        self._battle_frames = 0
        self.battle_start_image = None
        r = self._press_and_wait("battle", "shop", reader.BATTLE_BUTTON, {"result"}, 300)
        if not self.dry_run:
            r.battle = self._record_battle(r.ok)
        return r

    def _record_battle(self, reached_result: bool) -> dict:
        folder = CAPTURE_DIR / "battles"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        out: dict = {"start_capture": None, "result_capture": None}
        if reached_result and self.last_image is not None:
            path = folder / f"{stamp}_result.png"
            self.last_image.save(path)
            out["result_capture"] = str(path.relative_to(ROOT))
        img = self.battle_start_image
        if img is None:
            out["problems"] = ["no battle frame was seen"]
            return out
        path = folder / f"{stamp}_start.png"
        img.save(path)
        out["start_capture"] = str(path.relative_to(ROOT))
        import numpy as np
        try:
            out.update(reader.read_battle(np.asarray(window.canonical_picture(img))))
        except Exception as e:     # a reading bug must not stop the run; the capture is kept
            out["problems"] = [f"battle screen not read: {type(e).__name__}: {e}"]
        return out

    def read_battle_stats(self) -> dict:
        """On the result box: open Stats, read both teams' numbers, close it again.

        Best effort: reading problems come back in "problems" and the run goes on. Only a
        window that will not close is reported in "stuck" (the caller must stop then).
        Both captures are kept in captures/battles/.
        """
        import numpy as np
        out: dict = {"problems": [], "stuck": False}
        if self.dry_run:
            return out
        pic = np.asarray(window.canonical_picture(self.last_image)) if self.last_image is not None else None
        if pic is None or reader.detect_screen(pic) != "result":
            before = self.look()
            if before.get("screen") != "result":
                out["problems"].append(f"stats: not on the result screen ({before.get('screen')})")
                return out
            pic = np.asarray(window.canonical_picture(self.last_image))
        self._click(reader.RESULT_STATS)
        state = self.wait_for({"stats"}, 10)
        if state.get("screen") != "stats":
            out["problems"].append(f"stats: window did not open ({state.get('screen')})")
            return out
        folder = CAPTURE_DIR / "battles"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        top = self.last_image
        top.save(folder / f"{stamp}_stats_top.png")
        scrolled = None
        try:
            self._scroll(reader.STATS_SCROLL_POINT, -15)
            time.sleep(0.5)
            self.look(save_if_unreadable=False)
            if reader.is_stats_window(np.asarray(window.canonical_picture(self.last_image))):
                scrolled = self.last_image
                scrolled.save(folder / f"{stamp}_stats_bottom.png")
            else:
                self.last_image.save(folder / f"{stamp}_stats_not_scrolled.png")
                out["problems"].append("stats: the scrolled picture is not the Stats window")
        except Exception as e:
            out["problems"].append(f"stats: scrolling failed: {type(e).__name__}: {e}")
        # Close Stats before running the slower OCR/sprite read so the UI is unblocked immediately.
        self._click(reader.STATS_CLOSE)
        after = self.wait_for({"result"}, 10)
        if after.get("screen") != "result":
            out["stuck"] = True
            out["problems"].append(f"stats: window did not close ({after.get('screen')})")
        try:
            out["end_hp_fraction"] = reader.read_result_hp(pic)
        except Exception as e:
            out["problems"].append(f"end HP not read: {type(e).__name__}: {e}")
        try:
            canon = lambda im: np.asarray(window.canonical_picture(im))
            out.update(reader.read_stats(canon(top), canon(scrolled) if scrolled is not None else None))
        except Exception as e:
            out["problems"].append(f"stats not read: {type(e).__name__}: {e}")
        out["captures"] = [f"captures/battles/{stamp}_stats_top.png"] + (
            [f"captures/battles/{stamp}_stats_bottom.png"] if scrolled is not None else [])
        return out

    def close_stats(self) -> Result:
        """Close a Stats window left open (an earlier session stopped while it showed)."""
        return self._press_and_wait("close stats", "stats", reader.STATS_CLOSE, {"result"}, 10)

    def confirm_result(self) -> Result:
        """Press CONFIRM on the result box; the next shop, an event or the end of the run follows."""
        return self._press_and_wait("confirm result", "result", reader.RESULT_CONFIRM,
                                    {"shop", "event", "run_over", "gift_box", "trinket_pick", "cash_in", "overflow",
                                     "rank_result"}, 40)

    def confirm_rank(self) -> Result:
        """Press Confirm under the Ranked rank card at the end of a run; the end-of-run screen follows."""
        return self._press_and_wait("confirm rank", "rank_result", reader.RANK_CONFIRM,
                                    {"run_over", "rank_result", "main_menu", "new_run"}, 30)

    def select_target(self, where: tuple[str, int]) -> Result:
        """On "Select a target": click the monster in ("team"|"bench", i), then Confirm.

        Confirm is pressed only once the game has enabled it (it turns red after a valid click).
        """
        name = f"select target {where}"
        before = self.look()
        if before.get("screen") != "select_target":
            return self._log(name, Result(False, f"not on the target screen ({before.get('screen')})", before))
        slots = before["team"] if where[0] == "team" else before["bench"]
        if slots[where[1]] is None:
            return self._log(name, Result(False, f"no monster in {where}", before))
        box = reader.target_team_slot(where[1]) if where[0] == "team" else reader.target_bench_slot(where[1])
        clicks = self._click(box)
        if self.dry_run:
            return self._log(name, Result(True, "dry run: nothing sent", before, None, clicks))
        state = before
        for _ in range(10):
            time.sleep(0.4)
            state = self.look(save_if_unreadable=False)
            if state.get("screen") != "select_target" or state.get("confirm_enabled"):
                break
        if state.get("screen") == "select_target" and not state.get("confirm_enabled"):
            return self._log(name, Result(False, "Confirm stayed grey after clicking the monster", before, state, clicks))
        if state.get("screen") == "select_target":
            clicks += self._click(reader.TARGET_CONFIRM)
        wanted = {"shop", "event", "gift_box", "trinket_pick", "cash_in", "run_over", "overflow", "rank_result"}
        after = self.wait_for(wanted, 40)
        return self._log(name, Result(after.get("screen") in wanted, f"now on '{after.get('screen')}'", before, after, clicks))

    def leave_title(self) -> Result:
        """Click on the title screen ("press any key to start"); the main menu follows.

        Right after the game starts, the title ignores clicks until its intro ends and the label shows
        (2026-11-13: one click, then 20 s on the title). So click again, up to four times."""
        wanted = {"main_menu", "main_menu_continue", "shop", "new_run"}
        r = self._press_and_wait("leave title", "title", reader.TITLE_ANYWHERE, wanted, 8)
        for _ in range(3):
            if r.ok or self.dry_run or (r.after or {}).get("screen") != "title":
                break
            r = self._press_and_wait("leave title", "title", reader.TITLE_ANYWHERE, wanted, 8)
        return r

    def continue_run(self) -> Result:
        """Press Continue on the main menu (a run is open); the run's current screen follows.

        New Run is never pressed on this menu: it would abandon the run that is open.
        """
        return self._press_and_wait("continue run", "main_menu_continue", reader.MAIN_MENU_CONTINUE,
                                    {"continue_confirm", "shop", "result", "event", "gift_box", "trinket_pick",
                                     "cash_in", "trainer_pick", "battle", "matchmaking"}, 30)

    def confirm_continue(self, allow_ranked: bool = False) -> Result:
        """Press Confirm on the panel that follows Continue.

        Same gate as start_run(): two fresh captures in a row must both show the panel with the
        exact "CASUAL MODE" label, or, when allow_ranked (Ed ticked Ranked), the exact saved
        "RANKED MODE" label. Anything else (an unreadable label, a Ranked label without
        allow_ranked, a Ranked label never saved) is refused and nothing is clicked.
        """
        name = "confirm continue"
        before = self.look()
        if before.get("screen") != "continue_confirm":
            return self._log(name, Result(False, f"not on the continue panel ({before.get('screen')})", before))
        if self.dry_run:
            return self._log(name, Result(True, "dry run: nothing sent", before))
        state = before
        for _ in range(2):
            time.sleep(0.3)
            state = self.look()
            allowed = ("casual", "ranked") if allow_ranked else ("casual",)
            if not (state.get("screen") == "continue_confirm" and state.get("mode") in allowed
                    and not state["problems"]):
                shown = " or ".join(m.upper() + " MODE" for m in allowed)
                return self._log(name, Result(
                    False, f"NOT confirmed: the saved run is not shown as {shown} "
                           f"(mode={state.get('mode')}, screen={state.get('screen')})", before, state))
        clicks = self._click(reader.CONTINUE_CONFIRM)
        wanted = {"shop", "result", "event", "gift_box", "trinket_pick", "cash_in", "trainer_pick",
                  "battle", "matchmaking", "overflow"}
        after = self.wait_for(wanted, 40)
        ok = after.get("screen") in wanted
        return self._log(name, Result(ok, f"{state.get('mode', 'casual').capitalize()} run continued; "
                                          f"now on '{after.get('screen')}'", before, after, clicks))

    def cash_in(self) -> Result:
        """Press "Cash In" on a trinket dialog (e.g. Fake Diamond after a loss: gain money)."""
        return self._press_and_wait("cash in", "cash_in", reader.CASH_IN_BUTTON,
                                    {"shop", "event", "run_over", "gift_box", "trinket_pick", "cash_in", "overflow",
                                     "rank_result", "trainer_ability"}, 40)

    def start_casual_run(self) -> Result:
        """Start a new CASUAL Pantra run (see start_run)."""
        return self.start_run("casual")

    def start_ranked_run(self) -> Result:
        """Start a new RANKED Pantra run (see start_run). Only called when Ed ticked Ranked."""
        return self.start_run("ranked")

    def start_run(self, mode: str) -> Result:
        """Start a new Pantra run in `mode` ("casual" or "ranked") from the end-of-run screen or
        the New Run dialog.

        Ed allowed Ranked runs on 2026-11-14, but only when he asks for them (play.py --ranked,
        the control window's Ranked choice). CONFIRM is pressed only when two fresh captures in a
        row both show the wanted mode selected (blue), the other one not selected (grey) and Pantra
        selected. Anything unclear stops without pressing CONFIRM.
        """
        if mode not in ("casual", "ranked"):
            raise ValueError(f"unknown mode {mode!r}")
        label = mode.capitalize()
        name = f"start {mode} run"
        before = self.look()
        if before["screen"] == "main_menu":
            self._click(reader.MAIN_MENU_NEW_RUN)
            if self.dry_run:
                return self._log(name, Result(True, "dry run: nothing sent", before))
            state = self.wait_for({"new_run"}, 15)
        elif before["screen"] == "run_over":
            self._click(reader.RUN_OVER_NEW_RUN)
            if self.dry_run:
                return self._log(name, Result(True, "dry run: nothing sent", before))
            state = self.wait_for({"new_run"}, 15)
        else:
            state = before
        if state["screen"] != "new_run":
            return self._log(name, Result(False, f"New Run dialog not showing ({state['screen']})", before, state))
        if self.dry_run:
            return self._log(name, Result(True, "dry run: nothing sent", before))

        if state.get("region") != "Pantra":
            self._click(reader.NEW_RUN_PANTRA)
            time.sleep(SETTLE_S)
            state = self.look()
        if state.get("screen") == "new_run" and state.get("mode") != mode:
            self._click(reader.NEW_RUN_RANKED if mode == "ranked" else reader.NEW_RUN_CASUAL)
            time.sleep(SETTLE_S)
            state = self.look()

        # the gate: two independent reads, both must be clean <mode> + Pantra
        for _ in range(2):
            time.sleep(0.3)
            state = self.look()
            if not (state.get("screen") == "new_run" and state.get("mode") == mode
                    and state.get("region") == "Pantra" and not state["problems"]):
                return self._log(name, Result(
                    False, f"NOT confirmed: wanted {label}, read mode={state.get('mode')}, "
                           f"region={state.get('region')}, screen={state.get('screen')}", before, state))
        clicks = self._click(reader.NEW_RUN_CONFIRM)
        after = self.wait_for({"trainer_pick", "shop"}, 30)
        ok = after.get("screen") in ("trainer_pick", "shop")
        return self._log(name, Result(ok, f"{label} run confirmed; now on '{after.get('screen')}'", before, after, clicks))

    def pick_trainer(self, k: int) -> Result:
        """Click trainer card k (0 = left) on the trainer pick screen; the day 1 shop follows."""
        return self._press_and_wait(f"pick trainer {k}", "trainer_pick", reader.trainer_card(k), {"shop"}, 40)

    def open_gift(self) -> Result:
        """Click the gift box that appears when a monster levels up; the trinket choice follows."""
        return self._press_and_wait("open gift", "gift_box", reader.GIFT_BOX, {"trinket_pick"}, 20)

    def confirm_stolen(self) -> Result:
        """"Stolen!" (Burglar): press Confirm under the stolen trinket."""
        return self._press_and_wait("confirm stolen trinket", "trinket_stolen", reader.STOLEN_CONFIRM,
                                    {"shop", "gift_box", "overflow", "event", "cash_in", "trainer_ability"}, 20)

    def pick_trainer_ability(self, k: int) -> Result:
        """"Choose a trainer to gain their ability" (Mysterious Mask): click trainer card k (0 = left)."""
        return self._press_and_wait(f"pick trainer ability {k}", "trainer_ability", reader.trainer_ability_card(k),
                                    {"shop", "gift_box", "overflow", "event", "cash_in", "trinket_pick"}, 20)

    def pick_trinket(self, k: int) -> Result:
        """Click trinket card k (0 = left). The shop comes back, or another gift."""
        # k counts the cards on show; a single card sits in the middle place (2026-11-13)
        state = self.look()
        places = state.get("card_places") or [0, 1, 2]
        place = places[k] if k < len(places) else k
        # a new day's monster (Swim Coach: a Water monster each day) can arrive with team and bench full
        return self._press_and_wait(f"pick trinket {k}", "trinket_pick", reader.trinket_card(place),
                                    {"shop", "gift_box", "overflow", "trainer_ability", "trinket_stolen"}, 20)

    def take_all_trinkets(self) -> Result:
        """Press "Take All" under the trinket cards: every offered trinket is kept."""
        state = self.look()
        if state.get("screen") == "trinket_pick" and not state.get("take_all"):
            return self._log("take all trinkets", Result(False, "no Take All button on this trinket choice", state))
        return self._press_and_wait("take all trinkets", "trinket_pick", reader.TAKE_ALL,
                                    {"shop", "gift_box", "overflow", "event", "cash_in", "trainer_ability",
                                     "trinket_stolen"}, 20)

    def cancel_take_one(self) -> Result:
        """Press Cancel on "Are you sure you want to take one trinket rather than all?"."""
        return self._press_and_wait("cancel take one", "take_one_confirm", reader.TAKE_ONE_CANCEL,
                                    {"trinket_pick"}, 15)

    def choose_event(self, k: int) -> Result:  # may be followed by "select_target"
        """Click choice k (0 = top) on an event screen."""
        import numpy as np
        before = self.look()
        if before.get("screen") != "event":
            return self._log("event", Result(False, f"not on an event screen ({before.get('screen')})", before))
        boxes = reader.event_choices(np.asarray(window.canonical_picture(self.last_image)))
        if not 0 <= k < len(boxes):
            return self._log("event", Result(False, f"no choice {k} (there are {len(boxes)})", before))
        return self._press_and_wait(f"event choice {k}", "event", boxes[k],
                                    {"shop", "event", "gift_box", "trinket_pick", "select_target", "cash_in",
                                     "overflow"}, 40)

    def resolve_overflow(self, which: str | int) -> Result:
        """On the OVERFLOW screen: sell bench monster `which` (0-3) or the waiting one ("overflow")."""
        name = f"overflow: sell {which}"
        before = self.look()
        if before.get("screen") != "overflow":
            return self._log(name, Result(False, f"not on the overflow screen ({before.get('screen')})", before))
        if which == "overflow":
            box = reader.OVERFLOW_SLOT
        else:
            if before["bench"][which] is None:
                return self._log(name, Result(False, f"bench slot {which} is empty", before))
            box = reader.bench_slot(which)
        clicks = self._drag(box, reader.SELL_BAR)
        if self.dry_run:
            return self._log(name, Result(True, "dry run: nothing sent", before, None, clicks))
        # Usually the shop follows. But a second monster can be waiting (2026-11-15: an event's Shiny,
        # then Berroon): the overflow screen comes back with other monsters in it. That is a sale done
        # too, once two reads in a row agree that the bench or the waiting monster changed.
        start, seen, after = time.time(), None, before
        while time.time() - start < 20:
            after = self.look(save_if_unreadable=False)
            if after.get("screen") == "shop":
                after = self.wait_for({"shop"}, 10)
                break
            sig = _overflow_signature(after) if after.get("screen") == "overflow" else None
            if sig is not None and sig != _overflow_signature(before) and not after.get("problems"):
                if sig == seen:
                    return self._log(name, Result(True, "sold; another monster is waiting in the overflow slot",
                                                  before, after, clicks))
                seen = sig
            else:
                seen = None
            time.sleep(0.5)
        return self._log(name, Result(after.get("screen") == "shop", f"now on '{after.get('screen')}'",
                                      before, after, clicks))


def _overflow_signature(state: dict) -> tuple:
    """What the overflow screen holds: the bench and the waiting monster (name, level, stats)."""
    def one(m):
        return None if not m else (m.get("name"), m.get("level"), json.dumps(m.get("stats"), sort_keys=True))
    return tuple(one(m) for m in (state.get("bench") or [])) + (one(state.get("overflow")),)


def reader_free_slots(state: dict) -> int:
    return sum(s is None for s in state["team"] + state["bench"])


def _has_copy(state: dict, name: str) -> bool:
    return any(s and s["name"] == name for s in state["team"] + state["bench"])
