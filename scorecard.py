"""The scorecard: one row per run (Ranked and Casual), written as a simple web page.

    python scorecard.py            # reads every logs\\run_*.jsonl, writes logs\\scorecard.html and opens it
    python scorecard.py --no-open  # write it only
    (or double-click scorecard.bat)

A run often spans several logs (each Start of the bot writes a new one). A log that picks a
trainer starts a run; the logs after it, until the next trainer pick, continue that run.
The mode (Ranked or Casual) comes from the "new_run" record play.py writes when it starts a
run itself; a run that was started by hand shows "?". Nothing is sent to the game.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import report

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"


def starts_a_run(records: list[dict]) -> bool:
    return any(r.get("kind") == "trainer" and r.get("ok") for r in records)


def group_runs(paths: list[Path]) -> list[dict]:
    """[{"logs": [names], "records": [...]}] in time order, one entry per run."""
    runs: list[dict] = []
    for p in sorted(paths, key=lambda q: q.name):
        recs = report.load(p)
        if not recs:
            continue
        if starts_a_run(recs) or not runs:
            runs.append({"logs": [p.name], "records": list(recs)})
        else:
            runs[-1]["logs"].append(p.name)
            runs[-1]["records"] += recs
    return runs


def run_mode(records: list[dict]) -> str | None:
    """"ranked" / "casual" when the bot started the run itself, else None."""
    started = [r for r in records if r.get("kind") == "new_run" and r.get("ok")]
    if started:
        return started[-1].get("mode") or "casual"       # before 2026-11-15 the bot only started Casual runs
    return None


def rank_summary(records: list[dict]) -> str | None:
    """Short post-run rank string from rank_result records, or None when not recorded."""
    for r in reversed(records):
        if r.get("kind") != "rank_result":
            continue
        parts = []
        if r.get("division") is not None:
            parts.append(f"Div {r['division']}")
        if r.get("stars") is not None:
            parts.append(f"{r['stars']}/5")
        if r.get("rank_up"):
            parts.append("Rank Up!")
        if parts:
            return " ".join(parts)
    return None


def rows(paths: list[Path]) -> list[dict]:
    out = []
    for run in group_runs(paths):
        s = report.summarize_run(run["records"])
        if not s:
            continue
        first = next((r.get("time") for r in run["records"] if r.get("time")), None)
        if s["ended"]:
            result = "won" if (s["badges"] or 0) >= 10 else "lost"
        else:
            result = "stopped" if s["stopped"] else "unfinished"
        out.append({
            "date": first, "mode": run_mode(run["records"]), "trainer": s["trainer"], "result": result,
            "badges": s["badges"], "rank": rank_summary(run["records"]), "last_day": s["last_day"],
            "battles": s["battles"], "lives_lost_on": [d for d, _ in s["lost_lives_on"]], "stopped": s["stopped"],
            "stops": sum(1 for r in run["records"] if r.get("kind") == "stop"
                         and r.get("why") != "stopped from the control window"),
            "logs": run["logs"],
        })
    return out


def totals(rs: list[dict]) -> dict:
    done = [r for r in rs if r["result"] in ("won", "lost")]
    return {"runs": len(rs), "finished": len(done), "won": sum(r["result"] == "won" for r in done),
            "mean_badges": round(sum(r["badges"] or 0 for r in done) / len(done), 1) if done else None,
            "help_needed": sum(r["stops"] for r in rs)}


def trainer_totals(rs: list[dict]) -> list[dict]:
    by_trainer: dict[str, list[dict]] = {}
    for r in rs:
        by_trainer.setdefault(r.get("trainer") or "?", []).append(r)
    out = []
    for trainer, group in by_trainer.items():
        t = totals(group)
        winrate = round(100 * t["won"] / t["finished"]) if t["finished"] else None
        out.append({"trainer": trainer, "runs": t["runs"], "finished": t["finished"],
                    "won": t["won"], "winrate": winrate, "mean_badges": t["mean_badges"]})
    return sorted(out, key=lambda x: (-x["finished"], -x["won"], -(x["mean_badges"] or 0), x["trainer"]))


CSS = """
:root { --bg:#fbfaf7; --fg:#1d1d1b; --muted:#6b6a65; --line:#e3e1da; --win:#1f7a3a; --loss:#b3261e; --stop:#9a6700; }
@media (prefers-color-scheme: dark) { :root { --bg:#161614; --fg:#ecebe6; --muted:#a3a29b; --line:#33322e;
  --win:#5fc27c; --loss:#f08b84; --stop:#e0b252; } }
body { background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; margin:0; padding:16px; }
main { max-width:1100px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:24px 0 8px; }
.muted { color:var(--muted); }
.tiles { display:flex; flex-wrap:wrap; gap:10px; margin:12px 0; }
.tile { border:1px solid var(--line); border-radius:8px; padding:8px 12px; min-width:120px; }
.tile b { display:block; font-size:22px; }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
.won { color:var(--win); font-weight:600; } .lost { color:var(--loss); font-weight:600; }
.stopped, .unfinished { color:var(--stop); font-weight:600; }
td.why { max-width:340px; color:var(--muted); }
"""


def _cell(v) -> str:
    return html.escape("-" if v is None or v == [] else ", ".join(map(str, v)) if isinstance(v, list) else str(v))


def _section(title: str, rs: list[dict]) -> str:
    t = totals(rs)
    tiles = [("Runs", t["runs"]), ("Finished", t["finished"]), ("Won (10 badges)", t["won"]),
             ("Average badges", t["mean_badges"] if t["mean_badges"] is not None else "-"),
             ("Times it stopped for help", t["help_needed"])]
    out = [f"<h2>{html.escape(title)}</h2>", '<div class="tiles">']
    out += [f'<div class="tile"><span class="muted">{html.escape(k)}</span><b>{_cell(v)}</b></div>' for k, v in tiles]
    out.append("</div>")
    if not rs:
        out.append('<p class="muted">No runs yet.</p>')
        return "\n".join(out)
    out.append('<div class="wrap"><table><tr><th>Trainer</th><th>Runs</th><th>Finished</th>'
               '<th>Won</th><th>Winrate</th><th>Avg badges</th></tr>')
    for tr in trainer_totals(rs):
        wr = f"{tr['winrate']}%" if tr["winrate"] is not None else "-"
        out.append(f'<tr><td>{_cell(tr["trainer"])}</td><td>{tr["runs"]}</td><td>{tr["finished"]}</td>'
                   f'<td>{tr["won"]}</td><td>{wr}</td><td>{_cell(tr["mean_badges"])}</td></tr>')
    out.append("</table></div>")
    show_rank = title == "Ranked"
    rank_th = "<th>Rank</th>" if show_rank else ""
    out.append(f'<div class="wrap"><table><tr><th>Started</th><th>Mode</th><th>Trainer</th><th>Result</th>'
               f'<th>Badges</th>{rank_th}<th>Last day</th><th>Lives lost on day</th><th>Stops</th><th>Last stop</th></tr>')
    for r in sorted(rs, key=lambda x: x["date"] or "", reverse=True):
        date = (r["date"] or "").replace("T", " ")[:16]
        mode = {"ranked": "Ranked", "casual": "Casual"}.get(r["mode"], "?")
        rank_td = f'<td>{_cell(r.get("rank"))}</td>' if show_rank else ""
        out.append(f'<tr><td>{_cell(date)}</td><td>{mode}</td><td>{_cell(r["trainer"])}</td>'
                   f'<td class="{r["result"]}">{r["result"]}</td><td>{_cell(r["badges"])}</td>{rank_td}'
                   f'<td>{_cell(r["last_day"])}</td><td>{_cell(r["lives_lost_on"])}</td><td>{r["stops"]}</td>'
                   f'<td class="why">{_cell(r["stopped"])}</td></tr>')
    out.append("</table></div>")
    return "\n".join(out)


def page(rs: list[dict]) -> str:
    ranked = [r for r in rs if r["mode"] == "ranked"]
    others = [r for r in rs if r["mode"] != "ranked"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Batomon Buddy scorecard</title><style>{CSS}</style></head>
<body><main>
<h1>Batomon Buddy scorecard</h1>
<p class="muted">Made {datetime.now():%Y-%m-%d %H:%M} from {sum(len(r["logs"]) for r in rs)} log(s).
A run is won at 10 badges. "Stops" counts the times the bot stopped to ask for help during the run.
Post-run rank (division, stars, Rank Up!) is shown when recorded.</p>
{_section("Ranked", ranked)}
{_section("Casual and runs started by hand", others)}
</main></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logs", nargs="*", type=Path)
    ap.add_argument("--out", type=Path, default=LOG_DIR / "scorecard.html")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the rows as JSON instead")
    args = ap.parse_args(argv)
    paths = args.logs or sorted(LOG_DIR.glob("run_*.jsonl"))
    rs = rows(paths)
    if args.json:
        print(json.dumps(rs, indent=1))
        return 0
    args.out.parent.mkdir(exist_ok=True)
    args.out.write_text(page(rs), encoding="utf-8")
    t = totals([r for r in rs if r["mode"] == "ranked"])
    print(f"{len(rs)} run(s), {t['runs']} Ranked ({t['won']} won). Written: {args.out}")
    if not args.no_open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
