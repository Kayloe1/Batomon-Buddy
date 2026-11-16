"""Read-only diagnosis of Gold Ranked runs: where lives were lost, level/stat gaps, churn, merges,
and how far the planner's expected win chance was from the real result.

    .\.venv\Scripts\python.exe gold_diag.py runs\2026-11-15-ranked --since 20261115_020000
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict

STAT_KEYS = ("damage", "shield", "heal", "burn", "poison", "shock")


def unit_power(u: dict | None) -> float:
    if not u:
        return 0.0
    s = {b.get("stat"): b.get("value") or 0 for b in u.get("stats", [])}
    mc = s.get("multicast", 1) or 1
    # a rough "raw output" number: direct damage + statuses weighted, times multicast
    return mc * (s.get("damage", 0) + 0.5 * s.get("shield", 0) + 0.5 * s.get("heal", 0)
                 + 6 * (s.get("burn", 0) + s.get("poison", 0)) + 4 * s.get("shock", 0))


def levels(team: list) -> int:
    return sum((u or {}).get("level") or 0 for u in team if u)


def read_run(path: str) -> dict:
    ev = [json.loads(l) for l in open(path, encoding="utf-8")]
    run = {"file": os.path.basename(path), "days": {}, "trainer": None, "badges": None, "churn": [],
           "merges": 0, "trinkets": 0, "events": []}
    last_state = None
    last_win = None
    acquired: dict[str, list[int]] = defaultdict(list)    # name -> days bought (FIFO)
    day = 0
    for d in ev:
        k = d["kind"]
        if k == "trainer":
            run["trainer"] = d["offered"][d["picked"]] if d.get("picked") is not None else None
        elif k == "shop":
            last_state = d["state"]
            day = last_state.get("day") or day
        elif k == "plan":
            for n in d.get("notes", []):
                if n.startswith("expected win chance today"):
                    last_win = int(n.split()[-1].rstrip("%")) / 100
        elif k == "buy" and d.get("ok"):
            why = d.get("why", "")
            name = why.split(" to buy ")[-1] if " to buy " in why else why.removeprefix("buy ").rsplit(" $", 1)[0]
            if why.startswith("feed "):
                continue
            acquired[name].append(day)
        elif k == "sell" and d.get("ok") and last_state:
            src = d["action"].split("(", 1)[1].rstrip(")").replace("'", "").split(", ")
            where, idx = src[0], int(src[1])
            u = (last_state.get(where) or [None] * 6)[idx]
            if u and u.get("name"):
                name, lvl = u["name"], u.get("level") or 1
                bought = acquired[name].pop(0) if acquired[name] else None
                if lvl == 1 and bought is not None and "feed" not in d.get("why", ""):
                    run["churn"].append({"name": name, "bought": bought, "sold": day, "price": None})
        elif k == "battle":
            seen = d.get("seen") or {}
            mine = d.get("team") or seen.get("my_team") or []      # the shop screen reads our levels
            opp = seen.get("opponent_team") or []
            run["days"][d["day"]] = {
                "my_lv": levels(mine), "my_n": sum(1 for u in mine if u), "my_pow": sum(map(unit_power, mine)),
                "op_lv": levels(opp), "op_unread_lv": sum(1 for u in opp if u and not u.get("level")), "op_n": sum(1 for u in opp if u), "op_pow": sum(map(unit_power, opp)),
                "op_names": [u.get("name") for u in opp if u], "win_now": last_win,
                "my_names": [f"{u.get('name')}{u.get('level')}" for u in mine if u],
                "money": (last_state or {}).get("money"),
            }
        elif k == "day_result":
            r = run["days"].setdefault(d["day"], {})
            r["won"] = d.get("badges", 0) > 0
            r["lives_lost"] = -d.get("lives", 0)
        elif k == "gift":
            run["merges"] += 1
        elif k == "trinket":
            run["trinkets"] += 1
        elif k == "event":
            run["events"].append(d.get("why", [])[-1] if d.get("why") else "")
            run.setdefault("unreadable_events", 0)
            run["unreadable_events"] += sum(1 for e in d.get("effects", []) if e is None)
        elif k == "run_over":
            run["badges"] = d.get("badges")
    return run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--since", default="")
    args = ap.parse_args()
    paths = sorted(p for p in glob.glob(os.path.join(args.folder, "run_*.jsonl"))
                   if os.path.basename(p)[4:19] >= args.since)
    runs = [read_run(p) for p in paths]
    by_day = defaultdict(list)
    gaps = []
    for r in runs:
        print(f"\n== {r['file']} trainer={r['trainer']} badges={r['badges']} merges={r['merges']} "
              f"trinkets={r['trinkets']} churn={len(r['churn'])} unreadable_event_lines={r.get('unreadable_events', 0)}")
        for dday in sorted(r["days"]):
            x = r["days"][dday]
            if "my_lv" not in x:
                continue
            by_day[dday].append(x)
            res = "W" if x.get("won") else "L"
            if x.get("win_now") is not None and "won" in x:
                gaps.append((x["win_now"] - (1.0 if x["won"] else 0.0), r["file"], dday, x))
            print(f" d{dday:2} {res} lost={x.get('lives_lost', 0)} exp={x['win_now']} lv {x['my_lv']:2}/{x['op_lv']:2} "
                  f"pow {x['my_pow']:6.0f}/{x['op_pow']:6.0f} ${x['money']}  me={' '.join(x['my_names'])}  "
                  f"opp={' '.join(n or '?' for n in x['op_names'])}")
        for c in r["churn"]:
            print(f"   churn: {c['name']} bought d{c['bought']} sold d{c['sold']} at Lv1")
    print("\n== by day (all runs)")
    print(" day  n  wins  exp_win  my_lv  op_lv  my_pow  op_pow  lives_lost")
    for dday in sorted(by_day):
        xs = [x for x in by_day[dday] if "won" in x]
        if not xs:
            continue
        n = len(xs)
        exp = [x["win_now"] for x in xs if x["win_now"] is not None]
        print(f" {dday:3} {n:2} {sum(x['won'] for x in xs):5} {sum(exp) / max(1, len(exp)):8.2f} "
              f"{sum(x['my_lv'] for x in xs) / n:6.1f} {sum(x['op_lv'] for x in xs) / n:6.1f} "
              f"{sum(x['my_pow'] for x in xs) / n:7.0f} {sum(x['op_pow'] for x in xs) / n:7.0f} "
              f"{sum(x.get('lives_lost', 0) for x in xs):6}")
    churn = [c for r in runs for c in r["churn"]]
    held = Counter(c["sold"] - c["bought"] for c in churn)
    print(f"\n== churn: {len(churn)} Lv1 monsters bought and sold without merging "
          f"(days held: {dict(sorted(held.items()))}); per run {len(churn) / max(1, len(runs)):.1f}")
    print(f"== merges per run {sum(r['merges'] for r in runs) / max(1, len(runs)):.1f}, "
          f"trinkets per run {sum(r['trinkets'] for r in runs) / max(1, len(runs)):.1f}")
    print("\n== largest gaps: expected win chance vs real result (losses the planner felt safe about)")
    for g, f, dday, x in sorted(gaps, key=lambda t: -t[0])[:12]:
        print(f" {f} d{dday}: expected {x['win_now']:.0%}, lost; lv {x['my_lv']}/{x['op_lv']} "
              f"pow {x['my_pow']:.0f}/{x['op_pow']:.0f} opp={' '.join(n or '?' for n in x['op_names'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
