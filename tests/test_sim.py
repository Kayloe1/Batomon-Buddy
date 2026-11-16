"""Tests for the battle simulator (sim/). No game, captures or extra packages needed."""
import random
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sim import data  # noqa: E402
from sim.battle import Battle, simulate, team_from_reader, win_rate  # noqa: E402
from sim.check import check_case, load_cases  # noqa: E402
from sim.rules import SETTINGS  # noqa: E402
from sim.__main__ import main  # noqa: E402


def team(slots, hp=1000, **kw):
    slots = list(slots) + [None] * (6 - len(slots))
    return {"hp": hp, "slots": slots, **kw}


def mon(name, level=1, **stats):
    out = {"name": name, "level": level}
    if stats:
        out["stats"] = stats
    return out


EMPTY = team([])


class Data(unittest.TestCase):
    def test_all_pantra_species_load(self):
        sp = data.load_species()
        import csv
        foreign = set()                       # monsters of other regions (learned in play) and the stand-in
        for f in ("foreign_monsters.csv", "stand_in_monsters.csv", "jinto_monsters.csv"):
            with open(data.DATA / f, newline="", encoding="utf-8") as fh:
                foreign |= {r["name"] for r in csv.DictReader(fh)}
        self.assertEqual(len([n for n in sp if n not in data.EXTRA_UNITS and n not in foreign]), 64)
        for s in sp.values():
            for lv in (1, 2, 3, 4):
                self.assertGreater(s.levels[lv].cooldown, 0, s.name)

    def test_level_numbers(self):
        self.assertEqual(data.species("Brawlmantis").levels[2].stats["damage"], 100)
        self.assertEqual(data.species("Velocect").levels[1].multicast, 2)
        self.assertEqual(data.species("Velocect").levels[4].multicast, 8)
        self.assertEqual(data.species("Cherubble").levels[2].cooldown, 2.0)
        self.assertEqual([data.species("Dracana").levels[n].param for n in (1, 2, 3, 4)], [1, 2, 3, 18])
        self.assertEqual([data.species("Lignite").levels[n].param for n in (1, 2)], [24, 48])
        self.assertEqual(data.species("Draconarch").levels[1].param, 6)
        self.assertEqual(data.species("Draconarch").levels[2].param, 3)
        self.assertEqual(data.species("Shelldra").levels[3].param, 3)
        self.assertEqual(data.species("Shelldra").levels[3].stats["damage"], 15)

    def test_evolved_forms(self):
        bambudo = data.species("Bambudo")
        self.assertEqual(bambudo.levels[3].stats["damage"], 75)
        self.assertEqual(bambudo.levels[3].param, 35)
        self.assertEqual(data.species("Dribblet").levels[3].param, 60)
        self.assertEqual(data.species("Scorchimp").levels[4].param, 20)


class Positions(unittest.TestCase):
    def setUp(self):
        names = ["Panbud", "Spinarai", "Velocect", "Mosslug", "Dribblet", "Pebbler"]
        self.b = Battle(team([mon(n) for n in names]), EMPTY)
        self.t = self.b.teams[0]

    def test_behind_front_above(self):
        self.assertIs(self.b.behind(self.t[1]), self.t[0])
        self.assertIs(self.b.in_front(self.t[1]), self.t[2])
        self.assertIsNone(self.b.behind(self.t[3]))
        self.assertIs(self.b.above(self.t[4]), self.t[1])
        self.assertIsNone(self.b.above(self.t[1]))

    def test_adjacent_is_orthogonal(self):
        self.assertEqual({u.slot for u in self.b.adjacent(self.t[0])}, {1, 3})
        self.assertEqual({u.slot for u in self.b.adjacent(self.t[4])}, {1, 3, 5})


