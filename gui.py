"""The bot's control window: Start, Pause / Resume and Stop without a terminal.

    python gui.py                  (or double-click control_window.bat)

It runs play.main() in a second thread with the options ticked in the window, and shows what that
session reports: what the bot is thinking, the state it last read, the last action and its result,
and, in a red box, why it stopped when it cannot read something.

Pause and Stop take effect between two actions: never in the middle of a click or a drag, and never
while the bot waits for a battle to end. After a pause the screen is read again before anything else.

The window starts nothing in the game itself. With the first box ticked, play.py may start a
Casual run (the mode is read twice before Confirm is pressed), exactly as `python play.py --new-run`
does. With the Ranked box ticked it starts Ranked runs instead (`--new-run --ranked`; Ed allowed
this on 2026-11-14). Nothing here clicks.

The window is not kept on top and does not take the keyboard while the bot plays: the bot brings the
game to the front before every click. Keep it beside the game picture, not over it. Pushing the mouse
into a screen corner still aborts (pyautogui's fail-safe); the window then says so.
"""
from __future__ import annotations

import os
import queue
import re
import sys
import threading
import traceback
from datetime import datetime

def _startup_failed() -> None:
    """The heavy modules failed to load (a damaged data table, a missing package...). Started from the
    .bat there is no console, so without this nothing at all would show: no window, no message.
    Writes logs\\startup_error.txt and shows the error in a message box, then exits."""
    import ctypes
    from pathlib import Path
    text = traceback.format_exc()
    try:
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(exist_ok=True)
        (log_dir / "startup_error.txt").write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{text}", encoding="utf-8")
    except OSError:
        pass
    short = text.strip().splitlines()[-1] if text.strip() else "unknown error"
    try:
        ctypes.windll.user32.MessageBoxW(
            0, f"Batomon Buddy could not start.\n\n{short}\n\nThe full error is in logs\\startup_error.txt. "
               f"Tell Claude what it says.", "Batomon Buddy", 0x10)
    except Exception:
        print(text, file=sys.stderr)
    sys.exit(1)


try:
    import play                      # play imports hands and window; window makes the process DPI aware,
    import hands as hands_mod        # which has to happen before the first Tk window exists
    import window
    import tkinter as tk
except Exception:
    _startup_failed()

from tkinter import font as tkfont
from tkinter import messagebox, ttk

SCREENS = {
    "shop": "shop", "battle": "battle", "matchmaking": "looking for an opponent", "blank": "between two screens",
    "result": "battle result", "stats": "Stats window", "event": "event", "gift_box": "gift box",
    "trinket_pick": "trinket choice", "take_one_confirm": "take one or all?", "trainer_pick": "trainer choice", "trainer_ability": "choose a trainer's ability", "trinket_stolen": "a stolen trinket (Burglar)", "select_target": "choosing a target",
    "overflow": "team and bench full", "cash_in": "trinket box (Cash In / Open)", "rank_result": "Ranked: rank card", "run_over": "end of the run",
    "new_run": "New Run dialog", "main_menu": "main menu", "main_menu_continue": "main menu (a run is open)",
    "continue_confirm": "Continue panel", "title": "title screen", "minimized": "game window minimized",
    "unknown": "not recognised",
}
DOING = {"thinking": "thinking about the shop", "battle": "battle running, waiting for the result"}
TEAM_PLACES = ["top left", "top middle", "top right", "bottom left", "bottom middle", "bottom right"]
STOPPED_HERE = "stopped from the control window"       # play.py's reason when Stop was pressed
MAX_LINES = 3000                                        # lines kept in the thinking pane


# ------------------------------------------------------------ the session ----

class Session(play.Control):
    """One play.main() call in its own thread.

    The bot's side: event() puts what it reports in `events`, and pause_point() is where it waits.
    The window's side: start(), pause(), resume(), stop(). Nothing in here touches Tk.
    """

    def __init__(self, argv: list[str], hands=None, brain=None, runner=None, runs: int = 1) -> None:
        self.argv = list(argv)
        self.runs = runs                                # 0 = endless, N > 0 = play N runs
        self.events: queue.Queue = queue.Queue()
        self._hands, self._brain = hands, brain         # only the tests pass these
        self._runner = runner or play.main
        self._cond = threading.Condition()
        self._pause = False
        self._stop = False
        self._run_num = 0
        self._run_saw_day_limit = False
        self._run_saw_stop_logged = False
        self.thread: threading.Thread | None = None

    # -- the bot's side (called from the worker thread) --
    def event(self, kind: str, **data) -> None:
        if kind == "log":
            rec = data.get("record")
            why = (data.get("data") or {}).get("why")
            if rec == "stop":
                if why == "day limit":
                    self._run_saw_day_limit = True
                elif why == STOPPED_HERE:
                    self._run_saw_stop_logged = True
        self.events.put((kind, data))
        if kind == "start":
            self.events.put(("run_start", {"run": self._run_num, "total": self.runs}))

    @property
    def stop_asked(self) -> bool:
        return self._stop

    def pause_point(self) -> bool:
        with self._cond:
            if self._stop:
                return True
            if not self._pause:
                return False
            self.event("paused")
            while self._pause and not self._stop:
                self._cond.wait()
            if not self._stop:
                self.event("resumed")
            return True

    def _work(self) -> None:
        code = None
        run_num = 0
        try:
            while not self._stop and (self.runs == 0 or run_num < self.runs):
                if run_num > 0 and self.pause_point() and self._stop:
                    code = 0
                    break
                run_num += 1
                self._run_num = run_num
                run_argv = list(self.argv)
                if run_num > 1 and "--new-run" not in run_argv:
                    run_argv = ["--new-run", *run_argv]
                self._run_saw_day_limit = False
                self._run_saw_stop_logged = False
                code = self._runner(run_argv, control=self, hands=self._hands, brain=self._brain)
                if code != 0 or self._stop or self._run_saw_day_limit:
                    break
            if self._stop and not self._run_saw_stop_logged:
                self.event("log", record="stop", data={"why": STOPPED_HERE})
                if code is None:
                    code = 0
        except BaseException as e:                      # shown in the window; the thread ends cleanly
            if type(e).__name__ == "FailSafeException":
                text = ("Emergency stop: the mouse was pushed into a corner of the screen. "
                        "Nothing more was clicked.")
            else:
                traceback.print_exc()
                text = str(e) if isinstance(e, RuntimeError) and str(e) else f"{type(e).__name__}: {e}"
            self.event("error", text=text)
        finally:
            self.event("finished", code=code, runs_completed=run_num, target_runs=self.runs)

    # -- the window's side --
    def start(self) -> None:
        self.thread = threading.Thread(target=self._work, name="batomon-bot", daemon=True)
        self.thread.start()

    def pause(self) -> None:
        with self._cond:
            self._pause = True

    def resume(self) -> None:
        with self._cond:
            self._pause = False
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


