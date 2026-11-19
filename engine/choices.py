"""Choices outside the shop: trainer at the start of a run, trinket from a gift.

Trainer: Ed's own record per trainer from the Batopedia (data/trainers.csv: runs
won / runs played, read 2026-11-11), smoothed so one lucky run does not dominate, plus the
bot's own Ranked record per trainer (data/bot_trainer_record.csv, average badges, smoothed),
minus trainers whose ability works against the engine's plan (qualitative lessons
from the September notes, no numbers): Mad Scientist replaces the whole team with
random monsters mid-run, Gentleman removes the cheap monsters merges are built from.

Trinket: each offered trinket is added to the team's trinkets and the team is
scored against today's and tomorrow's opponents in the simulator; the trinket that
helps most wins. Trinkets whose effect is outside battles (income, sell value) get
a money value instead, since the simulator cannot see them.
"""
from __future__ import annotations

import csv
import re

import knowledge

from . import data
from .model import RunState
from .planner import Planner

AVOID_TRAINERS = knowledge.AVOID_TRAINERS
PREFERENCE_BONUS = 0.05     # small tie-breaker for trainers that did well in September

# The bot's own finished Ranked runs per trainer (data/bot_trainer_record.csv, from its logs; 57 runs
# 2026-11-14 to 11-19). Average badges, pulled toward BOT_PRIOR_BADGES by BOT_PRIOR_RUNS imagined runs so a
# trainer seen once or twice barely moves, then BOT_RECORD_WEIGHT x (that average - the prior) / 10 is added.
# (2026-11-19: Burglar 0 of 5 won, 3.2 badges, and 1 badge in each of its 3 Diamond runs, while Ed's
# own record and the meta made it the bot's pick over Gamer and Shopkeeper.)
BOT_RECORD = data.DATA / "bot_trainer_record.csv"
BOT_PRIOR_BADGES = 7.0
BOT_PRIOR_RUNS = 3
BOT_RECORD_WEIGHT = 0.6


def bot_record() -> dict[str, tuple[int, int]]:
    """{trainer: (runs, badges)} from the bot's own Ranked runs."""
    try:
        with open(BOT_RECORD, encoding="utf-8") as f:
            return {r["trainer"]: (int(r["runs"]), int(r["badges"])) for r in csv.DictReader(f)}
    except OSError:
        return {}


