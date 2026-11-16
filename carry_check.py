"""Carry vs spread: on real Gold boards (days 6-9), is it better to spend two cards merging a fielded
Lv1 into a Lv2, or to replace the two weakest fielded monsters with two new cards from the shop?

Each board is taken from the Gold logs (our team as the shop screen read it, with base stats so the
options are compared on equal terms). Every option is fought against two opponent sets:
  - the Gold practice teams for that day (what the bot really meets), and
  - the ghost pool the planner plans against (what the planner believes).
Read-only: nothing is sent to the game.

    .\\.venv\\Scripts\\python.exe carry_check.py
"""
from __future__ import annotations

import glob
import json
import os
import random
import statistics

from engine import data, model, opponents, practice
from engine.arrange import power
from engine.evaluate import Evaluator

LOGS = "runs/2026-11-15-ranked"
SINCE = "20261115_020000"
DAYS = (6, 7, 8, 9)
SHOP_SAMPLES = 12


def boards():
    for p in sorted(glob.glob(os.path.join(LOGS, "run_*.jsonl"))):
        if os.path.basename(p)[4:19] < SINCE:
            continue
        trainer, trinkets = None, []
        for line in open(p, encoding="utf-8"):
            d = json.loads(line)
            if d["kind"] == "trainer" and d.get("picked") is not None:
                pk = d["picked"]
                trainer = d["offered"][pk] if isinstance(pk, int) else pk
            elif d["kind"] == "trinket" and d.get("picked") is not None:
                pk = d["picked"]
                trinkets.append(d["offered"][pk] if isinstance(pk, int) else pk)
            elif d["kind"] == "battle" and d.get("day") in DAYS:
                team = []
                for u in d.get("team") or []:
                    if u and u.get("name") and data.species(u["name"]) and (u.get("level") or 0) in (1, 2, 3):
                        sp = data.species(u["name"])
                        team.append(model.new_unit(sp.name, sp.cost, u["level"]))
                    else:
                        team.append(None)
                if sum(1 for u in team if u) == 6:
                    yield os.path.basename(p), d["day"], trainer, list(trinkets), team


def main() -> int:
    gold = Evaluator(practice.gold_pools())
    ghost = Evaluator(opponents.pools(n=24, seed=1, recorded=False))
    rng = random.Random(7)
    rows = []
    for f, day, trainer, trinkets, team in boards():
        hp = data.hp_for_day(day)

        def score(t):
            return (gold.score(t, day, 99, hp=hp, trainer=trainer, trinkets=trinkets).win,
                    ghost.score(t, day, 16, hp=hp, trainer=trainer, trinkets=trinkets).win)

        base = score(team)
        order = sorted(range(6), key=lambda i: power(team[i]))
        # merge: two more copies of a fielded Lv1 make it Lv2 (best such choice and the average one)
        merges = []
        for i, u in enumerate(team):
            if u.level != 1 or data.species(u.name).foreign:
                continue
            t = list(team)
            t[i] = model.new_unit(u.name, u.species.cost, 2)
            merges.append((score(t), 2 * u.species.cost, u.name))
        # replace: a 4-card shop at today's odds, the two strongest cards replace the two weakest monsters
        reps = []
        odds = data.rank_odds(day)
        for _ in range(SHOP_SAMPLES):
            shop = []
            for _ in range(data.SHOP_MONSTERS):
                r = rng.choices(data.RARITIES, weights=odds)[0]
                sp = rng.choice([x for x in data.by_rarity(r) if not x.foreign])
                shop.append(model.new_unit(sp.name, sp.cost, 1))
            buy = sorted(shop, key=power, reverse=True)[:2]
            t = list(team)
            for slot, u in zip(order[:2], buy):
                t[slot] = u
            reps.append((score(t), sum(u.species.cost for u in buy)))
        if not merges:
            continue
        best_m = max(merges, key=lambda m: m[0][0])
        rows.append({
            "board": f"{f} d{day}", "base": base,
            "merge_best": best_m[0], "merge_avg": tuple(statistics.mean(m[0][k] for m in merges) for k in (0, 1)),
            "merge_cost": statistics.mean(m[1] for m in merges),
            "replace_avg": tuple(statistics.mean(r[0][k] for r in reps) for k in (0, 1)),
            "replace_cost": statistics.mean(r[1] for r in reps), "merge_name": best_m[2],
            "replace_best": max((r[0] for r in reps), key=lambda x: x[0]),
        })
        x = rows[-1]
        print(f"{x['board']:32} base {base[0]:.2f}/{base[1]:.2f}  merge(best {best_m[2]}) "
              f"{best_m[0][0]:.2f}/{best_m[0][1]:.2f} avg {x['merge_avg'][0]:.2f}/{x['merge_avg'][1]:.2f} "
              f"${x['merge_cost']:.0f}   replace avg {x['replace_avg'][0]:.2f}/{x['replace_avg'][1]:.2f} "
              f"${x['replace_cost']:.0f}   (gold field / planner's ghosts)")
    n = len(rows)
    m = lambda k, j: statistics.mean(r[k][j] for r in rows)
    print(f"\n{n} boards. Win chance vs Gold field / vs planner's ghost pool:")
    print(f"  as it is          {m('base', 0):.3f} / {m('base', 1):.3f}")
    print(f"  merge, best pick  {m('merge_best', 0):.3f} / {m('merge_best', 1):.3f}")
    print(f"  merge, average    {m('merge_avg', 0):.3f} / {m('merge_avg', 1):.3f}   (cost ${statistics.mean(r['merge_cost'] for r in rows):.0f})")
    print(f"  replace, average  {m('replace_avg', 0):.3f} / {m('replace_avg', 1):.3f}   (cost ${statistics.mean(r['replace_cost'] for r in rows):.0f})")
    print(f"  replace, best of {SHOP_SAMPLES} shops {m('replace_best', 0):.3f} / {m('replace_best', 1):.3f}")
    # does the planner's ghost pool rank the options the same way the Gold field does?
    for k in ("merge_best", "replace_avg", "replace_best"):
        up_g = sum(r[k][0] > r["base"][0] + 1e-9 for r in rows)
        up_h = sum(r[k][1] > r["base"][1] + 1e-9 for r in rows)
        print(f"  {k:13} beats the board as it is: Gold field {up_g} of {n}, ghost pool {up_h} of {n}")
    agree = sum((r["merge_best"][0] >= r["replace_best"][0]) == (r["merge_best"][1] >= r["replace_best"][1]) for r in rows)
    print(f"  boards where both pools agree on merge vs best replace: {agree} of {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
