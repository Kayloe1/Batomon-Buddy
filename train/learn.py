"""Learn the shop policy by practising: cross-entropy method with self-play league.

    python -m train.learn --gens 40 --pop 48 --runs 40          # ~5 min on 4 cores
    python -m train.learn --gens 60 --seed 2 --out train/best_policy.json

Each generation: sample `pop` parameter sets (Theta) around the current mean, play `runs` whole
runs with each (same shop seeds for every candidate), score = mean badges, keep the best
quarter and move the mean/spread toward them. The teams the candidates build on each day are
stored as opponents for the next generations (a league, like other players' snapshots in
the real game), mixed with fixed ghost teams and any recorded real teams, so the learner
cannot just beat one fixed opponent set.

Guards against exploiting simulator flaws: the score is measured on opponents the learner never
trained on in train.validate (a separate ghost pool), and validate also re-scores the result under
changed versions of the unverified battle rules.
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

from engine import opponents, practice
from engine.evaluate import Evaluator
from engine.runsim import default_workers

from .env import OppSource, play_run
from .policy import NAMES, SPEC, FastPolicy, Theta, save_theta, theta_from_vec, vec_from_theta

TRAIN_POOL_SEED = 2          # validation uses seed 9001: never seen in training
LEAGUE_PER_DAY = 600
HOF = 3                      # generations of league snapshots kept
DEFAULT_WORKERS = max(1, (os.cpu_count() or 4) - 2)


def _pool(field: str, ghost_n: int) -> dict:
    if field == "gold":
        return practice.gold_pools()
    return opponents.pools(n=ghost_n, seed=TRAIN_POOL_SEED, recorded=False)


def _source(league: dict, ghost_n: int, p_ghost: float, p_league: float, p_recorded: float,
            field: str = "ghosts") -> OppSource:
    rec = {} if field == "gold" else opponents.load_recorded()
    return OppSource(ghosts=_pool(field, ghost_n), league=league, recorded=rec,
                     p_ghost=p_ghost, p_league=p_league, p_recorded=p_recorded)


def evaluate_theta(args) -> dict:
    vec, seeds, league, cfg = args
    field = cfg.get("field", "ghosts")
    ev = Evaluator(_pool(field, 24)) if cfg.get("use_sim") else None
    pol = FastPolicy(theta_from_vec(vec), evaluator=ev)
    src = _source(league, cfg["ghost_n"], cfg["p_ghost"], cfg["p_league"], cfg["p_recorded"], field=field)
    badges = wins = 0
    snaps: dict[int, list[dict]] = {}
    for sd in seeds:
        r = play_run(pol, sd, src, snapshots=cfg["collect"])
        badges += r["badges"]
        wins += r["won"]
        for d, spec in r["snaps"].items():
            snaps.setdefault(d, []).append(spec)
    n = len(seeds)
    return {"fit": badges / n + 2.0 * wins / n, "badges": badges / n, "wins": wins / n, "snaps": snaps}


def learn(gens: int, pop: int, runs: int, seed: int, workers: int, ghost_n: int = 48,
          p_ghost: float = 0.5, p_league: float = 0.35, p_recorded: float = 0.30,
          init: Theta | None = None, use_sim: bool = False, field: str = "ghosts",
          log=print) -> tuple[Theta, list[dict]]:
    rng = random.Random(seed)
    mean = vec_from_theta(init or Theta())
    lo = [SPEC[n][0] for n in NAMES]
    hi = [SPEC[n][1] for n in NAMES]
    sd = [(h - l) * 0.25 for l, h in zip(lo, hi)]
    league_gens: list[dict[int, list[dict]]] = []
    history = []
    cfg = {"ghost_n": ghost_n, "p_ghost": p_ghost, "p_league": p_league, "p_recorded": p_recorded,
           "collect": True, "use_sim": use_sim, "field": field}
    _pool(field, ghost_n)
    if use_sim:
        _pool(field, 24)
    ex = ProcessPoolExecutor(workers) if workers > 1 else None
    try:
        for g in range(gens):
            t0 = time.time()
            league: dict[int, list[dict]] = {}
            for old in league_gens:
                for d, specs in old.items():
                    league.setdefault(d, []).extend(specs)
            for d in league:
                if len(league[d]) > LEAGUE_PER_DAY:
                    league[d] = rng.sample(league[d], LEAGUE_PER_DAY)
            seeds = [rng.randrange(1 << 30) for _ in range(runs)]
            cands = [mean] + [[min(h, max(l, rng.gauss(m, s))) for m, s, l, h in zip(mean, sd, lo, hi)]
                              for _ in range(pop - 1)]
            jobs = [(c, seeds, league, cfg) for c in cands]
            res = list(ex.map(evaluate_theta, jobs)) if ex else [evaluate_theta(j) for j in jobs]
            order = sorted(range(pop), key=lambda i: -res[i]["fit"])
            elite = order[: max(4, pop // 4)]
            new_mean = [statistics.fmean(cands[i][k] for i in elite) for k in range(len(NAMES))]
            new_sd = [max(0.04 * (hi[k] - lo[k]), statistics.pstdev([cands[i][k] for i in elite]))
                      for k in range(len(NAMES))]
            mean = [0.5 * m + 0.5 * n for m, n in zip(mean, new_mean)]
            sd = [0.5 * a + 0.5 * b for a, b in zip(sd, new_sd)]
            snap: dict[int, list[dict]] = {}
            for i in order[: max(4, pop // 2)]:
                for d, specs in res[i]["snaps"].items():
                    snap.setdefault(d, []).extend(specs)
            league_gens = (league_gens + [snap])[-HOF:]
            top = res[order[0]]
            history.append({"gen": g, "best_fit": top["fit"], "best_badges": top["badges"],
                            "mean_badges": statistics.fmean(r["badges"] for r in res),
                            "mean_wins": statistics.fmean(r["wins"] for r in res)})
            log(f"gen {g:3d}  best {top['badges']:.2f} badges, {top['wins']:.0%} won   "
                f"pop mean {history[-1]['mean_badges']:.2f}   ({time.time() - t0:.0f} s)")
    finally:
        if ex:
            ex.shutdown()
    return theta_from_vec(mean), history


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", type=int, default=40)
    ap.add_argument("--pop", type=int, default=48)
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=default_workers())
    ap.add_argument("--p-league", type=float, default=0.35)
    ap.add_argument("--p-ghost", type=float, default=0.5)
    ap.add_argument("--sim", action="store_true", help="policy uses simulator gain as a feature (slower)")
    ap.add_argument("--field", choices=("ghosts", "gold"), default="ghosts",
                    help="opponent pool: synthetic ghosts or Gold practice field")
    ap.add_argument("--init", help="optional path to an initial policy json to warm-start from")
    ap.add_argument("--out", default="train/best_policy.json")
    args = ap.parse_args()
    from pathlib import Path
    from .policy import load_theta
    init = load_theta(Path(args.init)) if args.init else (Theta(w_sim=10.0, buy_base=1.0) if args.sim else None)
    theta, hist = learn(args.gens, args.pop, args.runs, args.seed, args.workers,
                        p_ghost=args.p_ghost, p_league=args.p_league, use_sim=args.sim,
                        field=args.field, init=init)
    save_theta(theta, Path(args.out), {"gens": args.gens, "pop": args.pop, "runs": args.runs,
                                       "seed": args.seed, "field": args.field, "history": hist[-5:]})
    print(json.dumps(theta.__dict__, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
