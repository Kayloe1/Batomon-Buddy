"""Build data/jinto_monsters.csv and data/jinto_monsters_levels.csv from a scrape of batomon.com.

    python build_jinto.py path/to/batomon-showdown/monsters.json

Source: github.com/ysk-agent/batomon-showdown (a text copy of batomon.com, Balance 27, 2026-11-15).
Every Pantra monster in it matches data/batomon_pantra.csv on cost and rarity (checked 2026-11-15).
Writes the Jinto monsters that are in neither the Pantra file nor foreign_monsters.csv, in the same
columns, so engine/data.py and sim/data.py read them like the others (as foreign: never in a Pantra shop).
"""
import csv
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
COLS = [("Damage", "Deal {} damage"), ("Heal", "Heal {}"), ("Shield", "Shield {}"), ("Poison", "Poison {}"),
        ("Burn", "Burn {}"), ("Shock", "Shock {}"), ("Multicast", "Multicast {}")]


def active(row: dict) -> str:
    parts = []
    for col, fmt in COLS:
        v = str(row.get(col, "")).strip()
        if v.isdigit() and int(v) > 0 and not (col == "Multicast" and v == "1"):
            parts.append(fmt.format(v))
    return "; ".join(parts)


def main(path: str) -> None:
    mons = json.load(open(path, encoding="utf-8"))
    known = {r["name"] for r in csv.DictReader(open(DATA / "batomon_pantra.csv", encoding="utf-8"))}
    known |= {r["name"] for r in csv.DictReader(open(DATA / "foreign_monsters.csv", encoding="utf-8"))}
    base, lv = [], []
    for m in mons:
        if "Jinto" not in m.get("regions", []) or m["name"] in known or not m.get("cost_gold"):
            continue
        norm = m["detail"]["levels"]["normal"]
        rows = {int(r["Level"].split()[-1]): r for r in norm["rows"]}
        texts = {int(a["level"].split()[-1]): a["text"] for a in norm["abilities"]}
        cd = lambda n: float(str(rows[n].get("Cooldown", "0")).rstrip("s") or 0) if n in rows else 0.0
        base.append({"name": m["name"], "cost": m["cost_gold"], "rarity": m["rarity"], "types": "/".join(m["types"]),
                     "cooldown_s": cd(1), "active": active(rows[1]) if 1 in rows else "",
                     "passive": texts.get(1, m.get("ability_summary", "")),
                     "source": "batomon.com Balance 27, 2026-11-15 (via ysk-agent/batomon-showdown); passive not simulated"})
        row = {"name": m["name"]}
        for n in (2, 3, 4):
            if n not in rows:
                row[f"lv{n}"] = ""
                continue
            pre = f"CD {cd(n):g}s " if cd(n) != cd(n - 1 if n - 1 in rows else 1) else ""
            row[f"lv{n}"] = f"{pre}{active(rows[n])} | {texts.get(n, '')}".strip(" |")
        lv.append(row)
    with open(DATA / "jinto_monsters.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["name", "cost", "rarity", "types", "cooldown_s", "active", "passive", "source"])
        w.writeheader(); w.writerows(base)
    with open(DATA / "jinto_monsters_levels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["name", "lv2", "lv3", "lv4"])
        w.writeheader(); w.writerows(lv)
    print(len(base), "Jinto monsters written")


if __name__ == "__main__":
    main(sys.argv[1])
