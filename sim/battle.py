"""Offline Batomon battle simulator.

A team is a dict (or the JSON form of one):

    {
      "trainer": "Rich Lady",            # optional
      "hp": 500,                          # trainer HP (or "day": 2 to use the day table)
      "trinkets": ["Rally Flag"],         # optional
      "coffee": false,                    # optional: Coffee item used before this battle
      "slots": [                          # 6 entries: top row left->right, then bottom row
        {"name": "Brawlmantis", "level": 1, "stats": {"damage": 60}},
        null, ...
      ]
    }

A slot's "stats" (the badges on the shop screen) replace the Batopedia numbers when given.
Optional per-slot keys: "cooldown", "multicast", "speed" (% cooldown speed), "protect",
"shiny" (true: a self-knockout monster keeps casting).

`simulate(team_a, team_b)` plays the battle and returns a BattleResult. Rules and guesses are
listed in sim/rules.py.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import data
from .rules import SETTINGS

EPS = 1e-9

# Static trinket bonuses that change a card's badges (applied only when a slot has no "stats").
STATIC_STAT_TRINKETS = {"Wood Sword", "Small Club", "Giant Club", "Terrarium", "Fire Orb",
                        "Poison Orb", "Power Crown", "Excalibur", "Master Crown",
                        "Scrap Sword", "Fancy Sword"}
STATIC_STAT_TRAINERS = {"Chef", "Chemist", "Musician", "Redhead"}


@dataclass
class Unit:
    side: int
    slot: int
    name: str
    level: int
    types: list[str]
    rarity: str
    cooldown: float
    stats: dict[str, float]
    multicast: int
    passive: str
    param: float | None
    ongoing: bool
    has_ability: bool
    speed: float = 0.0            # % cooldown speed for this battle
    progress: float = 0.0         # seconds of cooldown done
    alive: bool = True
    disabled: bool = False        # abilities disabled (Celestia)
    protect: int = 0
    shiny: bool = False           # Shiny copy: the self-knockout drawback is gone (Stingarde, seen 2026-11-12)
    extra_multicast: int = 0
    base_damage: float = 0.0      # starting battle Damage (for % gains like Oniclaw)
    shelldra_next: float = 4.0
    casts: int = 0
    triggers: int = 0
    dealt: float = 0.0
    healed: float = 0.0
    shielded: float = 0.0
    inflicted: dict[str, float] = field(default_factory=lambda: {"poison": 0, "burn": 0, "shock": 0})
    base_name: str = field(init=False)
    t0_dmg: float = 0.0
    t0_heal: float = 0.0
    t0_burn: float = 0.0
    t0_shock: float = 0.0
    t0_mc: int = 0
    _speed_cached: float = -1.0

    def __post_init__(self) -> None:
        self.base_name = data.canonical_name(self.name)

    @property
    def row(self) -> int:
        return self.slot // 3

    @property
    def col(self) -> int:
        return self.slot % 3

    def is_(self, name: str) -> bool:
        return self.base_name == name

    def active_passive(self) -> bool:
        return self.alive and not self.disabled

    def evolved(self) -> bool:
        return self.base_name in data.EVOLVES and self.level >= 3

    def summary(self) -> dict:
        out = {"slot": self.slot + 1, "name": self.name, "level": self.level,
               "triggered": self.triggers, "dealt": round(self.dealt), "healed": round(self.healed)}
        if self.shielded:
            out["shielded"] = round(self.shielded)
        for k, v in self.inflicted.items():
            if v:
                out[f"inflicted_{k}"] = round(v)
        if not self.alive:
            out["knocked_out"] = True
        out["stats"] = {k: round(v) for k, v in self.stats.items() if v}
        return out


@dataclass
class Trainer:
    side: int
    name: str
    max_hp: float
    hp: float
    trinkets: list[str]
    shield: float = 0.0
    poison: float = 0.0
    burn: float = 0.0
    shock: float = 0.0
    shock_pending: float = 0.0    # Shock gained this instant; it boosts hits only from the next instant on
    heal_mult: float = 1.0
    shield_mult: float = 1.0
    link_cable: bool = False
    metronome: int = 0
    badges: int = 0
    mythical_items: int = 0
    zenith_mult: float = 1.0


@dataclass
class BattleResult:
    winner: str            # "a", "b", "draw", or "none" (stopped early with `until`)
    time: float
    hp: tuple[float, float]
    max_hp: tuple[float, float]
    statuses: tuple[dict, dict]
    units: tuple[list[dict], list[dict]]
    log: list[str]

    def as_dict(self) -> dict:
        return {"winner": self.winner, "time": round(self.time, 2),
                "hp": [round(h) for h in self.hp], "max_hp": list(self.max_hp),
                "statuses": list(self.statuses), "units": list(self.units)}


# What a Shiny copy changes, from the cards seen in the game (2026-11-12). Each species changes
# something different; species not listed here are simulated like the normal card.
#  - self-knockout monsters keep casting (Shiny Stingarde's card has no "Knockout self"; Electranade
#    and Pyronade are assumed to work the same way)
#  - monsters without a passive gain a small permanent growth at battle start
#  - Stalagrove's 15% became 18% (x1.2 on its parameter)
#  - other numbers go up by about 20%: Cobrex Poison 300 -> 360 (on the badge, so already read),
#    Geminiss +50% -> +60% Shield, Blixie gives 1.2x its Burn instead of 1x, Torrantler's
#    cooldown 7 -> 5.5 s (cards seen through a Glimmer Potion)
SHINY_BATTLE_START = {"Magmite": {"shield": 10, "burn": 1}, "Venopuff": {"poison": 1}}
SHINY_PARAM_MULT = {"Stalagrove": 1.2, "Geminiss": 1.2, "Blixie": 1.2}
SHINY_COOLDOWN_MULT = {"Torrantler": 5.5 / 7.0}


def build_unit(side: int, slot: int, spec: dict, trainer: str | None, trinkets: list[str],
               victories: int = 0) -> Unit:
    name = spec["name"]
    level = int(spec.get("level", 1))
    sp = data.species(name)
    lv = sp.levels[min(max(level, 1), 4)]
    stats = dict(lv.stats)
    given = spec.get("stats")
    if given:
        stats = {s: float(given.get(s, 0)) for s in data.STATS}
    types = list(sp.types)
    multicast = int(spec.get("multicast", lv.multicast))
    unit = Unit(side, slot, name, level, types, sp.rarity, float(spec.get("cooldown", lv.cooldown)),
                stats, multicast, lv.passive_text if level > 1 else sp.passive, lv.param,
                sp.is_ongoing, sp.has_ability() and not (sp.name in data.EVOLVES and level < 3),
                speed=float(spec.get("speed", 0)), protect=int(spec.get("protect", 0)),
                shiny=bool(spec.get("shiny", False)))
    if unit.shiny and unit.param and sp.name in SHINY_PARAM_MULT:
        unit.param *= SHINY_PARAM_MULT[sp.name]
    if unit.shiny and sp.name in SHINY_COOLDOWN_MULT and "cooldown" not in spec:
        unit.cooldown *= SHINY_COOLDOWN_MULT[sp.name]
    # Trainer / trinket bonuses that the badges would already show.
    if not given:
        if trainer == "Chef":
            if len(types) == 1 and "Fire" not in types:
                unit.types.append("Fire")
            if "Fire" in unit.types:
                unit.stats["burn"] += 2
        if trainer == "Chemist" and "Toxic" in unit.types:
            unit.stats["poison"] += 3
        if trainer == "Redhead" and "Fire" in unit.types and victories:
            unit.stats["burn"] += 2 * victories
        for t in trinkets:
            if t == "Wood Sword":
                unit.stats["damage"] += 5
            elif t == "Fire Orb" and "Fire" in unit.types:
                unit.stats["burn"] += 3
            elif t == "Poison Orb" and "Toxic" in unit.types:
                unit.stats["poison"] += 3
            elif t == "Power Crown" and slot == 1:
                unit.stats["damage"] += 20
            elif t == "Excalibur":
                unit.stats["damage"] += 350
            elif t == "Terrarium" and "Bug" in unit.types:
                unit.stats["damage"] *= 1.2
        n_scrap = trinkets.count("Scrap Sword")
        if n_scrap:
            rarities = data.trinket_rarities()
            n_common = sum(1 for x in trinkets if rarities.get(x) == "Common")
            unit.stats["damage"] += 2 * n_common * n_scrap
        n_fancy = trinkets.count("Fancy Sword")
        if n_fancy:
            unit.stats["damage"] *= 1 + 0.03 * len(trinkets) * n_fancy
        if slot == 1:
            n_master = trinkets.count("Master Crown")
            if n_master:
                for s in data.STATS:
                    unit.stats[s] *= 1 + 0.8 * n_master
        # Clubs stack additively: two Giant Clubs gave x2.4, not x1.7^2 (Cobrex Lv2 Shiny: 600 x 1.2 x 2.4
        # = 1728 on its card, 2026-11-12). Small Club is assumed to stack the same way.
        if unit.cooldown >= 5:
            n_small, n_giant = trinkets.count("Small Club"), trinkets.count("Giant Club")
            if n_small:
                unit.stats["damage"] *= 1 + 0.2 * n_small
            if n_giant:
                for s in data.STATS:
                    unit.stats[s] *= 1 + 0.7 * n_giant
    elif trainer == "Chef" and len(types) == 1 and "Fire" not in types:
        unit.types.append("Fire")
    # Cooldown and Multicast bonuses (not visible on badges).
    if "multicast" not in spec:
        for t in trinkets:
            if t == "Razor Beak" and "Flying" in unit.types:
                unit.multicast += 1
            elif t == "Winged Crown" and slot == 1:
                unit.multicast += 1
    if "speed" not in spec:
        for t in trinkets:
            if t == "Quick Flag" and unit.col == 0:
                unit.speed += 4
            elif t == "Speed Crest" and unit.col == 2:
                unit.speed += 15
            elif t == "Haste Orb":
                unit.speed += 10
            elif t == "Haste Crown" and slot == 1:
                unit.speed += 20
    unit.base_damage = unit.stats["damage"]
    return unit


class Battle:
    def __init__(self, team_a: dict, team_b: dict, seed: int = 0, verbose: bool = False,
                 until: float | None = None, immortal: bool = False):
        self.until = until
        self.immortal = immortal
        self.rng = random.Random(seed)
        self.verbose = verbose
        self.log: list[str] = []
        self.t = 0.0
        self.trainers = [self._trainer(0, team_a), self._trainer(1, team_b)]
        self.teams: list[list[Unit | None]] = [self._units(0, team_a), self._units(1, team_b)]
        for side, tm in ((0, team_a), (1, team_b)):
            if tm.get("magic_lasso"):
                self._apply_magic_lasso(side)
        self.coffee = [bool(team_a.get("coffee")), bool(team_b.get("coffee"))]
        self.at_battle_start = [bool(team_a.get("at_battle_start")), bool(team_b.get("at_battle_start"))]
        self.next_poison = SETTINGS["poison_interval"]
        self.next_burn = SETTINGS["burn_interval"]
        self.next_sd = SETTINGS["sudden_death_start"]
        self.depth = 0
        self._adj: dict[int, list[Unit]] = {}
        self._living: list[list[Unit] | None] = [None, None]   # cached living(side); reset by knockout()
        self._dmg_visiting: set[int] = set()
        self._side_names: list[set[str]] = [
            {u.base_name for u in self.teams[0] if u},
            {u.base_name for u in self.teams[1] if u},
        ]
        self._all_names: set[str] = self._side_names[0] | self._side_names[1]
        self._has_shelldra: bool = "Shelldra" in self._all_names

    # ---------- setup ----------
    def _trainer(self, side: int, team: dict) -> Trainer:
        hp = team.get("hp")
        if hp is None:
            hp = data.DAY_HP[int(team["day"])]
        trinkets = list(team.get("trinkets", []))
        tr = Trainer(side, team.get("trainer") or "", float(hp), float(team.get("hp_now", hp)), trinkets)
        if "Sapphire Amulet" in trinkets:
            tr.heal_mult = 1.25
        if "Topaz Amulet" in trinkets:
            tr.shield_mult = 1.25
        tr.link_cable = "Link Cable" in trinkets
        tr.metronome = trinkets.count("Metronome")
        tr.badges = int(team.get("badges") or 0)
        tr.mythical_items = int(team.get("mythical_items") or 0)
        tr.zenith_mult = 1.0 + 0.8 * trinkets.count("Zenith Stone")
        return tr

    def _units(self, side: int, team: dict) -> list[Unit | None]:
        slots = list(team.get("slots", []))[:6]
        slots += [None] * (6 - len(slots))
        trainer = team.get("trainer")
        trinkets = list(team.get("trinkets", []))
        victories = int(team.get("victories") if team.get("victories") is not None else (team.get("badges") or 0))
        out = []
        for i, s in enumerate(slots):
            out.append(build_unit(side, i, s, trainer, trinkets, victories=victories) if s else None)
        if trainer == "Musician" and not any(s and s.get("stats") for s in slots if s):
            electric = [u for u in out if u and "Electric" in u.types]
            if len(electric) == 1:
                electric[0].stats["shock"] *= 2.5
        return out

    def _apply_magic_lasso(self, side: int) -> None:
        """Magic Lasso: gain an exact copy of the enemy's top-middle monster in the first empty slot."""
        foe_mid = self.teams[1 - side][1]
        if foe_mid is None:
            return
        for idx, u in enumerate(self.teams[side]):
            if u is None:
                copy_u = Unit(
                    side, idx, foe_mid.name, foe_mid.level, list(foe_mid.types),
                    foe_mid.rarity, foe_mid.cooldown, dict(foe_mid.stats),
                    foe_mid.multicast, foe_mid.passive, foe_mid.param,
                    foe_mid.ongoing, foe_mid.has_ability,
                    speed=foe_mid.speed, protect=foe_mid.protect, shiny=foe_mid.shiny,
                    base_damage=foe_mid.base_damage,
                )
                self.teams[side][idx] = copy_u
                return

    def say(self, msg: str) -> None:
        if self.verbose:
            self.log.append(f"{self.t:6.2f}  {msg}")

    # ---------- geometry ----------
    def unit_at(self, side: int, row: int, col: int) -> Unit | None:
        if not (0 <= row < 2 and 0 <= col < 3):
            return None
        return self.teams[side][row * 3 + col]

    def behind(self, u: Unit) -> Unit | None:
        return self.unit_at(u.side, u.row, u.col - 1)

    def in_front(self, u: Unit) -> Unit | None:
        return self.unit_at(u.side, u.row, u.col + 1)

    def above(self, u: Unit) -> Unit | None:
        return self.unit_at(u.side, u.row - 1, u.col) if u.row == 1 else None

    def adjacent(self, u: Unit) -> list[Unit]:
        # Positions never change during a battle, so the neighbour list is computed once.
        cached = self._adj.get(id(u))
        if cached is not None:
            return cached
        if self.trainers[u.side].link_cable:
            out = [v for v in self.teams[u.side] if v and v is not u]
        else:
            cells = [(u.row, u.col - 1), (u.row, u.col + 1), (u.row - 1, u.col), (u.row + 1, u.col)]
            out = [v for v in (self.unit_at(u.side, r, c) for r, c in cells) if v]
        self._adj[id(u)] = out
        return out

    def speeds_changed(self) -> None:
        """Call after anything that can change a cooldown speed: a speed buff, a knockout."""
        for side in (0, 1):
            for u in self.teams[side]:
                if u is not None:
                    u._speed_cached = -1.0

    def speed(self, u: Unit) -> float:
        """speed_of(u), cached until speeds_changed() is called, and capped so the effective
        cooldown never drops below SETTINGS["min_cooldown"]."""
        v = u._speed_cached
        if v < 0.0:
            v = min(self.speed_of(u), u.cooldown / SETTINGS["min_cooldown"])
            u._speed_cached = v
        return v

    def allies(self, u: Unit) -> list[Unit]:
        return [v for v in self.teams[u.side] if v and v is not u]

    def living(self, side: int) -> list[Unit]:
        """The units still standing (a cached list: do not change it; knockout() starts a new one)."""
        c = self._living[side]
        if c is None:
            c = self._living[side] = [u for u in self.teams[side] if u and u.alive]
        return c

    # ---------- ongoing effects ----------
    def ongoing_mult(self, u: Unit) -> int:
        """How many times u's Ongoing ability applies (Onsetra in front of it adds more)."""
        n = 1
        front = self.in_front(u)
        if front and front.is_("Onsetra") and front.active_passive() and front.param:
            n += int(front.param)
        return n

    def ongoing_active(self, u: Unit) -> bool:
        return u.ongoing and u.active_passive()

    def speed_of(self, u: Unit) -> float:
        bonus = u.speed
        for v in self.adjacent(u):
            if v.is_("Formiqueen") and self.ongoing_active(v) and u.rarity == "Common" and v.param:
                bonus += v.param * self.ongoing_mult(v)
            if v.is_("Shrinell") and v.shiny and self.ongoing_active(v) and v.param:
                bonus += v.param * len(self.trainers[u.side].trinkets) * self.ongoing_mult(v)
        front = self.in_front(u)
        if front and front.is_("Shrinell") and not front.shiny and self.ongoing_active(front) and front.param:
            bonus += front.param * len(self.trainers[u.side].trinkets) * self.ongoing_mult(front)
        return 1.0 + bonus / 100.0

    def damage_of(self, u: Unit) -> float:
        if u.t0_dmg:
            return max(0.0, self._raw_damage_of(u) - u.t0_dmg)
        return self._raw_damage_of(u)

    def _raw_damage_of(self, u: Unit) -> float:
        dmg = u.stats["damage"]
        if self.ongoing_active(u) and u.param is not None and id(u) not in self._dmg_visiting:
            self._dmg_visiting.add(id(u))
            try:
                k = self.ongoing_mult(u)
                if u.is_("Lignite"):
                    dmg += u.param * k * u.stats["burn"]
                elif u.is_("Fumungus"):
                    dmg += u.param / 100.0 * k * self.trainers[1 - u.side].poison
                elif u.is_("Gaiadrasil"):
                    # allies' full Damage, Ongoing extras included (Fumungus's Poison-based Damage counted
                    # in the game: one cast hit 3375 where the allies' badges summed to ~2450, 2026-11-12)
                    dmg += u.param / 100.0 * k * sum(self.damage_of(v) for v in self.adjacent(u)
                                                     if not v.is_("Gaiadrasil") and v.alive)
                elif u.is_("Quillustrous"):
                    dmg += u.param / 100.0 * k * sum(self.damage_of(v) for v in self.allies(u)
                                                     if not v.is_("Quillustrous") and v.alive)
                elif u.is_("Orcana"):
                    dmg += u.param * k * sum(1 for v in self.allies(u) if v.alive and v.level >= 3)
                elif u.is_("Tengusto"):
                    dmg += u.param * k * self.trainers[u.side].badges
            finally:
                self._dmg_visiting.discard(id(u))
        m_items = self.trainers[u.side].mythical_items
        if m_items and "Faebloom" in self._side_names[u.side]:
            pct = 0.0
            for v in self.adjacent(u):
                if v.is_("Faebloom") and self.ongoing_active(v) and v.param:
                    pct += (v.param / 100.0) * self.ongoing_mult(v) * m_items
            if pct:
                dmg *= 1.0 + pct
        return dmg

    def heal_of(self, u: Unit) -> float:
        if u.t0_heal:
            return max(0.0, self._raw_heal_of(u) - u.t0_heal)
        return self._raw_heal_of(u)

    def _raw_heal_of(self, u: Unit) -> float:
        heal = u.stats["heal"]
        if not self.ongoing_active(u) or u.param is None:
            return heal
        k = self.ongoing_mult(u)
        if u.is_("Orcana"):
            heal += u.param * k * sum(1 for v in self.allies(u) if v.alive and v.level >= 3)
        return heal

    def shield_of(self, u: Unit) -> float:
        shld = u.stats["shield"]
        if not self.ongoing_active(u) or u.param is None:
            return shld
        k = self.ongoing_mult(u)
        if u.is_("Cinnabark"):
            shld += u.param / 100.0 * k * self.trainers[1 - u.side].poison
        return shld

    def burn_of(self, u: Unit) -> float:
        if u.t0_burn:
            return max(0.0, self._raw_burn_of(u) - u.t0_burn)
        return self._raw_burn_of(u)

    def _raw_burn_of(self, u: Unit) -> float:
        burn = u.stats["burn"]
        if not self.ongoing_active(u) or u.param is None:
            return burn
        k = self.ongoing_mult(u)
        if u.is_("Ouroblaze"):
            burn += u.param / 100.0 * k * self.trainers[1 - u.side].burn
        elif u.is_("Furnadon"):
            burn += u.param * k * len(set(self.trainers[u.side].trinkets))
        return burn

    def shock_of(self, u: Unit) -> float:
        if u.t0_shock:
            return max(0.0, self._raw_shock_of(u) - u.t0_shock)
        return self._raw_shock_of(u)

    def _raw_shock_of(self, u: Unit) -> float:
        shock = u.stats["shock"]
        b = self.in_front(u)   # Pylong boosts the ally behind it, i.e. Pylong is in front of u
        if b and b.is_("Pylong") and self.ongoing_active(b) and b.param:
            shock *= 1 + b.param / 100.0 * self.ongoing_mult(b)
        return shock

    def multicast_of(self, u: Unit) -> int:
        if u.t0_mc:
            return max(1, self._raw_multicast_of(u) - u.t0_mc)
        return self._raw_multicast_of(u)

    def _raw_multicast_of(self, u: Unit) -> int:
        mc = u.multicast + u.extra_multicast
        if not u.has_ability:
            for v in self.adjacent(u):
                if v.is_("Stellagon") and self.ongoing_active(v) and v.param:
                    mc += int(v.param) * self.ongoing_mult(v)
        if u.is_("Zephyrex"):
            mc = 1
        return max(mc, 1)

    # ---------- damage and statuses ----------
    def hit(self, target: Trainer, amount: float, source: Unit | None) -> float:
        if amount <= 0:
            return 0.0
        if target.shock > 0:
            mode = SETTINGS["shock_mode"]
            if mode == "flat":
                amount += target.shock
            elif mode == "percent":
                amount *= 1 + target.shock / 100.0
        self._take(target, amount)
        return amount

    def status_damage(self, target: Trainer, amount: float) -> None:
        if amount <= 0:
            return
        if target.shield > 0:
            amount *= SETTINGS["status_vs_shield"]
        self._take(target, amount)

    def _take(self, target: Trainer, amount: float) -> None:
        absorbed = min(target.shield, amount)
        target.shield -= absorbed
        target.hp -= amount - absorbed

    def heal(self, tr: Trainer, amount: float) -> None:
        if tr.hp <= EPS and not self.immortal:
            return                   # a trainer at 0 HP is out; a heal later in the same instant cannot revive it
        blocked_at = SETTINGS.get("heal_blocked_at_poison")
        if blocked_at is not None and tr.poison >= blocked_at:
            return                   # GUESS: heavy Poison stops heals (sim/rules.py)
        amount *= tr.heal_mult
        tr.hp += amount
        if SETTINGS["heal_cap"]:
            tr.hp = min(tr.hp, tr.max_hp)

    def gain_shield(self, tr: Trainer, amount: float) -> None:
        amount *= tr.shield_mult
        tr.shield += amount
        if "Stalagrove" in self._side_names[tr.side]:
            for v in self.teams[tr.side]:
                if v and v.is_("Stalagrove") and v.active_passive() and v.param:
                    v.stats["damage"] += v.param / 100.0 * amount

    def knockout(self, u: Unit) -> None:
        if not u.alive:
            return
        if u.protect > 0:
            u.protect -= 1
            self.say(f"{u.name} (side {u.side}) Protect blocks a Knockout")
            return
        u.alive = False
        self._living = [None, None]
        self.speeds_changed()
        if u.is_("Emburn") and not u.disabled and u.param:
            self._gain_stat(u, "burn", u.param)
        side_names = self._side_names[u.side]
        if "Danuki" in side_names or "Emburn" in side_names:
            for v in self.allies(u):
                if v.is_("Danuki") and not u.is_("Danuki") and v.active_passive():
                    v.stats["damage"] += ((v.param or 85.0) / 100.0) * u.stats["damage"]
                if v.is_("Emburn") and v.active_passive() and v.param:
                    self._gain_stat(v, "burn", v.param)
        if "Cawnushi" in self._all_names:
            for side in (0, 1):
                if "Cawnushi" not in self._side_names[side]:
                    continue
                for v in self.teams[side]:
                    if v and v is not u and v.is_("Cawnushi") and v.active_passive() and v.param:
                        self._gain_stat(v, "damage", v.param)
        self.say(f"{u.name} (side {u.side}) is knocked out")

    def _gain_stat(self, u: Unit, stat: str, amount: float) -> None:
        """Apply a flat stat gain in battle, scaled by Zenith Stone (+80% per copy)."""
        u.stats[stat] += amount * self.trainers[u.side].zenith_mult

    def charge(self, u: Unit, seconds: float) -> None:
        if u.alive and u.base_name not in ("Dracana", "Ironcore", "Steamscuttle"):
            u.progress = min(u.cooldown, u.progress + seconds)
            if u.progress >= u.cooldown - 1e-7:
                u.progress = 0.0
                self.cast(u, "charge")

    def _level_up(self, u: Unit, target_level: int) -> None:
        """Level up u in battle to target_level, applying the species' level deltas."""
        if u.level >= target_level:
            return
        sp = data.species(u.name)
        old_lv = sp.levels[min(max(u.level, 1), 4)]
        new_lv = sp.levels[min(max(target_level, 1), 4)]
        for sk in data.STATS:
            u.stats[sk] += new_lv.stats[sk] - old_lv.stats[sk]
        u.multicast += new_lv.multicast - old_lv.multicast
        if new_lv.cooldown != old_lv.cooldown:
            u.cooldown = new_lv.cooldown
            u.progress = min(u.progress, u.cooldown)
        u.level = target_level
        u.param = new_lv.param
        u.passive = new_lv.passive_text if target_level > 1 else sp.passive
        u.has_ability = sp.has_ability() and not (sp.name in data.EVOLVES and target_level < 3)

    # ---------- casting ----------
    def cast(self, u: Unit, reason: str = "cooldown") -> None:
        if not u.alive:
            return
        if self.depth >= SETTINGS["max_trigger_depth"]:
            return
        self.depth += 1
        try:
            self._cast(u, reason)
        finally:
            self.depth -= 1

    def _cast(self, u: Unit, reason: str) -> None:
        me, foe = self.trainers[u.side], self.trainers[1 - u.side]
        mc = self.multicast_of(u)
        u.casts += 1
        u.triggers += 1          # the Stats screen counts casts, not Multicast hits (Velocect x2: 4 casts = "4 times")
        applied_shield = False
        for _ in range(mc):
            dmg = self.damage_of(u)
            if dmg > 0:
                u.dealt += self.hit(foe, dmg, u)   # "Dealt" on the Stats screen includes the Shock bonus
            hl = self.heal_of(u)
            if hl > 0:
                self.heal(me, hl)
                u.healed += hl
            sh = self.shield_of(u)
            if sh > 0:
                self.gain_shield(me, sh)
                u.shielded += sh
                applied_shield = True
            if u.stats["poison"] > 0:
                foe.poison += u.stats["poison"]
                u.inflicted["poison"] += u.stats["poison"]
                self.on_ally_inflicts(u, "poison")
            brn = self.burn_of(u)
            if brn > 0:
                foe.burn += brn
                u.inflicted["burn"] += brn
                self.on_ally_inflicts(u, "burn")
            shock = self.shock_of(u)
            if shock > 0:
                if SETTINGS.get("shock_next_instant", True):
                    foe.shock_pending += shock
                else:
                    foe.shock += shock
                u.inflicted["shock"] += shock
                self.on_ally_inflicts(u, "shock")
        if self.verbose:
            self.say(f"{u.name} (side {u.side}, slot {u.slot + 1}) casts x{mc} [{reason}] "
                     f"-> HP {self.trainers[0].hp:.0f} / {self.trainers[1].hp:.0f}")
        if me.metronome:
            self._gain_stat(u, "damage", 10 * me.metronome)
        if u.active_passive():
            self.on_cast(u)
        side_names = self._side_names[u.side]
        # Puffloon: trigger when adjacent Toxic allies trigger.
        if "Puffloon" in side_names and "Toxic" in u.types and not u.is_("Puffloon"):
            for v in self.adjacent(u):
                if v.is_("Puffloon") and v.active_passive():
                    self.cast(v, f"{u.name} triggered")
        # Drumire: give casting Toxic ally Cooldown Speed for this battle.
        if "Drumire" in side_names and "Toxic" in u.types and u.alive:
            gained_spd = False
            for v in self.allies(u):
                if v.is_("Drumire") and v.active_passive() and v.param:
                    u.speed += v.param
                    gained_spd = True
            if gained_spd:
                self.speeds_changed()
        # Snapscald: trigger when an ally of level 3 or above casts.
        if "Snapscald" in side_names and u.level >= 3 and not u.is_("Snapscald"):
            for v in self.allies(u):
                if v.is_("Snapscald") and v.active_passive():
                    self.cast(v, f"{u.name} triggered")
        # Rhizuka: trigger when an ally applies Shield.
        if "Rhizuka" in side_names and applied_shield and not u.is_("Rhizuka"):
            for v in self.allies(u):
                if v.is_("Rhizuka") and v.active_passive():
                    self.cast(v, f"{u.name} triggered")

    def on_ally_inflicts(self, u: Unit, kind: str) -> None:
        side_names = self._side_names[u.side]
        if kind == "burn" and "Oniclaw" in side_names:
            for v in self.living(u.side):
                if v.is_("Oniclaw") and v.active_passive() and v.param:
                    v.stats["damage"] += v.base_damage * (v.param / 100.0)
        if kind == "poison" and ("Thorntail" in side_names or "Cobrex" in side_names):
            for v in self.allies(u):
                if not v.active_passive():
                    continue
                if v.is_("Thorntail") and v.param:
                    self._gain_stat(v, "damage", v.param)
                if v.is_("Cobrex") and v.param:
                    self.charge(v, v.param)
        elif kind == "shock" and "Clawnetic" in side_names:
            for v in self.allies(u):
                if v.active_passive() and v.is_("Clawnetic") and v.param:
                    self.charge(v, v.param)

    def on_cast(self, u: Unit) -> None:
        p = u.param or 0
        name = u.base_name
        me = self.trainers[u.side]
        if name == "Mosslug":
            self._gain_stat(u, "damage", p)
        elif name == "Pebbler":
            self._gain_stat(u, "shield", p)
        elif name == "Pyrokami":          # Jinto: "On Cast: +10 Burn for this battle"
            self._gain_stat(u, "burn", p)
        elif name in ("Stingarde", "Electranade", "Pyronade"):
            if not u.shiny:          # a Shiny copy keeps casting (Shiny Stingarde: 3 casts, 450 damage)
                self.knockout(u)
        elif name == "Boomagon":
            b = self.behind(u)
            if b:
                b.speed += p
                self.speeds_changed()
        elif name == "Noxnimbus":
            for v in self.adjacent(u):
                if "Toxic" in v.types:
                    self._gain_stat(v, "poison", p)
        elif name == "Runerock":
            for s in ("poison", "burn", "shock"):
                setattr(me, s, max(0.0, getattr(me, s) - p))
        elif name == "Cherubble":
            for v in self.adjacent(u):
                v.protect += int(p)
        elif name == "Cicadence":
            a = self.above(u)
            if a and "Bug" in a.types:
                self.cast(a, "Cicadence")
        elif name == "Dirgefin":
            for side in (0, 1):
                for v in self.teams[side]:
                    if v and v is not u and v.rarity == "Common":
                        self.knockout(v)
        elif name == "Dracana":
            b = self.behind(u)
            if b and not b.is_("Dracana"):
                self.charge(b, p)
        elif name == "Ironcore":
            for v in self.adjacent(u):
                if "Electric" in v.types and not v.is_("Ironcore"):
                    self.charge(v, p)
        elif name == "Magmalith":
            a = self.above(u)
            if a and "Fire" in a.types:
                self._gain_stat(a, "burn", p)
        elif name == "Noxalith":
            a = self.above(u)
            if a and "Toxic" in a.types:
                self._gain_stat(a, "poison", p)
        elif name == "Dryadell":
            a = self.above(u)
            if a and "Grass" in a.types:
                self.cast(a, "Dryadell")
        elif name == "Prismagon":
            kinds = {t for v in self.living(u.side) for t in v.types}
            self._gain_stat(u, "damage", p * len(kinds))
        elif name == "Reapra":
            enemies = [v for v in self.teams[1 - u.side] if v and v.alive]
            if u.level >= 4 and enemies:
                self.knockout(self.rng.choice(enemies))
            else:
                v = self.teams[1 - u.side][u.slot]
                if v:
                    self.knockout(v)
        elif name == "Sirenade":
            for s in ("poison", "burn", "shock"):
                cur = getattr(me, s)
                setattr(me, s, max(0.0, cur - math.ceil(cur * p / 100.0)))
        elif name == "Zephyrex":
            f = self.in_front(u)
            if f and "Flying" in f.types and not f.is_("Zephyrex"):
                f.extra_multicast += int(p)
        elif name == "Blixie":
            b = self.behind(u)
            if b and "Fire" in b.types:
                b.stats["burn"] += p * u.stats["burn"]
        elif name == "Draconarch":
            for v in self.adjacent(u):
                if v.active_passive():
                    self.battle_start(v, from_trigger=True)
            u.cooldown += p
        elif name == "Galvanine":
            u.speed += 50
            self.speeds_changed()
            self._gain_stat(u, "shock", p)
        elif name == "Geminiss":
            for v in self.adjacent(u):
                v.stats["shield"] *= 1 + p / 100.0
        elif name == "Saberhorn":
            f = self.in_front(u)
            if f:
                f.extra_multicast += int(p)
            u.cooldown += 8
        elif name == "Torrantler":
            for v in self.adjacent(u):
                if "Water" in v.types and not v.is_("Torrantler"):
                    self.cast(v, "Torrantler")
        elif name == "Panbud" and u.evolved():
            self._gain_stat(u, "damage", p)
        elif name == "Bonshell":
            self._gain_stat(u, "damage", p)
            self._gain_stat(u, "shield", p)
        elif name == "Shellter":
            u.stats["shield"] = max(0.0, u.stats["shield"] - p)
        elif name == "Cairnage":
            for v in self.allies(u):
                if v.alive:
                    v.stats["damage"] += p * self.shield_of(v)
        elif name == "Opalion":
            rocks = [v for v in self.allies(u) if v.alive and "Rock" in v.types and not v.is_("Opalion")]
            if rocks:
                self.cast(self.rng.choice(rocks), "Opalion")
        elif name == "Steamscuttle":
            for v in self.adjacent(u):
                if "Fire" in v.types or "Water" in v.types:
                    self.charge(v, p)
        elif name == "Shikitsune":
            revived = False
            for v in self.allies(u):
                if not v.alive:
                    v.alive = True
                    v.speed += p
                    revived = True
            if revived:
                self._living = [None, None]
                self.speeds_changed()
        elif name == "Petrirex":
            for v in list(self.adjacent(u)):
                was_alive = v.alive
                self.knockout(v)
                if was_alive and not v.alive:
                    self._gain_stat(u, "shield", p)
        elif name == "Vengrieve":
            enemies = [v for v in self.teams[1 - u.side] if v and v.alive]
            target = None
            if u.level >= 4 and enemies:
                target = self.rng.choice(enemies)
            else:
                v = self.teams[1 - u.side][u.slot]
                if v and v.alive:
                    target = v
            if target is not None:
                for sk in data.STATS:
                    u.stats[sk] += target.stats[sk]
                self.knockout(target)
        elif name == "Sukoi" and u.casts == 1:
            a = self.above(u)
            if a and a.level < 3:
                self._level_up(a, a.level + 1)

    # ---------- battle start ----------
    def battle_start(self, u: Unit, from_trigger: bool = False) -> None:
        bs_only = self.at_battle_start[u.side] and not from_trigger
        if u.shiny and not bs_only:
            self.shiny_battle_start(u)
        if not u.active_passive():
            return
        p = u.param or 0
        name = u.base_name
        if name in ("Frizzly", "Coalem"):
            self.cast(u, "battle start")
        elif name == "Aster" and not bs_only:
            for v in self.adjacent(u):
                if "Water" in v.types:
                    self._gain_stat(v, "heal", p)
        elif name == "Aegistruct" and not bs_only:
            gain = sum(v.stats["shield"] for v in self.adjacent(u) if not v.is_("Aegistruct"))
            u.stats["shield"] += p * gain
        elif name == "Dribblet" and u.evolved() and not bs_only:
            self._gain_stat(u, "damage", p)
            self._gain_stat(u, "heal", p)
        elif name == "Scorchimp" and u.evolved():
            fire = sum(1 for v in self.allies(u) if "Fire" in v.types)
            u.speed += p * fire
            self.speeds_changed()
        elif name == "Miasmaw" and not bs_only:
            u.stats["poison"] += p * sum(v.stats["poison"] for v in self.allies(u) if not v.is_("Miasmaw"))
        elif name == "Ginsage":
            for v in self.adjacent(u):
                if "Grass" in v.types:
                    v.speed += p
            self.speeds_changed()
        elif name == "Lumijel" and not bs_only:
            for v in self.allies(u):
                if v.level >= 3:
                    self._gain_stat(v, "damage", p)
                    self._gain_stat(v, "heal", p)
        elif name == "Brimtoad" and not bs_only:
            self._gain_stat(u, "burn", p)
            self._gain_stat(u, "poison", p)
        elif name == "Rattleghast":
            for v in list(self.adjacent(u)):
                was_alive = v.alive
                self.knockout(v)
                if was_alive and not v.alive and not bs_only:
                    self._gain_stat(u, "poison", p)
        elif name == "Talonite" and not bs_only:
            self._gain_stat(u, "shield", 20.0 * sum(1 for v in self.allies(u) if "Rock" in v.types))
            u.multicast += sum(1 for v in self.allies(u) if "Flying" in v.types)
        elif name == "Omnichrome" and not bs_only:
            enemies = self.living(1 - u.side)
            if enemies:
                best = max(enemies, key=lambda v: sum(v.stats.values()))
                mult = (p or 80.0) / 100.0
                for sk in data.STATS:
                    u.stats[sk] += mult * best.stats[sk]
        elif name == "Tsunamere" and not bs_only:
            for v in self.adjacent(u):
                if v.level < 3:
                    self._level_up(v, v.level + 1)

    def trinket_start(self, side: int) -> None:
        tr = self.trainers[side]
        units = self.living(side)
        bs_only = self.at_battle_start[side]
        for t in tr.trinkets:
            if t == "Rally Flag" and not bs_only:
                for u in units:
                    if u.col == 0:
                        u.stats["damage"] += 12
            elif t == "Momentum Flag":
                for u in units:
                    if u.col == 0:
                        u.speed += 8
            elif t == "Earth Crest" and not bs_only:
                for u in units:
                    if u.col == 2:
                        u.stats["shield"] *= 1.10
            elif t == "Sea Crest" and not bs_only:
                for u in units:
                    if u.col == 2:
                        u.stats["heal"] *= 1.10
            elif t == "Warhorn" and not bs_only:
                for u in units:
                    u.stats["damage"] += 10
            elif t == "Tempo Charm" and units:
                self.rng.choice(units).speed += 4
            elif t == "Metal Bat" and units and not bs_only:
                max(units, key=lambda u: u.cooldown).stats["damage"] *= 1.5
            elif t == "Ancient Plume" and len(units) == 1 and not bs_only:
                units[0].multicast += 1
            elif t == "Alpha Crown" and not bs_only:
                top = self.teams[side][1]
                if top:
                    for u in units:
                        u.stats["damage"] = top.stats["damage"]
            elif t == "Candy Jar" and not bs_only:
                for u in units:
                    if u.rarity in ("Common", "Uncommon") and u.level < 3:
                        self._level_up(u, 3)

    def shiny_battle_start(self, u: Unit) -> None:
        """Shiny copies of monsters without a passive gain one (seen 2026-11-12 on the cards):
        Magmite "On Battle Start: +10 Shield and +1 Burn permanently", Venopuff "+1 Poison" at
        level 1 and "+2 Poison" at level 2 (so the numbers are taken to scale with the level)."""
        lv = max(u.level, 1)
        for stat, per_level in SHINY_BATTLE_START.get(u.base_name, {}).items():
            self._gain_stat(u, stat, per_level * lv)

    def start(self) -> None:
        for side in (0, 1):
            self.trinket_start(side)
        # Celestia first: it disables Ongoing monsters before anything reads them.
        for side in (0, 1):
            for u in self.teams[side]:
                if u and u.is_("Celestia") and u.active_passive():
                    sides = (1 - side,) if u.shiny else (0, 1)
                    for s2 in sides:
                        for v in self.teams[s2]:
                            if v and v.row == u.row and v.ongoing:
                                v.disabled = True
        for side in (0, 1):
            if not self.at_battle_start[side]:
                continue
            for u in self.teams[side]:
                if not u:
                    continue
                u.t0_dmg = self._raw_damage_of(u) - u.stats["damage"]
                u.t0_heal = self._raw_heal_of(u) - u.stats["heal"]
                u.t0_burn = self._raw_burn_of(u) - u.stats["burn"]
                u.t0_shock = self._raw_shock_of(u) - u.stats["shock"]
                u.t0_mc = self._raw_multicast_of(u) - max(u.multicast + u.extra_multicast, 1)
        for side in self.order():
            repeats = 1 + ("Repeater Charm" in self.trainers[side].trinkets) + self.coffee[side]
            for _ in range(repeats):
                for u in self.teams[side]:
                    if u:
                        self.battle_start(u)

    def order(self) -> tuple[int, int]:
        return (0, 1) if SETTINGS["player_first_on_ties"] else (1, 0)

    # ---------- main loop ----------
    def dead(self) -> tuple[bool, bool]:
        return self.trainers[0].hp <= EPS, self.trainers[1].hp <= EPS

    def run(self) -> BattleResult:
        self.start()
        self.speeds_changed()
        limit = SETTINGS["time_limit"] if self.until is None else self.until
        result = None
        if not self.immortal and any(self.dead()):
            result = self._result()
        while result is None:
            # Next event time.
            times = [self.next_poison, self.next_burn, self.next_sd]
            standing = (self.living(0), self.living(1))
            spd = {}
            for side in (0, 1):
                for u in standing[side]:
                    v = spd[id(u)] = self.speed(u)
                    times.append(self.t + max(0.0, u.cooldown - u.progress) / v)
                    if self._has_shelldra and u.is_("Shelldra") and u.active_passive():
                        times.append(u.shelldra_next)
            t_next = min(times)
            if t_next > limit + EPS:
                self.t = limit
                return self._result(force_draw=self.until is None)
            dt = t_next - self.t
            for side in (0, 1):
                for u in standing[side]:
                    u.progress += dt * spd[id(u)]
            self.t = t_next
            # Casts: keep going while someone is ready (charges can make more units ready).
            for _ in range(50):
                ready = False
                for side in self.order():
                    for u in self.teams[side]:
                        if u and u.alive and u.progress >= u.cooldown - 1e-7:
                            u.progress -= u.cooldown
                            if u.progress < 1e-7:
                                u.progress = 0.0
                            u.progress = min(u.progress, u.cooldown)
                            self.cast(u)
                            ready = True
                            if (not self.immortal and not SETTINGS.get("deaths_after_instant", True)
                                    and any(self.dead())):
                                return self._result()
                if not ready:
                    break
            for tr in self.trainers:
                tr.shock += tr.shock_pending
                tr.shock_pending = 0.0
            if not self.immortal and any(self.dead()):
                return self._result()
            if self._has_shelldra:
                for side in (0, 1):
                    for u in self.living(side):
                        if u.is_("Shelldra") and u.active_passive() and u.shelldra_next <= self.t + EPS:
                            u.extra_multicast += int(u.param or 0)
                            u.shelldra_next += 4.0
            if abs(self.t - self.next_burn) < 1e-7:
                for tr in (self.trainers[i] for i in self.order()):
                    if tr.burn > 0:
                        self.status_damage(tr, tr.burn)
                        tr.burn = max(0.0, tr.burn - 1)
                self.next_burn += SETTINGS["burn_interval"]
            if abs(self.t - self.next_poison) < 1e-7:
                for tr in (self.trainers[i] for i in self.order()):
                    self.status_damage(tr, tr.poison)
                self.next_poison += SETTINGS["poison_interval"]
            if abs(self.t - self.next_sd) < 1e-7:
                sd_dmg = SETTINGS["sudden_death_base"] * (
                    2.0 ** ((self.t - SETTINGS["sudden_death_start"]) / 3.0)
                )
                for tr in (self.trainers[i] for i in self.order()):
                    self._take(tr, sd_dmg)
                self.next_sd += SETTINGS["sudden_death_interval"]
            if not self.immortal and any(self.dead()):
                result = self._result()
        return result

    def _result(self, force_draw: bool = False) -> BattleResult:
        da, db = self.dead()
        if self.immortal or (self.until is not None and not (da or db)):
            winner = "none"
        elif force_draw or (da and db):
            winner = "draw"
        elif db:
            winner = "a"
        else:
            winner = "b"
        a, b = self.trainers
        stat = lambda tr: {k: round(getattr(tr, k)) for k in ("poison", "burn", "shock", "shield")
                           if getattr(tr, k)}
        return BattleResult(
            winner, self.t, (a.hp if self.immortal else max(a.hp, 0.0),
                             b.hp if self.immortal else max(b.hp, 0.0)), (a.max_hp, b.max_hp),
            (stat(a), stat(b)),
            tuple([u.summary() for u in self.teams[s] if u] for s in (0, 1)),
            self.log)