class Mechanics(unittest.TestCase):
    def test_first_cast_after_full_cooldown(self):
        r = simulate(team([mon("Panbud")]), EMPTY, until=4.99)
        self.assertEqual(r.units[0][0]["triggered"], 0)
        r = simulate(team([mon("Panbud")]), EMPTY, until=5.0)
        self.assertEqual(r.units[0][0]["triggered"], 1)
        self.assertEqual(r.hp[1], 1000 - 25)

    def test_poison_ticks_every_second_and_stays(self):
        r = simulate(team([mon("Venopuff")]), EMPTY, until=6.0)
        # Poison 4 at 3.5 s; ticks at 4, 5, 6.
        self.assertEqual(r.statuses[1]["poison"], 4)
        self.assertEqual(r.hp[1], 1000 - 12)

    def test_burn_ticks_twice_a_second_and_decays(self):
        # Checks the code against an UNVERIFIED rule from the September notes (see sim/rules.py).
        r = simulate(team([mon("Pyronade")]), EMPTY, until=6.0)
        # Burn 20 at 4.5 s; ticks at 4.5 (right after the cast), 5.0, 5.5, 6.0 deal 20, 19, 18, 17.
        self.assertEqual(r.hp[1], 1000 - 74)
        self.assertEqual(r.statuses[1]["burn"], 16)

    def test_shock_adds_flat_damage_to_hits(self):
        r = simulate(team([mon("Electranade"), mon("Panbud")]), EMPTY, until=5.0)
        self.assertEqual(r.hp[1], 1000 - (25 + 25))
        self.assertEqual(r.statuses[1]["shock"], 25)

    def test_shield_blocks_damage_and_status_is_reduced(self):
        # The 15% status reduction is an UNVERIFIED number from the September notes.
        # Both cast at 5 s, player first: Shield 20, then the 25 hit takes it and 5 HP.
        r = simulate(team([mon("Pebbler")]), team([mon("Panbud")]), until=5.0)
        self.assertEqual(r.hp[0], 995)
        # Poison 4 from 3.5 s. Tick at 4 s hits HP (4); Shield 20 at 5 s; ticks at 5 and 6 s
        # do 4 x 0.85 = 3.4 each into the shield.
        r = simulate(team([mon("Pebbler")]), team([mon("Venopuff")]), until=6.0)
        self.assertEqual(r.hp[0], 996)
        self.assertEqual(r.statuses[0]["shield"], round(20 - 6.8))

    def test_heal_is_capped(self):
        r = simulate(team([mon("Dribblet")]), EMPTY, until=10)
        self.assertEqual(r.hp[0], 1000)

    def test_self_knockout(self):
        r = simulate(team([mon("Stingarde")]), EMPTY, until=20)
        self.assertEqual(r.units[0][0]["triggered"], 1)
        self.assertTrue(r.units[0][0]["knocked_out"])

    def test_cherubble_protect_blocks_self_knockout(self):
        # Cherubble (4 s) protects its neighbour before Stingarde (6 s) casts.
        r = simulate(team([mon("Cherubble"), mon("Stingarde")]), EMPTY, until=13)
        self.assertEqual(r.units[0][1]["triggered"], 2)

    def test_cicadence_triggers_bug_above(self):
        r = simulate(team([mon("Panbud"), mon("Velocect"), None, None, mon("Cicadence")]), EMPTY,
                     until=6.0)
        velocect = r.units[0][1]
        # Velocect casts at 3.5 (x2) and again when Cicadence casts at 6.0 (x2): two casts, four hits.
        # "triggered" counts casts, as the game's Stats screen does (seen 2026-11-12).
        self.assertEqual(velocect["triggered"], 2)
        self.assertEqual(velocect["dealt"], 60)

    def test_dracana_charges_ally_behind(self):
        r = simulate(team([mon("Stingarde"), mon("Dracana")]), EMPTY, until=5.0)
        # Dracana casts at 3 s and charges Stingarde 1 s: Stingarde casts at 5 s, not 6 s.
        self.assertEqual(r.units[0][0]["triggered"], 1)

    def test_formiqueen_speeds_adjacent_common(self):
        r = simulate(team([mon("Panbud"), mon("Formiqueen")]), EMPTY, until=4.0)
        self.assertEqual(r.units[0][0]["triggered"], 1)    # 5 s / 1.25 = 4 s

    def test_multicast_hits_twice_but_triggers_once(self):
        # Stats screen 2026-11-12: Velocect (15 x2) "Triggered 4 time(s)", "Dealt 144" with Shock on us.
        r = simulate(team([mon("Velocect")]), EMPTY, until=3.5)
        self.assertEqual(r.units[0][0]["triggered"], 1)
        self.assertEqual(r.hp[1], 1000 - 30)

    def test_badges_replace_base_numbers(self):
        r = simulate(team([mon("Brawlmantis", damage=60)]), EMPTY, until=5.5)
        self.assertEqual(r.hp[1], 1000 - 60)

    def test_winner_and_draw(self):
        r = simulate(team([mon("Stingarde")], hp=100), team([], hp=100))
        self.assertEqual(r.winner, "a")
        # Same instant: every cast of both sides resolves before deaths are checked (the opponent's
        # Craghorn still cast at the 18 s instant our casts won at, 2026-11-12), so both die: draw.
        r = simulate(team([mon("Stingarde")], hp=100), team([mon("Stingarde")], hp=100))
        self.assertEqual(r.winner, "draw")
        # Both die to the same Poison tick: draw.
        r = simulate(team([mon("Venopuff")], hp=4), team([mon("Venopuff")], hp=4))
        self.assertEqual(r.winner, "draw")
        r = simulate(team([mon("Dribblet")]), team([mon("Dribblet")]))
        self.assertEqual(r.winner, "draw")    # nobody can win: time limit

    def test_day_hp_table(self):
        r = simulate({"day": 3, "slots": []}, EMPTY, until=1)
        self.assertEqual(r.max_hp[0], 800)


