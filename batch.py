"""Play several Casual runs back to back and summarise them.

    python batch.py --runs 5 [--learned | --simple]

Exit code 0: every run finished. Exit code 1: the batch stopped because the bot needed help.

Each run is played by play.main(["--new-run"]): it continues the run that is open, and
when a run ends it starts the next one with hands.start_casual_run() (Casual Mode only,
checked twice; there is no code path that selects Ranked). The batch stops at the first
thing the bot does not understand (loud message + beep, see play.alert) and prints what
it has so far. Push the mouse into a screen corner to abort at any moment.

After every run one line is printed and appended to logs\\batch_summary.jsonl:
badges, battles, the days lives were lost, and how the run ended. `python report.py`
turns all the logs into the longer report (unseen Batomon, shop odds, income).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import play
import report
from hands import LOG_DIR


def one_line(s: dict) -> str:
    lost = ", ".join(f"day {d} -{n}" for d, n in s["lost_lives_on"]) or "none"
    how = "finished" if s["ended"] else f"stopped: {s['stopped']}"
    return (f"{s['trainer']}: {s['badges']} badges in {s['battles']} battles (last day {s['last_day']}), "
            f"lives lost: {lost}; {how}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=3, help="how many Casual runs to play (default 3)")
    ap.add_argument("--learned", action="store_true", help="shop with the learned policy (as play.py --learned)")
    ap.add_argument("--simple", action="store_true", help="use the old simple shop rules (as play.py --simple)")
    args = ap.parse_args(argv)
    play_argv = ["--new-run"] + (["--learned"] if args.learned else []) + (["--simple"] if args.simple else [])
    LOG_DIR.mkdir(exist_ok=True)
    summaries = []
    stopped = False
    for n in range(1, args.runs + 1):
        print(f"\n=== run {n} of {args.runs} ===")
        before_log = play.LAST_LOG
        code = play.main(play_argv)
        fresh = play.LAST_LOG is not None and play.LAST_LOG != before_log       # this call wrote its own log
        s = report.summarize_run(report.load(play.LAST_LOG)) if fresh and play.LAST_LOG.exists() else None
        if s:
            s["log"] = play.LAST_LOG.name
            summaries.append(s)
            print("\nRUN RESULT:", one_line(s))
            with open(LOG_DIR / "batch_summary.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), **s}) + "\n")
        if code != 0 or (s and not s["ended"]):
            print(f"\nBatch stopped after run {n}: the bot needs you (message above).")
            stopped = True
            break
    print("\n=== batch summary ===")
    for i, s in enumerate(summaries, 1):
        print(f"{i}. {one_line(s)}")
    done = [s["badges"] for s in summaries if s["ended"] and s["badges"] is not None]
    if done:
        print(f"Finished runs: {len(done)}, mean {sum(done) / len(done):.1f} badges, best {max(done)}")
    return 1 if stopped else 0          # a nonzero exit code tells a script or scheduler that the batch needed help


if __name__ == "__main__":
    sys.exit(main())
