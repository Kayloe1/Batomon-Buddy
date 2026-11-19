"""Paired practice tests: today's settings ("base") against changed settings, on the same practice runs.

Every finished run is written at once as one line of a .jsonl file, so the test can be stopped at any time
(close the window, restart the PC) and started again with the same command: it skips the runs already done.
The settings take turns seed by seed, so a test cut short still compares like with like.

    python -m engine.abtest --arms "{\"pw15\": {\"power_weight\": 0.15}}" --seeds 12100 96 --out logs/ab/test.jsonl
    python -m engine.abtest --report logs/ab/test.jsonl
    practice.bat                    (double-click: the test in practice_plan.json, at low priority)

`base` (today's settings) is always added. The report shows, per setting, the runs won and the average badges
next to `base` on the same seeds, and the difference with its 95% range (a difference whose range does not
include 0 is unlikely to be luck).

Works with PyPy too (pure Python, 3.9+), which plays practice runs about 3x faster than normal Python: the
simulator gives the same results under both (checked on 25,000 recorded battles and whole runs, 2026-11-18).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed


def _job(args):
    from . import runsim
    from .planner import Params
    name, params, seed, field = args
    r = runsim.play_run(seed, "engine", Params(**params) if params else None, field=field)
    r["arm"] = name
    return r


def _load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass                 # a line cut off by a shutdown: that run is played again
    return out


def report(path: str) -> str:
    by: dict[str, dict[int, dict]] = {}
    for r in _load(path):
        by.setdefault(r["arm"], {})[r["seed"]] = r
    base = by.pop("base", {})
    lines = [f"base: {len(base)} runs, {sum(r['won'] for r in base.values())} won, "
             f"{statistics.mean(r['badges'] for r in base.values()):.2f} badges" if base else "base: no runs yet"]
    for arm, d in sorted(by.items()):
        common = sorted(set(d) & set(base))
        if not common:
            lines.append(f"{arm}: {len(d)} runs, none paired with base yet")
            continue
        diff = [d[s]["badges"] - base[s]["badges"] for s in common]
        half = 1.96 * statistics.stdev(diff) / len(diff) ** 0.5 if len(diff) > 1 else float("nan")
        lines.append(f"{arm}: {len(common)} runs, won {sum(d[s]['won'] for s in common)} vs "
                     f"{sum(base[s]['won'] for s in common)}, badges "
                     f"{statistics.mean(d[s]['badges'] for s in common):.2f} vs "
                     f"{statistics.mean(base[s]['badges'] for s in common):.2f}, "
                     f"difference {statistics.mean(diff):+.2f} +/- {half:.2f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms", default="{}", help='JSON {"name": {Params overrides}}; "base" is always added')
    ap.add_argument("--seeds", nargs=2, type=int, metavar=("FIRST", "COUNT"), default=[12100, 96])
    ap.add_argument("--out", default=os.path.join("logs", "ab", "practice.jsonl"))
    ap.add_argument("--field", choices=["ghosts", "gold", "high"], default="gold")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 4),
                    help="parallel runs (default: all cores but 4, so live play keeps its speed)")
    ap.add_argument("--report", metavar="FILE", help="only print the comparison for this file")
    ap.add_argument("--plan", metavar="FILE", help='a JSON file with "arms", "seeds" [first, count] and "out" '
                                                   "(practice_plan.json, used by practice.bat)")
    a = ap.parse_args(argv)
    if a.plan:
        with open(a.plan, encoding="utf-8") as f:
            plan = json.load(f)
        a.arms = json.dumps(plan.get("arms", {}))
        a.seeds = plan.get("seeds", a.seeds)
        a.out = plan.get("out", a.out)
        a.field = plan.get("field", a.field)
    if a.report:
        print(report(a.report))
        return 0
    arms = {"base": {}}
    arms.update(json.loads(a.arms))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    done = {(r["arm"], r["seed"]) for r in _load(a.out)}
    first, count = a.seeds
    jobs = [(n, p, s, a.field) for s in range(first, first + count) for n, p in arms.items() if (n, s) not in done]
    print(f"{len(jobs)} runs to play ({len(done)} already done), {a.workers} at a time", flush=True)
    from . import opponents, practice
    opponents.pools(n=24, seed=1, recorded=False)      # build the caches once, before the workers start
    if a.field in ("gold", "high"):
        practice.pools(a.field)
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex:
        futures = [ex.submit(_job, j) for j in jobs]
        for k, f in enumerate(as_completed(futures), 1):
            r = f.result()
            with open(a.out, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(r) + "\n")
            print(f"[{k}/{len(jobs)}, {(time.time() - t0) / 60:.0f} min] {r['arm']} seed {r['seed']}: "
                  f"{r['badges']} badges{' (won)' if r['won'] else ''}", flush=True)
    print(report(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
