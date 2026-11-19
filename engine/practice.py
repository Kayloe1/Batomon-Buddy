"""A practice field as tough as the Gold-rank opponents the bot really met.

    python -m engine.practice --build      # rebuild data/practice_gold.jsonl from runs/2026-11-15-ranked
    python -m engine.runsim --field gold   # play offline runs against it

    python -m engine.practice --build --field high --logs logs --since run_20261117_231719
                                           # data/practice_high.jsonl: the Platinum/Diamond teams met
                                           # from 2026-11-17 23:17 on (Ranked at Platinum 8 and above)

Why: the ghost opponents (engine/opponents.py) were tuned on Casual runs. Against them the bot wins
most offline runs, while at Gold rank it really won 1 of 10 (2026-11-15, 6.8 badges on average), so
offline tests could not tell a better bot from a worse one there.

How: every Gold battle in the logs gives the opponent's team as the reader saw it at the start of
the battle. What the reader cannot see (the opponent's trinkets and trainer power) is estimated from
how the battle really went: the team's numbers are scaled until the simulator's ending HP matches
the logged ending HP, and the square root of that factor is kept (the full factor made the field
far too hard, 3.4 badges on average; the square root gives about 6-7, like the real Gold runs; the
simulator also misreads early days a little, so part of the factor is its own error). Days with
fewer than 4 such teams are filled with ghosts. Nothing is sent to the game.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim.battle import simulate

from . import data, opponents

ROOT = Path(__file__).resolve().parent.parent
GOLD_FILE = ROOT / "data" / "practice_gold.jsonl"
FIELD_FILES = {"gold": GOLD_FILE, "high": ROOT / "data" / "practice_high.jsonl"}
GOLD_LOGS = ROOT / "runs" / "2026-11-15-ranked"
GOLD_FROM = "run_20261115_020000"       # from here on the overnight runs were at Gold rank
GRID = (0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.7, 2.0, 2.4)
ALPHA = 0.5
MIN_REAL = 4


def scaled(spec: dict, f: float) -> dict:
    out = dict(spec)
    out["slots"] = [None if s is None else {**s, "stats": {k: round(v * f) for k, v in s["stats"].items()}}
                    for s in spec["slots"]]
    return out


def hidden_factor(mine: dict, theirs: dict, day: int, real_margin: float) -> float:
    """The factor on the opponent's numbers that makes the simulated battle end closest to the real one.
    real_margin = my HP share left - their HP share left (from the battle's end screen)."""
    best = None
    for f in GRID:
        a = {**mine, "hp": data.hp_for_day(day)}
        b = {**scaled(theirs, f), "hp": data.hp_for_day(day)}
        r = simulate(a, b, seed=0, until=120)
        err = abs(r.hp[0] / r.max_hp[0] - r.hp[1] / r.max_hp[1] - real_margin)
        if best is None or err < best[0] - 1e-9:
            best = (err, f)
    return best[1]


def build(log_dir: Path = GOLD_LOGS, since: str = GOLD_FROM, alpha: float = ALPHA, field: str = "gold",
          until: str | None = None, ranked_only: bool = False) -> list[dict]:
    """One practice opponent per usable logged battle (logs named run_* at or after `since`, before `until`)."""
    from sim import from_logs
    out = []
    for p in sorted(log_dir.glob("run_*.jsonl")):
        if p.stem < since or (until and p.stem >= until):
            continue
        if ranked_only and not _is_ranked(p):
            continue
        for case in from_logs.battles_from_log(p):
            day = case.get("day")
            end = ((case.get("stats") or {}).get("end_hp_fraction") or {})
            if not isinstance(day, int) or "me" not in end or "opponent" not in end:
                continue
            spec = opponents.from_case(case)
            if spec is None or not any((case.get("a") or {}).get("slots") or []):
                continue
            try:
                f = hidden_factor(case["a"], spec, day, end["me"] - end["opponent"])
            except Exception:          # our own team has a monster the simulator does not know
                continue
            team = scaled(spec, round(f ** alpha, 3))
            team["skill"] = f"practice_{field}"
            team["hidden_factor"] = f
            out.append(team)
    return out


def _is_ranked(p: Path) -> bool:
    """A log of a Ranked session: play.py/batch.py write the mode or a rank card into the log."""
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return '"ranked"' in text.lower() or "rank_result" in text


def save(teams: list[dict], path: Path = GOLD_FILE) -> None:
    path.write_text("".join(json.dumps(t) + "\n" for t in teams), encoding="utf-8")


def load(path: Path = GOLD_FILE) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


_POOLS: dict[str, dict] = {}


def pools(field: str = "gold") -> dict[int, list[dict]]:
    """Opponents per day for offline runs: the field's practice teams, topped up with ghosts on thin days.
    A day with no teams of its own in the "high" field borrows the Gold teams of that day first."""
    if field not in _POOLS:
        out: dict[int, list[dict]] = {}
        for t in load(FIELD_FILES[field]):
            out.setdefault(int(t["day"]), []).append(t)
        if field != "gold":
            for t in load(GOLD_FILE):
                d = int(t["day"])
                if len(out.get(d, [])) < MIN_REAL:
                    out.setdefault(d, []).append(t)
        ghosts = opponents.pools(n=48, seed=9001, recorded=False)
        for d, g in ghosts.items():
            if len(out.get(d, [])) < MIN_REAL:
                out[d] = out.get(d, []) + g[:8]
        _POOLS[field] = out
    return _POOLS[field]


def gold_pools() -> dict[int, list[dict]]:
    return pools("gold")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true", help="rebuild the practice file from the logs")
    ap.add_argument("--field", choices=sorted(FIELD_FILES), default="gold")
    ap.add_argument("--logs", type=Path, default=GOLD_LOGS, help="folder with run_*.jsonl logs")
    ap.add_argument("--since", default=GOLD_FROM, help="first log name (run_YYYYMMDD_HHMMSS) to use")
    ap.add_argument("--until", default=None, help="first log name not to use")
    ap.add_argument("--alpha", type=float, default=ALPHA,
                    help="share of the estimated hidden strength kept (0.5: square root, as for Gold)")
    ap.add_argument("--ranked-only", action="store_true", help="only logs of Ranked sessions")
    args = ap.parse_args()
    path = FIELD_FILES[args.field]
    if args.build:
        teams = build(args.logs, args.since, args.alpha, args.field, args.until, args.ranked_only)
        save(teams, path)
        print(f"{len(teams)} practice opponents written to {path.name}")
    per_day: dict[int, int] = {}
    for t in load(path):
        per_day[t["day"]] = per_day.get(t["day"], 0) + 1
    print("per day:", ", ".join(f"{d}: {n}" for d, n in sorted(per_day.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