# ------------------------------------------------- what the events mean ----

def _place(text: str) -> str:
    """"('team', 0)" in an action's name -> "team top left"."""
    def one(m):
        i = int(m.group(2))
        return f"team {TEAM_PLACES[i]}" if m.group(1) == "team" and i < 6 else f"bench slot {i + 1}"
    return re.sub(r"\('(team|bench)', (\d+)\)", one, text)


def _offered(rec: dict, key: str):
    items, k = rec.get("offered") or rec.get("effects") or [], rec.get(key)
    return items[k] if isinstance(k, int) and 0 <= k < len(items) and items[k] else None


def action_text(kind: str, rec: dict) -> str | None:
    """A few words for one record of the run log, or None when the record is not an action."""
    if "ok" not in rec:
        return None
    if kind == "buy" and rec.get("card"):
        return f"buy {rec['card'].get('name')} (${rec['card'].get('price')})"
    if kind in ("buy", "item", "sell", "reroll", "lock", "move"):
        return _place(rec.get("why") or rec.get("action") or kind)
    if kind == "battle":
        return f"BATTLE! (day {rec.get('day')})"
    if kind == "trainer":
        return f"pick the trainer {_offered(rec, 'picked') or 'card ' + str(rec.get('picked'))}"
    if kind == "trinket":
        return f"take the trinket {_offered(rec, 'picked') or 'card ' + str(rec.get('picked'))}"
    if kind == "event":
        return f"event: {_offered(rec, 'choice') or 'choice ' + str((rec.get('choice') or 0) + 1)}"
    if kind == "select_target":
        return _place(f"target for the event: {tuple(rec['target'])}") if rec.get("target") else "pick a target"
    if kind == "overflow":
        sold = rec.get("sold")
        return "sell " + (f"bench slot {sold + 1}" if isinstance(sold, int) else "the new monster") + " (no room)"
    return {"confirm": "CONFIRM on the result box", "gift": "open the gift box", "cash_in": "Cash In",
            "rank_result": "Confirm on the rank card",
            "new_run": f"start a {(rec.get('mode') or 'casual').capitalize()} run",
            "continue_run": "Continue on the main menu",
            "confirm_continue": "Confirm on the Continue panel",
            "title": "click on the title screen"}.get(kind, kind)


def activity_line(kind: str, rec: dict) -> tuple[str, bool] | None:
    """(line for the thinking pane, went well) for a record worth showing between the thoughts."""
    what = action_text(kind, rec)
    if what is not None:
        return f"> {what}  ->  {rec.get('message')}", bool(rec.get("ok"))
    if kind == "day_result":
        return f"> Day {rec.get('day')} is over: badges {rec.get('badges', 0):+d}, lives {rec.get('lives', 0):+d}", True
    return None