def trainer_scores() -> dict[str, float]:
    out = {}
    own = bot_record()
    with open(data.DATA / "trainers.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            won = int(r.get("ed_championships") or 0)
            played = int(r.get("ed_games_played") or 0)
            ed = (won + 1) / (played + 3)          # smoothed win share
            meta_wr = data.trainer_win_rate(r["name"])
            score = 0.5 * ed + 0.5 * (meta_wr * 1.6) if meta_wr is not None else ed
            if r["name"] in AVOID_TRAINERS:
                score -= 1.0
            if r["name"] in knowledge.TRAINER_PREFERENCE:
                score += PREFERENCE_BONUS
            runs, badges = own.get(r["name"], (0, 0))
            avg = (badges + BOT_PRIOR_BADGES * BOT_PRIOR_RUNS) / (runs + BOT_PRIOR_RUNS)
            score += BOT_RECORD_WEIGHT * (avg - BOT_PRIOR_BADGES) / 10.0
            out[r["name"]] = score
    return out


def pick_trainer(offered: list[str | None]) -> tuple[int, str]:
    scores = trainer_scores()
    best = max(range(len(offered)), key=lambda k: scores.get(offered[k] or "", 0.2))
    name = offered[best] or f"card {best}"
    return best, f"{name}: Ed's record, Ranked meta, and fit with the plan (scores {[round(scores.get(o or '', 0.2), 2) for o in offered]})"


_INCOME = re.compile(r"Increase Income by \$(\d+)")
_SIM_SOURCE = (data.DATA.parent / "sim" / "battle.py")


def sim_knows(name: str) -> bool:
    """Trinkets the simulator applies in battle (it names them in its source)."""
    try:
        return f'"{name}"' in _SIM_SOURCE.read_text(encoding="utf-8")
    except OSError:
        return False


# Trinkets that act in the shop (next buy, sell value, rerolls...) are invisible to the
# simulator. They get a small prior from their wording so they are not all scored zero.
_PRIOR_WORDS = [("permanently", 0.02), ("Multicast", 0.02), ("Cooldown Speed", 0.012), ("Damage", 0.01),
                ("free", 0.01), ("Trinket gift", 0.01), ("reroll", 0.006), ("Sell Value", 0.004)]
RARITY_PRIOR = {"Common": 0.0, "Uncommon": 0.004, "Rare": 0.008, "Super Rare": 0.012, "Legendary": 0.016}


def text_prior(name: str) -> float:
    t = data.trinkets().get(name, {})
    effect = t.get("effect", "")
    wr = data.trinket_win_rate(name)
    meta_bonus = max(0.0, (wr - 0.20) * 0.15) if wr is not None else 0.0
    return RARITY_PRIOR.get(t.get("rarity"), 0.0) + sum(v for w, v in _PRIOR_WORDS if w in effect) + meta_bonus


def _cooldown(u) -> float:
    sp = u.species
    return sp.level_cooldown.get(u.level, sp.cooldown) if sp else 0.0


def with_trinket_stats(team: list, name: str, owned: list[str]) -> list:
    """The team's badges as they would read once `name` is owned.

    Live units carry the numbers on their badges, which already include the trinkets owned, so
    the simulator does not apply trinket stat bonuses to them again. A trinket being considered
    is therefore applied here, to the badges. Clubs stack additively (two Giant Clubs: x2.4)."""
    out = []
    for i, u in enumerate(team):
        if u is None or u.species is None:
            out.append(u)
            continue
        d = u.stat_dict()
        types = u.species.types
        if name == "Giant Club" and _cooldown(u) >= 5:
            n = owned.count("Giant Club")
            f = (1 + 0.7 * (n + 1)) / (1 + 0.7 * n)
            d = {k: (v if k == "multicast" else round(v * f)) for k, v in d.items()}
        elif name == "Small Club" and _cooldown(u) >= 5:
            n = owned.count("Small Club")
            d["damage"] = round(d["damage"] * (1 + 0.2 * (n + 1)) / (1 + 0.2 * n))
        elif name == "Wood Sword":
            d["damage"] += 5
        elif name == "Warhorn":                 # +10 Damage every battle, permanently
            d["damage"] += 10
        elif name == "Power Crown" and i == 1:
            d["damage"] += 20
        elif name == "Excalibur":
            d["damage"] += 350
        elif name == "Master Crown" and i == 1:
            n = owned.count("Master Crown")
            f = (1 + 0.8 * (n + 1)) / (1 + 0.8 * n)
            d = {k: (v if k == "multicast" else round(v * f)) for k, v in d.items()}
        elif name == "Fire Orb" and "Fire" in types:
            d["burn"] += 3
        elif name == "Poison Orb" and "Toxic" in types:
            d["poison"] += 3
        elif name == "Terrarium" and "Bug" in types:
            d["damage"] = round(d["damage"] * 1.2)
        out.append(u.with_stats(d))
    return out


_HP = re.compile(r"\+(\d+)(%?) HP")


def trinket_gain(planner: Planner, s: RunState, team: list, base: float, name: str, k: int) -> float:
    """How much owning trinket `name` adds to the team's score (simulator, wording prior, income)."""
    effect = data.trinkets().get(name, {}).get("effect", "")
    t = s.copy()
    t.trinkets = s.trinkets + [name]
    hp = _HP.search(effect)                 # Training Weights, Barbell, the HP Charms
    if hp and t.hp_today:
        n = int(hp.group(1))
        t.hp_today = int(t.hp_today * (1 + n / 100)) if hp.group(2) else t.hp_today + n
    gain = planner.team_score(t, with_trinket_stats(team, name, s.trinkets), k) - base
    if not sim_knows(name):
        gain += text_prior(name)
    m = _INCOME.search(effect)
    if m:
        gain += planner.p.money * int(m.group(1)) * max(1, data.BADGES_TO_WIN - s.badges)
    return gain


def pick_trinket(planner: Planner, s: RunState, offered: list[str | None]) -> tuple[int, str]:
    team, _ = planner.final_layout(s)
    k = planner.p.k_final
    base = planner.team_score(s, team, k)
    results = []
    for j, name in enumerate(offered):
        if not name:
            results.append((-1.0, j, "unreadable"))
            continue
        gain = trinket_gain(planner, s, team, base, name, k)
        results.append((gain, j, f"{name} {gain:+.3f}"))
    results.sort(reverse=True)
    return results[0][1], "; ".join(r[2] for r in results)
