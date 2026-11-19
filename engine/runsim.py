"""Play whole runs offline to measure a policy's win rate.

    python -m engine.runsim --policy engine --runs 20
    python -m engine.runsim --policy simple --runs 200

A run follows the real rules the engine knows (engine/data.py): income, shop rank
odds, merges and gifts, lives lost per day, one rescue at 0 lives (+1 life and 25%
more HP), 10 badges to win. Each battle is against one opponent drawn from a
SEPARATE pool (different seed) from the one the engine plans with, so the engine
is not graded on the opponents it practised against.

Trainers and gift trinkets are picked as in live play (engine/choices.py: three trainers offered
on day 1, three trinkets per gift, the trinket's badge bonuses applied to the monsters owned);
`--random-choices` restores the old behaviour (no trainer, a random trinket, no badge bonuses).
Not modelled: trainers' shop abilities, events, and the trinket effects the simulator does not know. Both policies get the same shops (same seed per run), so
the comparison is fair even though the absolute numbers are only as good as the
simulator and the opponent pool.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict

from sim.battle import simulate

from . import data, growth, model, opponents
from .arrange import power
from .evaluate import Evaluator, team_spec
from .planner import Params, Planner
from .model import RunState

MAX_DAYS = 20


def default_workers() -> int:
    """All cores but two (the PC stays usable); at least one."""
    return max(1, (os.cpu_count() or 4) - 2)


DEFAULT_WORKERS = default_workers()


def simple_policy_turn(s: RunState, rng: random.Random) -> RunState:
    """play.py's old rules: buy the dearest affordable monster while a team slot is empty."""
    while True:
        if all(u is not None for u in s.team):
            return s
        cards = [(c.price, -i) for i, c in enumerate(s.shop)
                 if c and c.kind == "monster" and c.price <= s.money and model.can_buy(s, i)]
        if not cards:
            return s
        _, ni = max(cards)
        s = model.buy(s, -ni)


def engine_turn(s: RunState, planner: Planner, rng: random.Random, log: list | None = None,
                key: str | None = None) -> RunState:
    """One shop turn. With `key`, every reroll and item draws from its own stream (key, what, count), so
    two policies that differ in one decision still see the same later shops (paired comparisons)."""
    items = 0

    def stream(what: str, n: int) -> random.Random:
        return random.Random(f"{key}-{what}-{n}") if key is not None else rng
    for _ in range(90):
        if key is not None:
            # the planner's own sampling (reroll look-ahead) depends only on the state, not on the history
            import hashlib
            planner.rng.seed(hashlib.md5(f"{key}-{s.rerolls}-{s.money}-{s.team}-{s.bench}-{s.shop}".encode()).hexdigest())
        plan = planner.decide(s)
        a = plan.actions[0]
        if log is not None and a.kind not in ("move", "battle", "lock"):
            log.append(f"d{s.day} ${s.money}: {a.why}")
        if a.kind == "buy":
            s = model.buy(s, a.index)
        elif a.kind == "item":
            s = model.use_item(s, a.index, stream("item", items))
            items += 1
        elif a.kind == "sell":
            s = model.sell(s, *a.src)
        elif a.kind == "reroll":
            s = model.reroll(s, stream("reroll", s.rerolls))
        else:
            team, bench = planner.final_layout(s)
            s.team, s.bench = list(team), list(bench)
            if any(x.kind == "lock" for x in plan.actions):
                s.locked = True
            return s
    team, bench = planner.final_layout(s)
    s.team, s.bench = list(team), list(bench)
    return s


def pick_gift(s: RunState, rarity: str, rng: random.Random) -> str | None:
    opts = [n for n, t in data.trinkets().items() if t["rarity"] == rarity]
    return rng.choice(opts) if opts else None


def trainer_names() -> list[str]:
    import csv
    with open(data.DATA / "trainers.csv", encoding="utf-8") as f:
        return [r["name"] for r in csv.DictReader(f)]


def choose_gift(planner: Planner, s: RunState, rarity: str, rng: random.Random) -> str | None:
    """Three trinkets of the gift's rarity offered, picked as in live play; the badges of the monsters
    owned then show the trinket's bonuses, as they do on screen."""
    from .choices import pick_trinket, with_trinket_stats
    opts = sorted(n for n, t in data.trinkets().items() if t["rarity"] == rarity)
    if not opts:
        return None
    offered = rng.sample(opts, min(3, len(opts)))
    name = offered[pick_trinket(planner, s, offered)[0]]
    s.team = with_trinket_stats(s.team, name, s.trinkets)
    s.bench = with_trinket_stats(s.bench, name, s.trinkets)
    return name