class Status:
    """What the status pane shows, kept up to date from a session's events."""

    def __init__(self) -> None:
        self.day = self.lives = self.badges = self.badges_goal = self.money = self.trainer = None
        self.screen: str | None = None       # as the reader names it
        self.doing: str | None = None        # "thinking" / "battle", until the screen is read again
        self.win_now: float | None = None    # Plan.win_now of the latest plan for this day
        self.last_action: str | None = None
        self.last_result: str | None = None
        self.last_ok: bool | None = None
        self.ended: str | None = None        # how the session ended when it did not need help
        self.team: list = []                 # the 6 team places as last read: {"name", "level"} or None
        self.bench: list = []                # the bench as last read, same shape
        self.shop_rank: int | None = None    # the shop's tier ("Rank 4"; 14 = Rank Max), not the Ranked ladder
        self.shop_odds: dict | None = None   # chance of each rarity in the shop, in percent
        self.trainer_hp: int | None = None
        self.why: str | None = None          # the bot's latest decision, in its own words (from the thinking lines)
        self.results: list[tuple[int, bool]] = []   # (day, won) for every day finished in this session
        self.run_number: int | None = None
        self.target_runs: int | None = None         # 0 = endless, N > 0 = target run count
        self.started = datetime.now()

    def apply(self, kind: str, data: dict) -> None:
        if kind == "run_start":
            self.run_number = data.get("run")
            self.target_runs = data.get("total")
            if self.run_number and self.run_number > 1:
                self.win_now = None
        elif kind == "state":
            s = data["state"]
            self.screen, self.doing = s.get("screen"), None
            if self.screen == "shop":
                if s.get("day") != self.day:
                    self.win_now = None                  # yesterday's chance says nothing about today
                for name in ("day", "lives", "badges", "badges_goal", "money", "trainer", "trainer_hp",
                             "shop_rank", "shop_odds"):
                    if s.get(name) is not None:
                        setattr(self, name, s[name])
                self.team = list(s.get("team") or [])
                self.bench = [m for m in (s.get("bench") or []) if m]
        elif kind == "doing":
            self.doing = data.get("what")
        elif kind == "plan":
            self.win_now = data.get("win_now")
        elif kind == "think":
            for line in data.get("lines") or []:
                if line.startswith("Decision:") or line.startswith("Nothing ") or "ends here" in line:
                    self.why = line
        elif kind == "log":
            name, rec = data["record"], data["data"]
            what = action_text(name, rec)
            if what is not None:
                self.last_action, self.last_result, self.last_ok = what, rec.get("message"), bool(rec.get("ok"))
                self.doing = None                        # that action is over
                now_on = re.search(r"now on '(\w+)'", str(rec.get("message")))
                if now_on:
                    self.screen = now_on.group(1)        # the hands say where it ended
            if name == "day_result":
                self.results.append((rec.get("day"), (rec.get("badges") or 0) > 0))
            if name == "run_over":
                if not self.target_runs or self.target_runs == 1:
                    self.ended = "The run is over."
            elif name == "stop" and rec.get("why") == "day limit":
                self.ended = "The number of battles you set is played. Stopped before pressing BATTLE!."
            elif name == "stop" and rec.get("why") == STOPPED_HERE:
                self.ended = "Stopped, as you asked."
        elif kind == "finished":
            if data.get("code") == 0 and self.ended is None:
                target = data.get("target_runs")
                if target and target > 1 and data.get("runs_completed") == target:
                    self.ended = f"All {target} runs finished."

    # -- texts --
    def runs_line(self) -> str:
        if self.run_number is None:
            return "-"
        if self.target_runs == 0:
            return f"Run {self.run_number} (endless)"
        if self.target_runs:
            return f"Run {self.run_number} / {self.target_runs}"
        return f"Run {self.run_number}"
    def run_line(self) -> str:
        def show(v, pre=""):
            return "-" if v is None else f"{pre}{v}"
        badges = show(self.badges) + (f" / {self.badges_goal}" if self.badges_goal and self.badges is not None else "")
        return f"Day {show(self.day)}     Lives {show(self.lives)}     Badges {badges}     Money {show(self.money, '$')}"

    def screen_line(self) -> str:
        if self.doing == "battle":
            return DOING["battle"]
        text = "-" if self.screen is None else SCREENS.get(self.screen, self.screen)
        return f"{text} ({DOING.get(self.doing, self.doing)})" if self.doing else text

    def team_names(self) -> list[str]:
        """Six short texts, one per team place: "Joltail  Lv 2", or "empty"."""
        out = []
        for i in range(6):
            m = self.team[i] if i < len(self.team) else None
            out.append(f"{m['name']}  Lv {m.get('level', '?')}" if m and m.get("name") else "empty")
        return out

    def bench_line(self) -> str:
        if not self.team and not self.bench:
            return "-"
        return ", ".join(f"{m['name']} Lv {m.get('level', '?')}" for m in self.bench if m.get("name")) or "empty"

    def shop_line(self) -> str:
        if self.shop_rank is None:
            return "-"
        rank = "Rank Max" if self.shop_rank >= 14 else f"Rank {self.shop_rank}"
        odds = self.shop_odds or {}
        shown = ", ".join(f"{k} {v}%" for k, v in odds.items() if v)
        return rank + (f"   ({shown})" if shown else "")

    def tally(self) -> str:
        won = sum(1 for _, w in self.results if w)
        return f"{len(self.results)} battles   {won} won   {len(self.results) - won} lost"

    def win_line(self) -> str:
        return "-" if self.win_now is None else f"{self.win_now:.0%}  (day {self.day}, with the team as it is)"

    def action_line(self) -> str:
        if self.last_action is None:
            return "-"
        return f"{self.last_action}  ->  {self.last_result}" + ("" if self.last_ok else "   (FAILED)")


# -------------------------------------------------- where the window goes ----

def beside(game, works, size, gap: int = 12) -> tuple[int, int]:
    """Top left corner for a window of `size` (w, h) that keeps off the game window.

    game: the game window as (left, top, right, bottom), or None when it is not open.
    works: the usable area of every monitor, same shape. Another monitor than the game's is taken
    first; else right of the game, left of it, below it, above it, whichever fits first. When
    nothing fits (or there is no game) the top right corner of the screen is used.
    """
    w, h = size
    if game is None:
        return works[0][2] - w - gap, works[0][1] + gap
    gl, gt, gr, gb = game
    cx, cy = (gl + gr) // 2, (gt + gb) // 2
    home = next((a for a in works if a[0] <= cx < a[2] and a[1] <= cy < a[3]), works[0])
    for a in works:
        if a is not home:
            return a[0] + gap, a[1] + gap
    left, top, right, bottom = home
    y = max(top, min(max(gt, top), bottom - h))          # level with the game, pushed up if too tall
    x = max(left, min(max(gl, left), right - w))
    for px, py in ((gr + gap, y), (gl - gap - w, y), (x, gb + gap), (x, gt - gap - h)):
        if left <= px and px + w <= right and top <= py and py + h <= bottom:
            return px, py
    return right - w - gap, top + gap


def place_beside_game(root: tk.Tk) -> None:
    """Put the window where beside() says. Never fatal: without it the window opens where Windows likes."""
    try:
        import win32api
        import win32gui
        root.update_idletasks()
        size = root.winfo_reqwidth() + 32, root.winfo_reqheight() + 80      # the frame and the title bar
        works = [win32api.GetMonitorInfo(m[0])["Work"] for m in win32api.EnumDisplayMonitors()]
        hwnd = window.find_window()
        game = win32gui.GetWindowRect(hwnd) if hwnd and not window.is_minimized(hwnd) else None
        root.geometry("+%d+%d" % beside(game, works, size))
    except Exception as e:
        print("control window not placed:", type(e).__name__, e)


# -------------------------------------------------------------- the window ----

RED, CALM, AMBER, GREY = "#b3261e", "#1f5f8b", "#8a5a00", "#5f6368"
BG, CARD, EDGE, SLOT, TRACK = "#eef1f7", "#ffffff", "#d3d9e6", "#eef2fa", "#dfe4ee"
NAVY, INK, GOOD, GOLD = "#1f2a44", "#1b2333", "#2e7d32", "#d19a00"
MODES = {
    "idle": "Not running.",
    "starting": "Getting ready (finding the game, loading the decision engine) ...",
    "playing": "Playing.",
    "pausing": "Pausing after the current action (a battle is waited out first) ...",
    "paused": "PAUSED. Nothing is clicked until you press Resume.",
    "stopping": "Stopping after the current action (a battle is waited out first) ...",
}


