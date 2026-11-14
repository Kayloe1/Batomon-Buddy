"""Teach the reader from a stop, without editing code.

When the bot stops because it cannot read a piece of text on the shop screen, the red box in the
control window offers "Teach it". That opens a small window showing the part of the capture the bot
could not read; the person types what it says, and this module does the rest:

    python stop_lessons.py                    # the newest capture that has something unreadable
    python stop_lessons.py captures\\x.png     # a particular capture

What it handles: the text regions of the shop screen (lives, day, badges, trainer, HP, money,
reroll price, the rank bar, and each shop card's name and price). What it does not: sprites, level
labels, stat badges, event lines, whole new screens. Those still need Claude (the window says so).

Safety, because a wrong answer would teach the reader wrong glyphs:
  * the typed text must have exactly as many characters as the picture has marks, or nothing changes;
  * after teaching, the capture is read again; if the region does not now read as typed, the glyph
    file is put back exactly as it was;
  * the glyph file is copied to glyphs.json.bak first, as teach.py does;
  * every accepted answer is saved in data/user_lessons.json, which teach.py replays, so the lesson
    survives a `teach.py --rebuild`.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

import glyphs
import reader
import window

ROOT = Path(__file__).resolve().parent
USER_LESSONS = ROOT / "data" / "user_lessons.json"
CAPTURE_DIR = ROOT / "captures"

# region key -> (what a person calls it, the box to show, an example of what to type)
_BOXES = {
    "lives": ("Lives (top, next to the heart)", reader.LIVES, "10"),
    "day": ("The day (top middle)", reader.DAY, "DAY 9"),
    "badges": ("Badges (top right)", reader.BADGES, "3/10"),
    "trainer": ("Trainer name (left)", reader.TRAINER, "BUG CATCHER"),
    "hp": ("Trainer HP (left)", reader.HP, "11600"),
    "rank_bar": ("The rank bar (above the shop)", reader.RANK_BAR, "Rank 4  67% 30% 3% 0% 0%"),
    "money": ("Your money", reader.MONEY, "$40"),
    "reroll": ("The reroll price", reader.REROLL_PRICE, "$3"),
}
for _i in range(reader.SHOP_SLOTS):
    _BOXES[f"shop{_i}"] = (f"Shop card {_i + 1} (from the left): name and price", reader._card_text(_i),
                           "Kickrane $40")

# A problem line from the reader -> the region it is about.
_PROBLEM = re.compile(
    r"^(?:(?P<shop>shop) (?P<i>\d): (?:unknown name|cannot read price|cannot split name and price|no text found)"
    r"|(?P<key>lives|day|badges|trainer|money|hp|reroll): (?:cannot read|unknown name)"
    r"|(?P<rank>rank bar): cannot read)")


@dataclass
class Item:
    key: str                  # a key of reader.text_masks
    label: str                # for a person
    example: str              # what typing it looks like
    guess: str                # what the reader made of it ("?" marks a glyph it does not know)
    picture: Image.Image      # the part of the capture, as it is in the game


@dataclass
class Result:
    ok: bool
    message: str
    remaining: list[str]      # reader problems still on the capture after teaching
    override_needed: bool = False   # typed text contradicts a letter the bot is sure of; ask, then apply(override=True)


def _picture_of(image: Image.Image) -> np.ndarray:
    return np.asarray(window.canonical_picture(image))


def _guess(pic: np.ndarray, key: str) -> str:
    font, mask, opts = reader.text_masks(pic)[key]
    if key.startswith("shop"):
        parts = reader._split_name_price(mask)
        if parts is None:
            return reader.store().read(font, mask, space_gap=7)
        return f"{reader.store().read(font, parts[0], space_gap=7)} {reader.store().read(font, parts[1])}"
    return reader.store().read(font, mask, **opts)


def items_for(capture: Path | str) -> list[Item]:
    """What on this capture the reader could not read and a person can teach it."""
    image = Image.open(capture)
    state = reader.read_state(image)
    if state.get("screen") != "shop":
        return []
    pic = _picture_of(image)
    keys: list[str] = []
    for p in state.get("problems", []):
        m = _PROBLEM.match(p)
        if not m:
            continue
        key = f"shop{m['i']}" if m["shop"] else "rank_bar" if m["rank"] else m["key"]
        if key not in keys:
            keys.append(key)
    out = []
    for key in keys:
        label, box, example = _BOXES[key]
        x0, y0, x1, y1 = box
        out.append(Item(key, label, example, _guess(pic, key), Image.fromarray(pic[y0:y1, x0:x1])))
    return out


def unteachable(capture: Path | str) -> list[str]:
    """Problems on the capture that this tool cannot teach (so the window can say who can)."""
    state = reader.read_state(Image.open(capture))
    if state.get("screen") != "shop":
        return [f"the capture shows the '{state.get('screen')}' screen; only the shop screen can be taught here"]
    return [p for p in state.get("problems", []) if not _PROBLEM.match(p)]


def newest_stop_capture() -> Path | None:
    """The newest capture that has something teachable on it (the red box names it; this is for the terminal)."""
    files = sorted(Path(__file__).resolve().parent.joinpath("captures").glob("unread_*.png"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        if items_for(f):
            return f
    return None


def _truth(key: str, typed: str) -> str:
    """What a person typed -> the text teach.py wants for that region."""
    typed = typed.strip()
    if key.startswith("shop"):
        m = re.fullmatch(r"(.+?)\s*\|?\s*(\$\s*\d+)", typed)
        if not m:
            raise ValueError("type the name and then the price, like  Kickrane $40")
        return f"{m[1].strip()}|{m[2].replace(' ', '')}"
    if key == "day":
        m = re.fullmatch(r"(?i)day\s*(\d+)", typed)
        if not m:
            raise ValueError("type the day like  DAY 9")
        return f"DAY{m[1]}"
    if key == "rank_bar":
        m = re.fullmatch(r"(?i)rank\s*(max|\d+)\s*[,;*]?\s*((?:\d+%[\s,;*]*)+)", typed)
        if not m:
            raise ValueError("type the rank and the five percentages, like  Rank 4  67% 30% 3% 0% 0%")
        odds = re.findall(r"\d+%", m[2])
        return "Rank" + ("Max" if m[1].lower() == "max" else m[1]) + "".join("*" + o for o in odds)
    if key == "trainer":
        return typed.upper()
    return typed.replace(" ", "")


def _reads_as(pic: np.ndarray, key: str, truth: str) -> bool:
    """Does the region, read with the glyphs now known, say `truth`?"""
    font, mask, opts = reader.text_masks(pic)[key]
    st = reader.store()
    if "|" in truth:
        name, price = truth.split("|")
        parts = reader._split_name_price(mask)
        return (parts is not None and st.read(font, parts[0], space_gap=7).replace(" ", "") == name.replace(" ", "")
                and st.read(font, parts[1]) == price)
    return st.read(font, mask, **opts).replace(" ", "") == truth.replace(" ", "")


def _names_note(key: str, truth: str) -> str:
    """A name can be read perfectly and still not be in the game data; say so."""
    if key.startswith("shop"):
        name = truth.split("|")[0]
        if name not in reader.MONSTER_NAMES + reader.ITEM_NAMES:
            return (f" '{name}' is read correctly now, but it is not in the game data yet "
                    f"(data/names.json), so the bot will still stop on it: tell Claude.")
    elif key == "trainer" and not reader.resolve(truth, reader.TRAINER_NAMES, ignore_case=True):
        return f" '{truth}' is not in data/trainers.csv yet: tell Claude."
    return ""


def _contradictions(pic: np.ndarray, truths: dict[str, str]) -> list[str]:
    """Places where the typed text disagrees with a character the bot read without doubt ("?" is doubt)."""
    out = []
    for key, truth in truths.items():
        guess = _guess(pic, key).replace(" ", "")
        want = truth.replace("|", "").replace(" ", "")
        if len(guess) != len(want):
            continue                          # teaching will say so if the counts do not fit the glyphs
        for g, w in zip(guess, want):
            if g != "?" and g != w:
                out.append(f"{_BOXES[key][0]}: the bot reads '{guess}', you typed '{want}'")
                break
    return out


def apply(capture: Path | str, answers: dict[str, str], override: bool = False) -> Result:
    """Teach what was typed (key -> text). All or nothing; see the module doc for the checks.

    A typed letter that contradicts one the bot read with confidence is refused unless override=True
    (the bot can be sure and wrong: it read K as H). A slip of the keyboard would otherwise teach it
    a wrong glyph that reads back perfectly.
    """
    capture = Path(capture)
    answers = {k: v for k, v in answers.items() if v and v.strip()}
    if not answers:
        return Result(False, "Nothing was typed, so nothing was taught.", [])
    try:
        truths = {k: _truth(k, v) for k, v in answers.items()}
    except ValueError as e:
        return Result(False, str(e), [])
    image = Image.open(capture)
    pic = _picture_of(image)
    clash = [] if override else _contradictions(pic, truths)
    if clash:
        return Result(False, "Check this before teaching: " + "; ".join(clash) + ". If the bot is the one "
                             "that is wrong, press Teach anyway.", [], override_needed=True)
    path = glyphs.TEMPLATE_FILE
    before = path.read_bytes() if path.exists() else None
    bak = path.with_name(path.name + ".bak")
    store = glyphs.GlyphStore(path)
    reader._store = store
    try:
        masks = reader.text_masks(pic)
        for key, truth in truths.items():
            font, mask, _ = masks[key]
            if "|" in truth:
                name, price = truth.split("|")
                parts = reader._split_name_price(mask)
                if parts is None:
                    raise ValueError(f"{_BOXES[key][0]}: the bot cannot even tell where the name ends and the "
                                     f"price starts, so typing cannot teach it. Tell Claude.")
                store.teach(font, parts[0], name)
                store.teach(font, parts[1], price)
            else:
                store.teach(font, mask, truth)
    except ValueError as e:
        reader.reload_templates()
        return Result(False, f"Nothing was changed. {e} (The picture may have two letters touching or one "
                             f"letter in pieces; that needs Claude.)", [])
    if before is not None:
        bak.write_bytes(before)
    store.save()
    reader.reload_templates()
    wrong = [_BOXES[k][0] for k, t in truths.items() if not _reads_as(pic, k, t)]
    if wrong:                                   # put everything back exactly as it was
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(before)
        reader.reload_templates()
        return Result(False, "Nothing was changed: after teaching, the bot still did not read "
                             + ", ".join(wrong) + " as typed. Check what you typed against the picture; "
                             "if it is right, tell Claude.", [])
    _remember(capture, truths)
    state = reader.read_state(image)
    note = "".join(_names_note(k, t) for k, t in truths.items())
    left = list(state.get("problems", []))
    if left:
        msg = "Taught. The capture still has: " + "; ".join(left) + "." + note
    else:
        msg = "Taught. The bot reads the whole capture now: press Start again." + note
    return Result(not left, msg, left)


def self_teach_cards(image: Image.Image, state: dict) -> list[str]:
    """Teach the card font from shop cards the reader named by their picture (reader.name_by_card_sprite):
    the picture says which monster it is, so the true text is known and no person needs to type it.

    Uses apply() with all its checks (the marks must fit the text, the card must read back as taught,
    otherwise everything is put back). The capture is kept in captures/ so teach.py can replay the lesson.
    Returns lines for the thinking log; empty when there was nothing to teach."""
    answers = {f"shop{i}": f"{c['name']} ${c['price']}" for i, c in enumerate(state.get("shop") or [])
               if c and c.get("name_from") == "sprite" and c.get("price") is not None}
    if not answers:
        return []
    capture = CAPTURE_DIR / f"self_taught_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    capture.parent.mkdir(parents=True, exist_ok=True)
    image.save(capture)
    # override: the reader was sure of a wrong letter (H for K); the picture match is the second opinion
    r = apply(capture, answers, override=True)
    what = ", ".join(f"'{state['shop'][int(k[4:])]['raw']}' as {v}" for k, v in answers.items())
    if r.message.startswith("Taught"):
        return [f"Taught myself to read {what} (the card pictures told me the names; capture {capture.name})"]
    capture.unlink(missing_ok=True)
    return [f"Could not teach myself to read {what}: {r.message}"]


def _remember(capture: Path, truths: dict[str, str]) -> None:
    """Keep the lesson so teach.py can replay it (it needs the capture to still be there)."""
    try:
        rel = capture.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = str(capture)
    lessons = load_user_lessons()
    lessons.append({"file": rel, "text": truths, "taught": f"{datetime.now():%Y-%m-%d %H:%M:%S}"})
    USER_LESSONS.parent.mkdir(exist_ok=True)
    USER_LESSONS.write_text(json.dumps(lessons, indent=1), encoding="utf-8")


def load_user_lessons() -> list[dict]:
    """Lessons taught from the control window, in the same shape as teach.LESSONS."""
    if not USER_LESSONS.exists():
        return []
    try:
        return json.loads(USER_LESSONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"warning: {USER_LESSONS} is not readable; its lessons were skipped")
        return []


def main(argv: list[str]) -> int:
    """Teach in the terminal: show each unreadable region's guess, ask for the truth."""
    capture = Path(argv[0]) if argv else newest_stop_capture()
    if capture is None or not Path(capture).exists():
        print("No capture with something teachable. Give one: python stop_lessons.py captures\\unread_x.png")
        return 1
    items = items_for(capture)
    if not items:
        print("Nothing on this capture can be taught here.", *unteachable(capture), sep="\n  ")
        return 1
    answers = {}
    for it in items:
        path = ROOT / "captures" / f"_teach_{it.key}.png"
        it.picture.save(path)
        print(f"\n{it.label}: the bot reads  '{it.guess}'   (picture saved as {path})")
        answers[it.key] = input(f"  what does it really say?  (like: {it.example}; empty to skip) ")
    r = apply(capture, answers)
    if r.override_needed:
        print("\n" + r.message)
        if input("Teach anyway? (y/n) ").strip().lower() == "y":
            r = apply(capture, answers, override=True)
    print("\n" + r.message)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