def simulate(team_a: dict, team_b: dict, seed: int = 0, verbose: bool = False,
             until: float | None = None, immortal: bool = False) -> BattleResult:
    """Play one battle. team_a is 'you' (side 0).

    until: stop at this time and report the state (winner "none" if nobody has died).
    immortal: nobody dies (HP can go negative); use with until to compare damage over time.
    """
    return Battle(team_a, team_b, seed=seed, verbose=verbose, until=until, immortal=immortal).run()


def win_rate(team_a: dict, opponents: list[dict], seeds: int = 1) -> float:
    """Share of battles team_a wins against a list of opponent teams (draws count half)."""
    score = 0.0
    n = 0
    for opp in opponents:
        for s in range(seeds):
            r = simulate(team_a, opp, seed=s)
            score += 1.0 if r.winner == "a" else 0.5 if r.winner == "draw" else 0.0
            n += 1
    return score / n if n else 0.0


def team_from_reader(state: dict) -> dict:
    """Turn reader.read_state() output for the shop screen into a simulator team."""
    slots = []
    for s in state.get("team", []):
        if not s or not s.get("name"):
            slots.append(None)
            continue
        stats = {}
        mc = None
        for b in s.get("stats", []):
            if b.get("stat") == "multicast":
                mc = b["value"]
            elif b.get("stat") in data.STATS and b.get("value") is not None:
                stats[b["stat"]] = b["value"]
        slot = {"name": s["name"], "level": s.get("level") or 1, "stats": stats}
        if mc:
            slot["multicast"] = mc
        slots.append(slot)
    return {"trainer": state.get("trainer"), "hp": state.get("trainer_hp"), "slots": slots}