class TeachDialog:
    """A window that shows the text the bot could not read and asks what it says.

    All the work is in stop_lessons.py (tested without a screen); this only shows and asks. A letter
    that contradicts one the bot is sure of turns the button into "Teach anyway".
    """

    def __init__(self, parent: tk.Tk, capture, on_done=None) -> None:
        import base64
        import io
        import stop_lessons
        self.stop_lessons, self.capture, self.on_done = stop_lessons, capture, on_done
        self.override = False
        self.top = tk.Toplevel(parent)
        self.top.title("Teach the bot what it cannot read")
        self.entries: dict[str, tk.StringVar] = {}
        self.pictures = []                              # tk drops an image nobody holds on to
        try:
            items = stop_lessons.items_for(capture)
            rest = stop_lessons.unteachable(capture)
        except Exception as e:                          # an unreadable file must not take the window down
            items, rest = [], [f"the capture could not be read: {e}"]
        pad = {"padx": "8p", "pady": "3p"}
        if not items:
            ttk.Label(self.top, wraplength=520, justify="left", text=(
                "Nothing on this capture can be taught here.\n" + "\n".join(rest) +
                "\n\nPress 'Copy this for Claude' in the red box and tell Claude.")).grid(row=0, column=0, **pad)
            ttk.Button(self.top, text="Close", command=self.top.destroy).grid(row=1, column=0, **pad)
            return
        ttk.Label(self.top, wraplength=520, justify="left", text=(
            "The bot could not read the text below. Type what the game really shows, exactly. "
            "Nothing is changed unless it fits the picture.")).grid(row=0, column=0, columnspan=2, **pad)
        row = 1
        for item in items:
            ttk.Label(self.top, text=item.label, font=("TkDefaultFont", 10, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", **pad)
            buf = io.BytesIO()
            item.picture.save(buf, "PNG")
            img = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
            self.pictures.append(img)
            tk.Label(self.top, image=img, bg="#222").grid(row=row + 1, column=0, columnspan=2, sticky="w", **pad)
            ttk.Label(self.top, text=f"The bot reads: {item.guess or '(nothing)'}").grid(
                row=row + 2, column=0, sticky="w", **pad)
            var = tk.StringVar()
            self.entries[item.key] = var
            ttk.Entry(self.top, textvariable=var, width=36).grid(row=row + 2, column=1, sticky="w", **pad)
            ttk.Label(self.top, text=f"It says (like: {item.example})", foreground="#666").grid(
                row=row + 3, column=1, sticky="w", padx="8p")
            row += 4
        if rest:
            ttk.Label(self.top, wraplength=520, justify="left", foreground="#a00",
                      text="Also stuck, and not teachable here (tell Claude): " + "; ".join(rest)).grid(
                row=row, column=0, columnspan=2, sticky="w", **pad)
            row += 1
        self.message = tk.StringVar()
        ttk.Label(self.top, textvariable=self.message, wraplength=520, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="w", **pad)
        bar = ttk.Frame(self.top)
        bar.grid(row=row + 1, column=0, columnspan=2, sticky="e", **pad)
        self.go = ttk.Button(bar, text="Teach", command=self.teach)
        self.go.pack(side="left", padx="4p")
        ttk.Button(bar, text="Close", command=self.top.destroy).pack(side="left")

    def teach(self) -> None:
        answers = {key: var.get() for key, var in self.entries.items()}
        try:
            r = self.stop_lessons.apply(self.capture, answers, override=self.override)
        except Exception as e:
            self.message.set(f"Nothing was changed: {e}")
            return
        self.message.set(r.message)
        self.override = r.override_needed
        self.go.configure(text="Teach anyway" if self.override else "Teach")
        if not self.override and self.on_done:
            self.on_done(r.message)


class App:
    POLL_MS = 100

    def __init__(self, root: tk.Tk, new_session=None) -> None:
        self.root = root
        self.new_session = new_session or Session       # the tests pass one with a fake game
        self.session: Session | None = None
        self.status = Status()
        self.mode = "idle"
        self.closing = False
        self.banner_kind: str | None = None             # None, "needs", "error" or "info"
        self.capture: str | None = None                 # file name in captures\ of the last stop
        self._copy_text = ""
        self._ticks = 0
        self._build()
        self._set_mode("idle")
        self._refresh()
        self._check_windows()
        self._after = root.after(self.POLL_MS, self._tick)

    # -- building --
    def _card(self, parent, title: str | None = None) -> tk.Frame:
        """A white box with a thin border and, when given, a small grey heading."""
        card = tk.Frame(parent, bg=CARD, highlightbackground=EDGE, highlightthickness=1)
        if title:
            tk.Label(card, text=title.upper(), bg=CARD, fg=GREY, font=self.f_tiny, anchor="w").pack(
                fill="x", padx=10, pady=(6, 0))
        return card

    def _build(self) -> None:
        root = self.root
        root.title("Batomon Buddy")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.configure(bg=BG)
        style = ttk.Style(root)
        try:
            style.theme_use("clam")                     # the one theme whose colours can all be set
        except tk.TclError:
            pass
        style.configure(".", background=BG)
        style.configure("TButton", padding=(14, 6))
        style.configure("Go.TButton", padding=(18, 7), font=("TkDefaultFont", 10, "bold"))
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", background=BG, foreground=GREY)
        style.configure("TCheckbutton", background=BG)
        style.configure("TRadiobutton", background=BG)
        base = tkfont.nametofont("TkDefaultFont")
        size = base.cget("size")
        self.f_bold = base.copy()
        self.f_bold.configure(weight="bold")
        self.f_big = base.copy()
        self.f_big.configure(size=size + 2, weight="bold")
        self.f_huge = base.copy()
        self.f_huge.configure(size=size + 12, weight="bold")
        self.f_tiny = base.copy()
        self.f_tiny.configure(size=max(size - 2, 7), weight="bold")
        self.f_mono = tkfont.Font(family="Consolas", size=9)
        self.wrap = self.f_mono.measure("0") * 76          # width of the text column, in pixels
        root.columnconfigure(0, weight=1)
        root.rowconfigure(5, weight=1)
        P = {"padx": "6p", "pady": "3p"}

        # the box that says why the bot stopped (hidden until then)
        self.banner = tk.Frame(root, bg=RED)
        self.banner.columnconfigure(0, weight=1)
        self.banner_title = tk.Label(self.banner, bg=RED, fg="white", font=self.f_big, anchor="w", justify="left")
        self.banner_text = tk.Label(self.banner, bg=RED, fg="white", anchor="w", justify="left", wraplength=self.wrap)
        self.banner_capture = tk.Label(self.banner, bg=RED, fg="white", font=self.f_bold, anchor="w",
                                       justify="left", wraplength=self.wrap)
        self.banner_buttons = tk.Frame(self.banner, bg=RED)
        self.open_button = ttk.Button(self.banner_buttons, text="Open the capture", command=self.open_capture)
        self.copy_button = ttk.Button(self.banner_buttons, text="Copy this for Claude", command=self.copy_stop)
        self.teach_button = ttk.Button(self.banner_buttons, text="Teach it", command=self.teach)
        self.teach_button.pack(side="left", padx=(0, "6p"))
        self.open_button.pack(side="left", padx=(0, "6p"))
        self.copy_button.pack(side="left")
        self.banner_title.grid(row=0, column=0, sticky="ew", padx="8p", pady=("6p", 0))
        self.banner_text.grid(row=1, column=0, sticky="ew", padx="8p")
        self.banner_capture.grid(row=2, column=0, sticky="ew", padx="8p")
        self.banner_buttons.grid(row=3, column=0, sticky="w", padx="8p", pady="6p")

        # the title bar of the window: name on the left, what the bot is doing on the right
        head = tk.Frame(root, bg=NAVY)
        head.grid(row=1, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="Batomon Buddy", bg=NAVY, fg="white", font=self.f_big).grid(
            row=0, column=0, padx=12, pady=8)
        self.mode_text = tk.StringVar()
        self.mode_label = tk.Label(head, textvariable=self.mode_text, bg=NAVY, fg="#cfd8ee", anchor="e",
                                   justify="right", wraplength=self.wrap - 150)
        self.mode_label.grid(row=0, column=1, sticky="e", padx=12)

        # the buttons
        bar = tk.Frame(root, bg=BG)
        bar.grid(row=2, column=0, sticky="ew", **P)
        self.start_button = ttk.Button(bar, text="Start", style="Go.TButton", command=self.start)
        self.pause_button = ttk.Button(bar, text="Pause", style="Go.TButton", command=self.pause_resume)
        self.stop_button = ttk.Button(bar, text="Stop", style="Go.TButton", command=self.stop)
        for b in (self.start_button, self.pause_button, self.stop_button):
            b.pack(side="left", padx=(0, "6p"))

        # the dashboard
        body = tk.Frame(root, bg=BG)
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        self.warning = tk.StringVar()
        self.warning_label = tk.Label(body, textvariable=self.warning, fg=AMBER, bg=BG, font=self.f_bold,
                                      anchor="w", justify="left", wraplength=self.wrap)
        self.v = {name: tk.StringVar(value="-") for name in
                  ("run", "runs", "trainer", "screen", "win", "action", "game", "day", "lives", "badges", "money",
                   "shop", "bench", "why", "tally", "team0", "team1", "team2", "team3", "team4", "team5")}
        self.value_labels = {}

        tiles = tk.Frame(body, bg=BG)
        tiles.grid(row=1, column=0, sticky="ew", **P)
        for c in range(4):
            tiles.columnconfigure(c, weight=1, uniform="tile")
        self.tile_value = {}
        for c, (key, caption) in enumerate((("day", "Day"), ("lives", "Lives"), ("badges", "Badges"),
                                            ("money", "Gold"))):
            card = self._card(tiles, caption)
            card.grid(row=0, column=c, sticky="nsew", padx=(0 if c == 0 else 3, 0 if c == 3 else 3))
            lab = tk.Label(card, textvariable=self.v[key], bg=CARD, fg=INK, font=self.f_huge, anchor="w")
            lab.pack(fill="x", padx=10, pady=(0, 6))
            self.tile_value[key] = lab
        self.badge_bar = tk.Canvas(tiles, height=5, bg=BG, highlightthickness=0)
        self.badge_bar.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))

        win = self._card(body, "Chance to win today's battle")
        win.grid(row=2, column=0, sticky="ew", **P)
        self.win_value = tk.Label(win, textvariable=self.v["win"], bg=CARD, fg=INK, font=self.f_big, anchor="w")
        self.win_value.pack(fill="x", padx=10)
        self.win_bar = tk.Canvas(win, height=12, bg=CARD, highlightthickness=0)
        self.win_bar.pack(fill="x", padx=10, pady=(2, 8))
        self.win_bar.bind("<Configure>", lambda e: self._draw_bars())

        mid = tk.Frame(body, bg=BG)
        mid.grid(row=3, column=0, sticky="ew", **P)
        mid.columnconfigure(0, weight=3)
        mid.columnconfigure(1, weight=2)
        team = self._card(mid, "Your team")
        team.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        grid = tk.Frame(team, bg=CARD)
        grid.pack(fill="x", padx=8, pady=(2, 2))
        self.team_labels = []
        for i in range(6):
            grid.columnconfigure(i % 3, weight=1, uniform="slot")
            lab = tk.Label(grid, textvariable=self.v[f"team{i}"], bg=SLOT, fg=INK, anchor="w", padx=6, pady=5)
            lab.grid(row=i // 3, column=i % 3, sticky="ew", padx=2, pady=2)
            self.team_labels.append(lab)
        tk.Label(team, text="BENCH", bg=CARD, fg=GREY, font=self.f_tiny, anchor="w").pack(fill="x", padx=10)
        tk.Label(team, textvariable=self.v["bench"], bg=CARD, fg=INK, anchor="w", justify="left",
                 wraplength=self.wrap * 6 // 10).pack(fill="x", padx=10, pady=(0, 8))
        info = self._card(mid, "The run")
        info.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        for key, caption in (("runs", "Run"), ("trainer", "Trainer"), ("shop", "Shop tier"), ("screen", "Screen"),
                             ("tally", "This session")):
            tk.Label(info, text=caption, bg=CARD, fg=GREY, font=self.f_tiny, anchor="w").pack(fill="x", padx=10)
            lab = tk.Label(info, textvariable=self.v[key], bg=CARD, fg=INK, anchor="w", justify="left",
                           wraplength=self.wrap * 4 // 10 - 20)
            lab.pack(fill="x", padx=10, pady=(0, 4))
            self.value_labels[key] = lab
        self.days_row = tk.Canvas(info, height=16, bg=CARD, highlightthickness=0)     # one dot per battle
        self.days_row.pack(fill="x", padx=10, pady=(0, 8))

        now = self._card(body, "What the bot is doing")
        now.grid(row=4, column=0, sticky="ew", **P)
        lab = tk.Label(now, textvariable=self.v["action"], bg=CARD, fg=INK, font=self.f_bold, anchor="w",
                       justify="left", wraplength=self.wrap - 24)
        lab.pack(fill="x", padx=10)
        self.value_labels["action"] = lab
        tk.Label(now, textvariable=self.v["why"], bg=CARD, fg=GREY, anchor="w", justify="left",
                 wraplength=self.wrap - 24).pack(fill="x", padx=10, pady=(0, 8))

        # options: the same ones play.py takes
        opts = ttk.LabelFrame(root, text="Options", padding="6p")
        opts.grid(row=4, column=0, sticky="ew", **P)
        self.new_run = tk.BooleanVar(value=False)
        self.ranked = tk.BooleanVar(value=False)
        self.limit_days = tk.BooleanVar(value=False)
        self.days = tk.StringVar(value="1")
        self.decide = tk.StringVar(value="planner")
        self.option_widgets = []
        w = ttk.Checkbutton(opts, variable=self.new_run,
                            text="If no run is open, start a new Casual run")
        w.grid(row=0, column=0, columnspan=4, sticky="w")
        self.option_widgets.append(w)
        w = ttk.Checkbutton(opts, variable=self.ranked,
                            text="Play Ranked: start new runs in Ranked Mode instead (and continue a saved Ranked run)")
        w.grid(row=3, column=0, columnspan=4, sticky="w", pady=("3p", 0))
        self.option_widgets.append(w)
        self.runs = tk.StringVar(value="1")
        runs_row = ttk.Frame(opts)
        runs_row.grid(row=4, column=0, columnspan=4, sticky="w", pady=("3p", 0))
        ttk.Label(runs_row, text="Runs to play:").pack(side="left")
        w = ttk.Spinbox(runs_row, from_=0, to=999, width=4, textvariable=self.runs)
        w.pack(side="left", padx="4p")
        self.option_widgets.append(w)
        ttk.Label(runs_row, text="(0 = endless)").pack(side="left")
        w = ttk.Checkbutton(opts, variable=self.limit_days, text="Stop after")
        w.grid(row=1, column=0, sticky="w")
        self.option_widgets.append(w)
        w = ttk.Spinbox(opts, from_=0, to=99, width=3, textvariable=self.days)
        w.grid(row=1, column=1, sticky="w", padx="4p")
        self.option_widgets.append(w)
        ttk.Label(opts, text="battles (otherwise it plays until the run ends)").grid(row=1, column=2, sticky="w")
        how = ttk.Frame(opts)
        how.grid(row=2, column=0, columnspan=4, sticky="w", pady=("3p", 0))
        ttk.Label(how, text="Decide with:").pack(side="left")
        for value, text in (("planner", "the planner (normal)"), ("learned", "the learned policy"),
                            ("simple", "the old simple rules")):
            w = ttk.Radiobutton(how, variable=self.decide, value=value, text=text)
            w.pack(side="left", padx=("6p", 0))
            self.option_widgets.append(w)

        # thinking
        th = self._card(root, "Thinking (newest at the bottom)")
        th.grid(row=5, column=0, sticky="nsew", **P)
        inner = tk.Frame(th, bg=CARD)
        inner.pack(fill="both", expand=True, padx=4, pady=4)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        self.text = tk.Text(inner, width=76, height=8, wrap="word", font=self.f_mono, state="disabled",
                            borderwidth=0, padx=4, pady=4, bg=CARD, fg=INK)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.tag_configure("head", foreground=GREY)
        self.text.tag_configure("done", foreground=CALM)
        self.text.tag_configure("bad", foreground=RED)

        self.files = tk.StringVar(value="Emergency stop: push the mouse into a corner of the screen.")
        tk.Label(root, textvariable=self.files, bg=BG, fg=GREY, wraplength=self.wrap, justify="left",
                 anchor="w").grid(row=6, column=0, sticky="ew", padx="6p")
        tk.Label(root, textvariable=self.v["game"], bg=BG, fg=GREY, wraplength=self.wrap, justify="left",
                 anchor="w", font=self.f_tiny).grid(row=7, column=0, sticky="ew", padx="6p", pady=(0, "4p"))

    def _draw_bars(self) -> None:
        """The two bars: badges won out of the goal (under the tiles) and the chance to win today."""
        s = self.status
        for canvas, frac, colour in (
                (self.badge_bar, (s.badges or 0) / s.badges_goal if s.badges_goal else 0, GOLD),
                (self.win_bar, s.win_now or 0,
                 GOOD if (s.win_now or 0) >= 0.65 else GOLD if (s.win_now or 0) >= 0.4 else RED)):
            canvas.delete("all")
            w = max(canvas.winfo_width(), 10)
            h = int(canvas.cget("height"))
            canvas.create_rectangle(0, 0, w, h, fill=TRACK, outline="")
            if frac:
                canvas.create_rectangle(0, 0, int(w * min(frac, 1)), h, fill=colour, outline="")

    # -- the options, as the arguments play.py takes --
    def argv(self) -> list[str] | None:
        out = []
        if self.new_run.get() or self.ranked.get():
            out.append("--new-run")
        if self.ranked.get():
            out.append("--ranked")
        if self.limit_days.get():
            text = self.days.get().strip()
            if not text.isdigit() or not 0 <= int(text) <= 99:
                return None
            out += ["--days", str(int(text))]
        if self.decide.get() == "simple":
            out.append("--simple")
        elif self.decide.get() == "learned":
            out.append("--learned")
        return out

    def parse_runs(self) -> int | None:
        text = self.runs.get().strip()
        if not text.isascii() or not text.isdigit():
            return None
        n = int(text)
        return n if n >= 0 else None

    # -- the buttons --
    def start(self) -> None:
        if self.session is not None and self.session.running:
            return
        argv = self.argv()
        if argv is None:
            self.mode_text.set("Not started: the number of battles must be between 0 and 99.")
            return
        runs = self.parse_runs()
        if runs is None:
            self.mode_text.set("Not started: the number of runs must be 0 (endless) or a positive whole number.")
            return
        self.drain()
        self._hide_banner()
        self.status = Status()
        runs_label = "endless runs" if runs == 0 else f"{runs} runs" if runs > 1 else ""
        extra = f" ({runs_label})" if runs_label else ""
        self._say([f"=== {datetime.now():%H:%M:%S}  Start"
                   f"{' with ' + ' '.join(argv) if argv else ''}{extra} ==="], "head")
        self.session = self.new_session(argv)
        if hasattr(self.session, "runs"):
            self.session.runs = runs
        self.session.start()
        self._set_mode("starting")
        self._refresh()

    def pause_resume(self) -> None:
        if self.session is None or not self.session.running:
            return
        if self.mode in ("starting", "playing"):
            self.session.pause()
            self._set_mode("pausing")
        elif self.mode in ("pausing", "paused"):
            self.session.resume()
            self._set_mode("playing")

    def stop(self) -> None:
        if self.session is None or not self.session.running:
            return
        self.session.stop()
        self._set_mode("stopping")

    def close(self) -> None:
        """The window's X: the bot first finishes what it is doing, so no click or drag is cut in half."""
        if self.session is not None and self.session.running:
            if self.closing and not messagebox.askyesno(
                    "Batomon Buddy", "The bot is still finishing an action (a battle can take a few minutes).\n\n"
                                   "Close now anyway?", parent=self.root):
                return
            if not self.closing:
                self.closing = True
                self.session.stop()
                self._set_mode("stopping")
                self.mode_text.set("Closing after the current action ... (press X again to close at once)")
                return
        self.destroy()

    def destroy(self) -> None:
        try:
            self.root.after_cancel(self._after)
            self.root.destroy()
        except tk.TclError:
            pass

    def open_capture(self) -> None:
        path = hands_mod.CAPTURE_DIR / self.capture if self.capture else hands_mod.CAPTURE_DIR
        try:
            os.startfile(path if path.exists() else hands_mod.CAPTURE_DIR)
        except OSError as e:
            self.mode_text.set(f"Could not open {path}: {e}")

    def teach(self) -> None:
        """Teach the reader what the capture of this stop shows, by typing it: no coding session."""
        if self.session is not None and self.session.running:
            return                                      # the bot is still working; teach after it has stopped
        path = hands_mod.CAPTURE_DIR / self.capture if self.capture else None
        if path is None or not path.exists():
            messagebox.showinfo("Teach it", "The capture of this stop is not there to teach from.")
            return
        TeachDialog(self.root, path, on_done=self._taught)

    def _taught(self, message: str) -> None:
        self.mode_text.set(message)

    def copy_stop(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self._copy_text)

    # -- what the session reports --
    def _tick(self) -> None:
        self.drain()
        self._ticks += 1
        if self._ticks % 20 == 0:
            self._check_windows()
        self._after = self.root.after(self.POLL_MS, self._tick)

    def drain(self) -> None:
        """Take everything the session has reported so far and show it."""
        if self.session is None:
            return
        changed = False
        for _ in range(500):
            try:
                kind, data = self.session.events.get_nowait()
            except queue.Empty:
                break
            self._handle(kind, data)
            changed = True
            if kind == "finished" and self.closing:
                self.destroy()
                return
        if changed:
            self._refresh()

    def _handle(self, kind: str, data: dict) -> None:
        self.status.apply(kind, data)
        if kind == "run_start":
            run_num = data.get("run", 1)
            if run_num > 1:
                total = data.get("total")
                label = f"Run {run_num} / {total}" if total else f"Run {run_num} (endless)"
                self._say([f"=== {datetime.now():%H:%M:%S}  {label} ==="], "head")
        elif kind == "start":
            self.files.set(f"Log: logs\\{data['log']}  and  logs\\{data['thinking']}\n"
                           f"Emergency stop: push the mouse into a corner of the screen.")
            if self.mode == "starting":
                self._set_mode("playing")
        elif kind == "think":
            self._say(list(data["lines"]) + [""])
        elif kind == "log":
            line = activity_line(data["record"], data["data"])
            if line:
                self._say([line[0], ""], "done" if line[1] else "bad")
        elif kind == "paused":
            if self.mode == "pausing":
                self._set_mode("paused")
        elif kind == "resumed":
            if self.mode in ("pausing", "paused"):
                self._set_mode("playing")
        elif kind == "needs_you":
            why = data.get("why") or "no reason was given"
            capture = data.get("capture")
            if not capture:
                m = re.search(r"capture: ([\w.\-]+\.png)", why)
                capture = m.group(1) if m else None
            if "--new-run" in why:
                why = ("No run is open in the game. Tick the first box under Options to let the bot start a "
                       "Casual run (or the Ranked box for a Ranked run), then press Start.")
            self._show_banner("needs", "THE BOT STOPPED AND NEEDS YOU", why, capture, data.get("log"))
        elif kind == "error":
            text = data.get("text") or "unknown error"
            if "window not found" in text:
                text += ". Open the game first, then press Start."
            self._show_banner("error", "THE BOT STOPPED", text, None, None)
        elif kind == "finished":
            self._set_mode("idle")
            if self.banner_kind is None:
                if data.get("code") == 0 or self.status.ended:
                    self._show_banner("info", self.status.ended or "The session has ended.", "", None, None)
                else:
                    self._show_banner("error", "THE BOT STOPPED", "It gave no reason. Look at the last lines "
                                      "under Thinking and at the log named below.", None, None)

    def _say(self, lines: list[str], tag: str | None = None) -> None:
        """Add lines to the thinking pane; it follows the newest unless you have scrolled up."""
        at_end = self.text.yview()[1] >= 0.999
        self.text.configure(state="normal")
        self.text.insert("end", "\n".join(lines) + "\n", (tag,) if tag else ())
        extra = int(self.text.index("end-1c").split(".")[0]) - MAX_LINES
        if extra > 0:
            self.text.delete("1.0", f"{extra + 1}.0")
        self.text.configure(state="disabled")
        if at_end:
            self.text.see("end")

    def thinking(self) -> str:
        return self.text.get("1.0", "end-1c")

    # -- showing --
    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.mode_text.set(MODES[mode])
        running = mode != "idle"
        self.start_button.configure(state="disabled" if running else "normal")
        self.pause_button.configure(state="normal" if mode in ("starting", "playing", "pausing", "paused") else "disabled",
                                    text="Resume" if mode in ("pausing", "paused") else "Pause")
        self.stop_button.configure(state="normal" if mode in ("starting", "playing", "pausing", "paused") else "disabled")
        for w in self.option_widgets:
            w.configure(state="disabled" if running else "normal")

    def _refresh(self) -> None:
        s = self.status
        dash = lambda v, pre="": "-" if v is None else f"{pre}{v}"
        self.v["run"].set(s.run_line())
        self.v["runs"].set(s.runs_line())
        self.v["day"].set(dash(s.day))
        self.v["lives"].set(dash(s.lives))
        self.v["badges"].set("-" if s.badges is None else f"{s.badges} / {s.badges_goal or '?'}")
        self.v["money"].set(dash(s.money, "$"))
        self.v["trainer"].set(s.trainer or "-")
        self.v["screen"].set(s.screen_line())
        self.v["win"].set(s.win_line())
        self.v["action"].set(s.action_line())
        self.v["shop"].set(s.shop_line())
        self.v["bench"].set(s.bench_line())
        self.v["why"].set(s.why or "")
        self.v["tally"].set(s.tally())
        for i, text in enumerate(s.team_names()):
            self.v[f"team{i}"].set(text)
            self.team_labels[i].configure(fg=GREY if text == "empty" else INK)
        self.days_row.delete("all")
        for n, (_, won) in enumerate(s.results[-14:]):          # green = badge won, red = badge lost
            self.days_row.create_oval(n * 18, 2, n * 18 + 13, 15, fill=GOOD if won else RED, outline="")
        self.value_labels["action"].configure(fg=RED if s.last_ok is False else INK)
        self._draw_bars()

    def _show_banner(self, kind: str, title: str, text: str, capture: str | None, log: str | None) -> None:
        colour = CALM if kind == "info" else RED
        for w in (self.banner, self.banner_title, self.banner_text, self.banner_capture, self.banner_buttons):
            w.configure(bg=colour)
        self.banner_kind, self.capture = kind, capture
        self.banner_title.configure(text=title)
        if kind == "needs":
            text += "\nNothing was clicked after this. Look at the game, then tell Claude what it shows."
            where = f"Capture: captures\\{capture}" if capture else "No capture was saved for this stop."
        else:
            where = ""
        self.banner_text.configure(text=text)
        self.banner_capture.configure(text=where)
        self._copy_text = "\n".join(x for x in (title, text, where, f"Log: logs\\{log}" if log else "") if x)
        for w, show in ((self.banner_text, bool(text)), (self.banner_capture, bool(where)),
                        (self.banner_buttons, kind != "info")):
            w.grid() if show else w.grid_remove()
        self.open_button.configure(text="Open the capture" if capture else "Open the captures folder")
        if kind == "needs" and capture:                 # a stop on something unreadable can be taught here
            self.teach_button.pack(side="left", padx=(0, "6p"), before=self.open_button)
        else:
            self.teach_button.pack_forget()
        self.banner.grid(row=0, column=0, sticky="ew")
        if kind != "info":
            self._call_attention()

    def _hide_banner(self) -> None:
        self.banner_kind = self.capture = None
        self.banner.grid_remove()

    def _call_attention(self) -> None:
        """Bring the window into view and flash its taskbar button, without taking the keyboard.
        Only used once the bot has stopped clicking."""
        try:
            if self.root.state() != "normal":
                return
            import ctypes
            self.root.attributes("-topmost", True)
            self.root.after(400, self._not_topmost)
            user32 = ctypes.windll.user32
            user32.GetParent.restype = ctypes.c_void_p
            user32.FlashWindow(ctypes.c_void_p(user32.GetParent(self.root.winfo_id())), True)
        except Exception:
            pass

    def _not_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _check_windows(self) -> None:
        """Is the game there, at the size the reader needs, and is this window off its picture?"""
        over = False
        try:
            hwnd = window.find_window()
            if hwnd is None:
                game = "not found (open Batomon Showdown first)"
            elif window.is_minimized(hwnd):
                game = "minimized (the bot cannot read a minimized window)"
            else:
                pic = window.picture_rect(window.client_rect(hwnd))
                right_size = (pic.width, pic.height) == (window.CANON_W, window.CANON_H)
                game = f"open, picture {pic.width} x {pic.height}" + (
                    "" if right_size else f" (the reader needs {window.CANON_W} x {window.CANON_H})")
                x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
                over = (self.root.state() == "normal" and x < pic.right and pic.left < x + self.root.winfo_width()
                        and y < pic.bottom and pic.top < y + self.root.winfo_height())
        except Exception as e:
            game = f"could not be checked ({type(e).__name__})"
        self.v["game"].set(game)
        if over:
            self.warning.set("This window is over the game picture. Move it beside the game: the bot brings "
                             "the game to the front when it clicks, and this window would end up behind it.")
            self.warning_label.grid(row=0, column=0, sticky="ew", padx="6p")
        else:
            self.warning.set("")
            self.warning_label.grid_remove()


def main() -> int:
    if sys.stdout is None or sys.stderr is None:
        # started without a console (pythonw): keep what play.py prints, and any error, in a file
        hands_mod.LOG_DIR.mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(hands_mod.LOG_DIR / "control_window_console.txt", "a",
                                       encoding="utf-8", buffering=1)
        print(f"\n=== control window opened {datetime.now():%Y-%m-%d %H:%M:%S} ===")
    root = tk.Tk()
    App(root)
    place_beside_game(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
