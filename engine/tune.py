"""Search the planner's settings (Params) by practice: a cross-entropy search on the Gold practice field.

    python -m engine.tune --gens 18 --pop 10 --runs 12 --out /mnt/project-files/synergy/tune

Each generation draws `pop` settings around the current best guess, plays `runs` whole offline runs with
each (the same shops for every candidate, so the comparison is fair), keeps the top few and moves the
guess toward them. The incumbent (the current guess itself) is always scored too, so a generation shows
whether the new draws beat it on the same runs. The search uses a cheaper planner (fewer simulated
opponents per decision, about 5x faster); `python -m engine.tune --validate FILE` re-checks a result at
full planner settings on seeds the search never used.

Score = mean badges + 3 x share of runs won. Output: <out>.jsonl (every candidate, as it is scored) and
<out>_state.json (the guess after each generation, so a stopped search resumes with --resume).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from . import runsim
from .planner import Params

# name: (low, high, integer?)
SPACE = {
    "w_next": (0.1, 1.2, False),
    "w_next2": (0.0, 0.8, False),
    "money": (0.0, 0.02, False),
    "bench_money": (0.2, 1.0, False),
    "copy_progress": (0.0, 0.15, False),
    "power_weight": (0.0, 0.2, False),
    "reserve": (5, 40, True),
    "max_rerolls": (3, 25, True),
    "lock_gain": (0.0, 0.2, False),
    "margin_weight": (0.0, 0.8, False),
    "swap_gain": (0.0, 0.02, False),
    "gift_scale": (0.0, 2.0, False),
}
CHEAP = {"k_final": 8, "k_quick": 5, "greedy_steps": 2, "screen": 1, "reroll_samples": 2}


def clip(name: str, v: float):
    lo, hi, isint = SPACE[name]
    v = min(hi, max(lo, v))
    return int(round(v)) if isint else round(v, 5)


def as_params(guess: dict, cheap: bool) -> dict:
    p = {k: clip(k, v) for k, v in guess.items()}
    if cheap:
        p.update(CHEAP)
    return p


def score(results: list[dict]) -> float:
    return statistics.mean(r["badges"] for r in results) + 3 * sum(r["won"] for r in results) / len(results)


def evaluate(ex, cands: list[dict], seeds: list[int], cheap: bool = True) -> list[list[dict]]:
    jobs = [(s, "engine", as_params(c, cheap), "gold") for c in cands for s in seeds]
    res = list(ex.map(runsim._job, jobs))
    n = len(seeds)
    return [res[i * n:(i + 1) * n] for i in range(len(cands))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gens", type=int, default=18)
    ap.add_argument("--pop", type=int, default=10)
    ap.add_argument("--runs", type=int, default=12)
    ap.add_argument("--elite", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--first-seed", type=int, default=5000)
    ap.add_argument("--out", default="tune")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--validate", default="", help="state file: score its best settings at full planner settings")
    ap.add_argument("--val-runs", type=int, default=48)
    ap.add_argument("--val-seed", type=int, default=7000)
    args = ap.parse_args()
    rng = random.Random(1)
    ex = ProcessPoolExecutor(args.workers)
    if args.validate:
        state = json.loads(Path(args.validate).read_text())
        seeds = list(range(args.val_seed, args.val_seed + args.val_runs))
        base = {k: getattr(Params(), k) for k in SPACE}
        for label, g in (("defaults", base), ("tuned", state["mean"])):
            r = evaluate(ex, [g], seeds, cheap=False)[0]
            print(label, {"wins": sum(x["won"] for x in r), "runs": len(r),
                          "badges": round(statistics.mean(x["badges"] for x in r), 2)}, flush=True)
        return 0
    out = Path(args.out)
    state_file = out.parent / (out.name + "_state.json")
    if args.resume and state_file.exists():
        st = json.loads(state_file.read_text())
        mean, sd, g0 = st["mean"], st["sd"], st["gen"] + 1
    else:
        mean = {k: getattr(Params(), k) for k in SPACE}
        sd = {k: (SPACE[k][1] - SPACE[k][0]) * 0.2 for k in SPACE}
        g0 = 0
    log = open(out.parent / (out.name + ".jsonl"), "a")
    for gen in range(g0, args.gens):
        t = time.time()
        seeds = list(range(args.first_seed + gen * args.runs, args.first_seed + (gen + 1) * args.runs))
        cands = [dict(mean)] + [{k: rng.gauss(mean[k], sd[k]) for k in SPACE} for _ in range(args.pop)]
        res = evaluate(ex, cands, seeds)
        scores = [score(r) for r in res]
        for c, r, s in zip(cands, res, scores):
            log.write(json.dumps({"gen": gen, "score": round(s, 3), "wins": sum(x["won"] for x in r),
                                  "badges": round(statistics.mean(x["badges"] for x in r), 2),
                                  "params": as_params(c, False)}) + "\n")
        log.flush()
        order = sorted(range(1, len(cands)), key=lambda i: -scores[i])[:args.elite]
        for k in SPACE:
            vals = [cands[i][k] for i in order]
            mean[k] = 0.5 * mean[k] + 0.5 * statistics.mean(vals)       # move halfway: the scores are noisy
            sd[k] = max(0.4 * sd[k] + 0.6 * (statistics.pstdev(vals) if len(vals) > 1 else sd[k]),
                        (SPACE[k][1] - SPACE[k][0]) * 0.03)
        state_file.write_text(json.dumps({"gen": gen, "mean": {k: clip(k, v) for k, v in mean.items()}, "sd": sd}))
        print(f"gen {gen}: incumbent {scores[0]:.2f}, best draw {max(scores[1:]):.2f}, "
              f"mean of draws {statistics.mean(scores[1:]):.2f} ({time.time() - t:.0f} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