def play_run(seed: int, policy: str, params: Params | None = None, pool_n: int = 24,
             verbose: bool = False, field: str = "ghosts", live_choices: bool = True) -> dict:
    # Every random draw has its own stream keyed by (seed, day, what, count): two policies that differ in
    # one decision still get the same later shops, rerolls, gifts and opponents, so runs stay paired and
    # small differences between policies can be measured (2026-11-16: with shared streams, one different
    # merge reshuffled every later shop and opponent, and 96 runs could not see half a badge).
    def stream(*what) -> random.Random:
        return random.Random("-".join(map(str, (seed,) + what)))
    # ghosts only in both pools: the recorded real teams would sit at the front of both, and the
    # engine would be graded on the opponents it planned against
    if field in ("gold", "high"):           # the Gold / Platinum-Diamond opponents the bot met (practice.py)
        from . import practice
        fight_pools = practice.pools(field)
    else:
        fight_pools = opponents.pools(n=48, seed=9001, recorded=False)        # who we really meet
    planner = None
    if policy == "engine":
        planner = Planner(Evaluator(opponents.pools(n=pool_n, seed=1, recorded=False)), params or Params(),
                          seed=seed)
    s = RunState(day=1, money=0, rank=1)
    live_choices = live_choices and planner is not None
    if live_choices:
        from .choices import pick_trainer
        offered = stream("trainer").sample(trainer_names(), 3)
        s.trainer = offered[pick_trainer(offered)[0]]
    history = []
    log: list[str] = []
    locked_shop = None
    for day in range(1, MAX_DAYS + 1):
        s.day, s.rank = day, day
        s.money += data.income(day)
        s.items_left = 1
        s.bought, s.rerolls = [], 0
        s.gifts = []
        s.shop = locked_shop if locked_shop else model.random_shop(s.rank, stream(day, "shop"))
        s.locked = False
        if policy == "engine":
            s = engine_turn(s, planner, stream(day, "turn"), log if verbose else None, key=f"{seed}-{day}")
        else:
            s = simple_policy_turn(s, stream(day, "turn"))
        locked_shop = list(s.shop) if s.locked else None
        for j, r in enumerate(s.gifts):
            if live_choices:
                t = choose_gift(planner, s, r, stream(day, "gift", j))
            else:
                t = pick_gift(s, r, stream(day, "gift", j))
            if t:
                s.trinkets.append(t)
        s.gifts = []
        rng = stream(day, "battle")
        opp = rng.choice(fight_pools[min(day, max(fight_pools))])
        hp = data.hp_for_day(day) * (1.25 if s.rescue_used else 1)
        mine = team_spec(s.team, int(hp), s.trainer, s.trinkets)
        o = dict(opp)
        o["hp"] = data.hp_for_day(day)
        r = simulate(mine, o, seed=rng.randrange(1 << 30)) if any(mine["slots"]) else None
        won = r is not None and r.winner == "a"
        if r is not None:
            s.team = growth.carry(s.team, r.units[0])
        if won:
            s.badges += 1
        else:
            s.lives -= data.lives_lost(day)
        history.append("W" if won else "L")
        if verbose:
            team = [f"{u.name}{u.level}" if u else "-" for u in s.team]
            print(f"day {day}: {'W' if won else 'L'} lives {s.lives} badges {s.badges} ${s.money} {team}")
        if s.badges >= data.BADGES_TO_WIN:
            return {"seed": seed, "won": True, "badges": s.badges, "day": day, "arc": "".join(history),
                    "trainer": s.trainer}
        if s.lives <= 0:
            if s.rescue_used:
                return {"seed": seed, "won": False, "badges": s.badges, "day": day, "arc": "".join(history),
                        "trainer": s.trainer}
            s.rescue_used = True
            s.lives = 1
    return {"seed": seed, "won": False, "badges": s.badges, "day": MAX_DAYS, "arc": "".join(history),
            "trainer": s.trainer}


def _job(args):
    seed, policy, params, field, live_choices = args
    return play_run(seed, policy, Params(**params) if params else None, field=field, live_choices=live_choices)


def run_many(policy: str, seeds: list[int], params: Params | None = None, workers: int = DEFAULT_WORKERS,
             field: str = "ghosts", live_choices: bool = True) -> list[dict]:
    jobs = [(s, policy, asdict(params) if params else None, field, live_choices) for s in seeds]
    if policy == "engine":
        opponents.pools(n=24, seed=1, recorded=False)
    if field in ("gold", "high"):
        from . import practice
        practice.pools(field)
    else:
        opponents.pools(n=48, seed=9001, recorded=False)
    if workers <= 1:
        return [_job(j) for j in jobs]
    with ProcessPoolExecutor(workers) as ex:
        return list(ex.map(_job, jobs))


def summary(results: list[dict]) -> dict:
    wins = sum(r["won"] for r in results)
    return {"runs": len(results), "win_rate": round(wins / len(results), 3),
            "mean_badges": round(statistics.mean(r["badges"] for r in results), 2),
            "battle_win_rate": round(sum(r["arc"].count("W") for r in results)
                                     / max(1, sum(len(r["arc"]) for r in results)), 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", choices=["engine", "simple"], default="engine")
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--first-seed", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=default_workers())
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--field", choices=["ghosts", "gold", "high"], default="ghosts",
                    help="who the runs fight: the ghosts (default), the Gold-rank practice field, or the "
                         "Platinum/Diamond one (high)")
    ap.add_argument("--random-choices", action="store_true",
                    help="old behaviour: no trainer, random gift trinkets without badge bonuses")
    ap.add_argument("--params", default="", help='JSON of Params overrides, e.g. {"w_next": 0.3}')
    args = ap.parse_args()
    params = Params(**json.loads(args.params)) if args.params else None
    t = time.time()
    if args.verbose:
        r = play_run(args.first_seed, args.policy, params, verbose=True, field=args.field,
                     live_choices=not args.random_choices)
        print(r)
        return 0
    res = run_many(args.policy, list(range(args.first_seed, args.first_seed + args.runs)), params, args.workers,
                   field=args.field, live_choices=not args.random_choices)
    for r in res:
        print(r)
    print(json.dumps(summary(res)), f"({time.time() - t:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