class RecordedBattles(unittest.TestCase):
    def setUp(self):
        self.cases = dict(load_cases())

    def _rows(self, name):
        case = self.cases[name]
        known = case["expect"].get("known_gaps", {})
        rows = check_case(case)
        verified = [r for r in rows if not any(r[0] == k or r[0].endswith(k) for k in known)]
        return rows, verified

    def test_day2_reproduced_exactly(self):
        rows, _ = self._rows("day2_rich_lady_win")
        bad = [r for r in rows if not r[3]]
        self.assertEqual(bad, [])

    def test_day3_verified_parts(self):
        _, verified = self._rows("day3_rich_lady_loss")
        bad = [r for r in verified if not r[3]]
        self.assertEqual(bad, [])
        self.assertGreaterEqual(len(verified), 10)

    def test_day3_outcome(self):
        """Ed loses Day 3 once heals at 60+ Poison do nothing (a guess, sim/rules.py); without it, a draw."""
        case = self.cases["day3_rich_lady_loss"]
        self.assertEqual(simulate(case["a"], case["b"]).winner, "b")
        with mock.patch.dict(SETTINGS, {"heal_blocked_at_poison": None}):
            self.assertEqual(simulate(case["a"], case["b"]).winner, "draw")

    def test_heals_at_low_poison_still_count(self):
        case = self.cases["day3_rich_lady_loss"]
        with mock.patch.dict(SETTINGS, {"heal_blocked_at_poison": 1}):     # every heal while poisoned blocked
            all_blocked = simulate(case["a"], case["b"], until=14.0)
        r = simulate(case["a"], case["b"], until=14.0)
        self.assertGreater(r.hp[0], all_blocked.hp[0])     # Ed's heals at 5-50 Poison counted

    def test_check_command_passes(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--check"]), 0)


class Robustness(unittest.TestCase):
    def test_every_species_and_level_runs(self):
        names = [n for n in data.load_species()]
        rng = random.Random(1)
        for i in range(150):
            def rand_team():
                return team([mon(rng.choice(names), rng.randint(1, 4)) if rng.random() < 0.85 else None
                             for _ in range(6)], hp=rng.choice([300, 2400, 47300]),
                            trinkets=rng.sample(["Rally Flag", "Warhorn", "Link Cable", "Metronome",
                                                 "Repeater Charm", "Topaz Amulet", "Momentum Flag",
                                                 "Tempo Charm", "Haste Orb", "Razor Beak"], 3))
            r = simulate(rand_team(), rand_team(), seed=i)
            self.assertIn(r.winner, ("a", "b", "draw"))

    def test_self_accelerating_team_finishes(self):
        # Galvanine Lv4 gains +50% Cooldown Speed per cast; without the min_cooldown floor this
        # battle never ended.
        a = team([None, mon("Pebbler", 4), mon("Guardiant"), mon("Geminiss", 4), mon("Aegistruct"),
                  mon("Craghorn", 2)], hp=800, trinkets=["Razor Beak", "Warhorn", "Repeater Charm"])
        b = team([mon("Noxalith", 2), mon("Galvanine", 4), mon("Noxnimbus", 3), mon("Dirgefin", 4),
                  None, mon("Dryadell", 4)], hp=8300, trinkets=["Razor Beak", "Metronome", "Repeater Charm"])
        r = simulate(a, b)
        self.assertIn(r.winner, ("a", "b", "draw"))

    def test_speed_cache_follows_buffs(self):
        # Boomagon speeds up the ally behind it on each cast; the cached speed must follow.
        r = simulate(team([mon("Panbud"), mon("Boomagon", 4)]), EMPTY, until=60)
        r0 = simulate(team([mon("Panbud")]), EMPTY, until=60)
        self.assertGreater(r.units[0][0]["triggered"], r0.units[0][0]["triggered"])

    def test_same_seed_same_result(self):
        a = team([mon("Reapra", 4), mon("Panbud")], trinkets=["Tempo Charm"])
        b = team([mon("Velocect"), mon("Panbud"), mon("Spinarai")], trinkets=["Tempo Charm"])
        self.assertEqual(simulate(a, b, seed=3).as_dict(), simulate(a, b, seed=3).as_dict())

    def test_win_rate(self):
        strong = team([mon("Stingarde", 2)], hp=300)
        weak = team([mon("Dribblet")], hp=300)
        self.assertEqual(win_rate(strong, [weak, weak]), 1.0)

    def test_team_from_reader(self):
        state = {"trainer": "Rich Lady", "trainer_hp": 500, "team": [
            {"name": "Brawlmantis", "level": 1, "stats": [{"value": 60, "stat": "damage"}]},
            None,
            {"name": "Velocect", "level": 2, "stats": [{"value": 20, "stat": "damage"},
                                                      {"value": 3, "stat": "multicast"}]},
            {"name": None, "level": 1, "stats": []}, None, None]}
        t = team_from_reader(state)
        self.assertEqual(t["hp"], 500)
        self.assertEqual(t["slots"][0]["stats"], {"damage": 60})
        self.assertEqual(t["slots"][2]["multicast"], 3)
        self.assertIsNone(t["slots"][3])
        simulate(t, EMPTY, until=5)


if __name__ == "__main__":
    unittest.main()


class FromLogs(unittest.TestCase):
    """A logged battle (as play.py writes it) becomes a simulator case."""

    def _log(self, tmp, records):
        import json
        p = Path(tmp) / "run_test.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        return p

    def test_day2_read_from_the_battle_screen_matches_the_hand_made_case(self):
        import json
        import tempfile
        from sim import from_logs
        seen = json.loads((Path(__file__).parent / "fixtures" / "battle_day2_seen.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = self._log(tmp, [
                {"kind": "trainer", "ok": True},
                {"kind": "trinket", "ok": True, "offered": ["Rally Flag", "Wood Sword"], "picked": 1},
                {"kind": "battle", "time": "t", "day": 2, "trainer": "Rich Lady", "trainer_hp": 500, "seen": seen},
                {"kind": "day_result", "day": 2, "badges": 1, "lives": 0},
            ])
            cases = from_logs.battles_from_log(path)
        self.assertEqual(len(cases), 1)
        c = cases[0]
        self.assertEqual((c["result"], c["problems"], c["a"]["trinkets"]), ("win", [], ["Wood Sword"]))
        hand = json.loads((ROOT / "sim" / "cases" / "day2_rich_lady_win.json").read_text())
        self.assertEqual(c["a"]["slots"], hand["a"]["slots"])
        self.assertEqual(c["b"]["slots"], hand["b"]["slots"])
        c["a"]["trinkets"] = []
        res = from_logs.check([c], seeds=1)
        self.assertEqual((res["usable"], res["agree"]), (1, 1))

    def test_unnamed_opponent_is_skipped_not_guessed(self):
        import tempfile
        from sim import from_logs
        seen = {"my_team": [{"name": "Mosslug", "level": 1, "stats": []}] + [None] * 5,
                "opponent_team": [{"name": None, "level": 2, "stats": [{"stat": "damage", "value": 40}]}] + [None] * 5,
                "problems": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = self._log(tmp, [{"kind": "battle", "day": 1, "seen": seen},
                                   {"kind": "day_result", "badges": 0, "lives": -1}])
            cases = from_logs.battles_from_log(path)
        self.assertEqual(cases[0]["result"], "loss")
        self.assertIn("opponent slot 1 not named", cases[0]["problems"])
        self.assertEqual(from_logs.check(cases)["skipped"], 1)


class ShinyEffects(unittest.TestCase):
    """What the Shiny cards said in the game (2026-11-12)."""

    def test_shiny_stingarde_keeps_casting(self):
        r = simulate(team([dict(mon("Stingarde"), shiny=True)]), EMPTY, until=18)
        self.assertEqual(r.units[0][0]["triggered"], 3)
        self.assertFalse(r.units[0][0].get("knocked_out"))

    def test_shiny_battle_start_growth_scales_with_level(self):
        a = team([dict(mon("Magmite"), shiny=True), dict(mon("Venopuff", 2), shiny=True)])
        r = simulate(a, EMPTY, until=0.1)
        self.assertEqual(r.units[0][0]["stats"]["shield"], 50)    # 40 + 10
        self.assertEqual(r.units[0][1]["stats"]["poison"], 10)    # Lv2 8 + 2


class GaiadrasilCountsOngoingDamage(unittest.TestCase):
    def test_fumungus_poison_damage_is_counted(self):
        # Fumungus (Ongoing: +200% of enemy Poison as Damage) next to Gaiadrasil: once the enemy
        # is poisoned, Gaiadrasil's hit includes Fumungus's extra Damage (game, 2026-11-12).
        a = team([mon("Fumungus", 2, poison=50), mon("Gaiadrasil")])
        r = simulate(a, EMPTY, until=9.0)
        fum, gaia = r.units[0]
        self.assertGreater(gaia["dealt"], 50 * 2)        # more than Fumungus's own base Damage


class JintoData(unittest.TestCase):
    def test_jinto_monsters_load_and_stay_out_of_the_pantra_shop(self):
        from engine import data as edata
        sp = data.load_species()
        for n in ("Cinnabark", "Drumire", "Miasmaw", "Quillustrous",
                  "Cordycant", "Kickrane", "Dollhime", "Shikitsune", "Sproach", "Steamscuttle"):
            self.assertIn(n, sp)
        self.assertEqual(sp["Cinnabark"].levels[3].stats["poison"], 15)
        self.assertEqual(sp["Drumire"].cost, 45)
        self.assertEqual(sp["Kickrane"].levels[1].multicast, 2)
        self.assertEqual(sp["Cordycant"].types, ["Toxic", "Bug"])
        shop = {s.name for r in edata.RARITIES for s in edata.by_rarity(r)}
        self.assertNotIn("Cinnabark", shop)
        self.assertEqual(len(shop), 64)


class JintoPassives(unittest.TestCase):
    def test_miasmaw_and_drumire(self):
        # Miasmaw copies non-Miasmaw allies' Poison (2x at Lv2); two Miasmaw do not copy each other.
        r = simulate(team([mon("Miasmaw", 2), mon("Miasmaw", 2), mon("Venopuff")]), EMPTY, until=3.0)
        self.assertEqual(r.statuses[1]["poison"], 36)  # 2 * (10 + 2 * 4)
        # Drumire speeds up Toxic allies each time they cast.
        fast = simulate(team([mon("Venopuff"), mon("Drumire", 3)]), EMPTY, until=20.0)
        base = simulate(team([mon("Venopuff")]), EMPTY, until=20.0)
        self.assertGreater(fast.units[0][0]["triggered"], base.units[0][0]["triggered"])

    def test_cinnabark_ouroblaze_and_quillustrous_ongoing(self):
        # Cinnabark gains extra Shield equal to 100% of enemy Poison on its 2nd cast (8 s).
        r = simulate(team([mon("Cinnabark")]), EMPTY, until=8.0)
        self.assertEqual(r.units[0][0]["shielded"], 5)
        # Ouroblaze gains extra Burn equal to 50% of enemy Burn.
        r = simulate(team([mon("Pyronade"), mon("Ouroblaze")]), EMPTY, until=8.0)
        self.assertGreater(r.units[0][1]["inflicted_burn"], 0)
        # Quillustrous adds 50% of allies' Damage; safe when paired with Gaiadrasil.
        r = simulate(team([mon("Panbud", 1, damage=100), mon("Quillustrous", 1, damage=100)]), EMPTY, until=7.0)
        self.assertEqual(r.units[0][1]["dealt"], 300)
        r2 = simulate(team([mon("Gaiadrasil"), mon("Quillustrous")]), EMPTY, until=10.0)
        self.assertGreater(r2.units[0][1]["dealt"], 0)

    def test_bonshell_cairnage_opalion_and_snapscald(self):
        r = simulate(team([mon("Bonshell")]), EMPTY, until=14.0)
        self.assertEqual((r.units[0][0]["shielded"], r.units[0][0]["dealt"]), (270, 70))
        r = simulate(team([mon("Pebbler", 1, shield=100), mon("Cairnage")]), EMPTY, until=6.0)
        self.assertEqual(r.units[0][0]["stats"]["damage"], 92)  # 0.8 * (100 + 15)
        r = simulate(team([mon("Opalion"), mon("Pebbler")]), EMPTY, until=6.5)
        self.assertEqual(r.units[0][1]["triggered"], 2)  # own 5s cast + triggered by Opalion at 6.5s
        r = simulate(team([mon("Panbud", 3), mon("Snapscald")]), EMPTY, until=5.0)
        self.assertEqual(r.units[0][1]["triggered"], 1)  # triggered by Lv3 ally cast

    def test_steamscuttle_and_charge_immunity(self):
        # Adjacent Fire ally is charged by 1 s; another Steamscuttle and Dracana are immune to Charge.
        r = simulate(team([mon("Magmite"), mon("Steamscuttle"), mon("Steamscuttle")]), EMPTY, until=4.5)
        self.assertEqual(r.units[0][0]["triggered"], 1)
        r2 = simulate(team([mon("Dracana"), mon("Steamscuttle"), mon("Steamscuttle")]), EMPTY, until=6.8)
        self.assertEqual(r2.units[0][2]["triggered"], 1)
        r3 = simulate(team([mon("Dracana"), mon("Steamscuttle")]), EMPTY, until=5.5)
        self.assertEqual(r3.units[0][0]["triggered"], 1)

    def test_shikitsune_vengrieve_danuki_and_oniclaw(self):
        # Shikitsune revives Stingarde after its 6s self-KO and accelerates it (+15%).
        r6 = simulate(team([mon("Stingarde"), mon("Shikitsune")]), EMPTY, until=6.0)
        self.assertFalse(r6.units[0][0].get("knocked_out"))
        r = simulate(team([mon("Stingarde"), mon("Shikitsune")]), EMPTY, until=11.5)
        self.assertEqual(r.units[0][0]["triggered"], 2)
        # Vengrieve knocks out the opposite enemy and gains its stats.
        r = simulate(team([mon("Vengrieve")]), team([mon("Panbud", 1, damage=123)]), until=7.5)
        self.assertTrue(r.units[1][0].get("knocked_out"))
        self.assertEqual(r.units[0][0]["stats"]["damage"], 123)
        # Danuki gains 85% of knocked-out ally's Damage.
        r = simulate(team([mon("Stingarde"), mon("Danuki")]), EMPTY, until=6.1)
        self.assertEqual(r.units[0][1]["stats"]["damage"], round(40 + 0.85 * 150))
        # Oniclaw gains +50% Damage when team inflicts Burn.
        r = simulate(team([mon("Oniclaw")]), EMPTY, until=6.0)
        self.assertEqual(r.units[0][0]["stats"]["damage"], 150)


class MechanicsAndMythicals(unittest.TestCase):
    def test_sirenade_rounds_debuff_removal_up(self):
        # 20% of 4 Poison = 0.8 -> ceil rounds up to 1 removed (4 -> 3).
        r = simulate(team([mon("Sirenade")], hp=5000), team([mon("Venopuff", 1, poison=4)]), until=6.0)
        self.assertEqual(r.statuses[0]["poison"], 3)

    def test_shiny_celestia_disables_only_enemies(self):
        r = simulate(team([dict(mon("Celestia"), shiny=True), mon("Lignite")]),
                     team([mon("Lignite")]), until=5.0)
        self.assertEqual(r.units[0][1]["dealt"], 72)   # ally Lignite stays enabled (24 * 3 Burn)
        self.assertEqual(r.units[1][0]["dealt"], 0)    # enemy Lignite disabled

    def test_excalibur_and_master_crown(self):
        r = simulate(team([mon("Panbud")], trinkets=["Excalibur"]), EMPTY, until=0.1)
        self.assertEqual(r.units[0][0]["stats"]["damage"], 375)
        r = simulate(team([mon("Panbud"), mon("Panbud")], trinkets=["Master Crown"]), EMPTY, until=0.1)
        self.assertEqual(r.units[0][0]["stats"]["damage"], 25)
        self.assertEqual(r.units[0][1]["stats"]["damage"], 45)

    def test_sudden_death_starts_at_30s(self):
        # At 30.5 s, both sides have taken 8 + 8 * 2^(0.5/3) Sudden Death damage.
        r = simulate(team([], hp=1000), team([], hp=1000), until=30.5)
        expected = 1000 - (8.0 + 8.0 * (2.0 ** (0.5 / 3.0)))
        self.assertAlmostEqual(r.hp[0], expected, places=5)
        self.assertAlmostEqual(r.hp[1], expected, places=5)
        # Mirror stall finishes via Sudden Death well before the 300 s fallback.
        r = simulate(team([mon("Dribblet")]), team([mon("Dribblet")]))
        self.assertEqual(r.winner, "draw")
        self.assertLess(r.time, 60.0)

    def test_charge_discards_excess_and_casts_at_once(self):
        # Ironcore L2 (2.0 s cd) charges adjacent Bumblebolt (2.5 s cd) by 2.0 s at 2.0 s:
        # Bumblebolt's 2.0 + 2.0 = 4.0 s casts once at 2.0 s and discards the 1.5 s excess,
        # so by 4.0 s (before the 2nd Ironcore cast? no, at 3.9 s) Bumblebolt has only 1 cast.
        r = simulate(team([mon("Bumblebolt"), mon("Ironcore", 2)]), EMPTY, until=3.9)
        self.assertEqual(r.units[0][0]["triggered"], 1)
        # Clawnetic (3.0 s cd) charged 1.0 s per Shock hit: a 3-hit Bumblebolt cast at 2.5 s
        # fills Clawnetic on hit 1 (2.5 + 1.0 -> casts at once, resets to 0), then hits 2 & 3
        # charge it to 2.0 s, so by 3.6 s Clawnetic has 2 casts (2.5 s and 3.5 s).
        r = simulate(team([mon("Clawnetic"), {**mon("Bumblebolt"), "multicast": 3}]), EMPTY, until=3.6)
        self.assertEqual(r.units[0][0]["triggered"], 2)

    def test_heal_at_60_poison_counts_but_70_is_blocked(self):
        # 15 x Venopuff (4 Poison) inflicts 60 Poison at 3.5 s; Dribblet heals 20 at 6.0 s.
        # At 60 Poison the heal still counts; at 72 Poison (18 x Venopuff) it is blocked.
        r60 = simulate(team([mon("Dribblet")], hp=1000),
                       team([mon("Venopuff", 1, poison=60)]), until=6.1)
        r72 = simulate(team([mon("Dribblet")], hp=1000),
                       team([mon("Venopuff", 1, poison=72)]), until=6.1)
        # Between 3.5 s and 6.0 s Poison ticks at 4, 5, 6 s (3 ticks).
        self.assertAlmostEqual(r60.hp[0], 1000 - 3 * 60 + 20)
        self.assertAlmostEqual(r72.hp[0], 1000 - 3 * 72)

    def test_at_battle_start_avoids_double_counting_badges(self):
        # Battle-screen capture already includes Rally Flag (+12), Aegistruct (+140),
        # Stellagon (+2 Multicast), and Lignite (24 * 3 = +72).
        t = team([
            mon("Runerock", 1, shield=140),
            mon("Aegistruct", 1, shield=290),
            mon("Lignite", 1, damage=72, burn=3),
            mon("Stellagon", 1, damage=100),
            {**mon("Panbud", 1, damage=25), "multicast": 3},
        ], trinkets=["Rally Flag"])
        t["at_battle_start"] = True
        r = simulate(t, EMPTY, until=6.1)
        self.assertEqual(r.units[0][1]["shielded"], 290)
        self.assertEqual(r.units[0][2]["dealt"], 72)
        self.assertEqual(r.units[0][4]["dealt"], 75)   # 3 hits x 25, not 5 hits x 25
