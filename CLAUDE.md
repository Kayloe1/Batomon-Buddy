# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Batomon Buddy is a Windows Python 3.12 bot that plays the game **Batomon Showdown** by screenshotting the
game window, reading it with template matching (no OCR library, no game memory), deciding with a battle
simulator, and clicking. `README.md` has the file-by-file layout; `STATUS.md` is the living record of what
works, what was tried, and what is next. Read `STATUS.md` before changing engine behaviour, and update it
(dated) when results or behaviour change.

The owner, Ed, is tech savvy but not a coder: explain changes and results in plain language, define jargon,
and keep docs and `thinking_*.txt` output readable by a non-programmer. The repo's docs use that style.

## Commands

On Ed's Windows PC everything runs through `.\.venv\Scripts\python.exe` (paths below use plain `python`).

```
python -m unittest discover tests                 # full suite, ~1 min; needs Windows (pywin32) + opencv
python -m unittest tests.test_engine              # one module
python -m unittest tests.test_sim.Data.test_level_numbers  # one test
python play.py [--new-run] [--ranked] [--days N] [--simple|--learned] [--background]   # live play
python gui.py                                     # control window (same options as play.py)
python reader.py [capture.png] [--expect]         # read live window / a capture; --expect writes a .expected.json test
python -m sim --check | --rules | --case FILE --log
python -m engine.runsim --runs 24 --first-seed 3100 [--field gold] [--params '{"w_next":0.3}'] [--verbose]
python -m engine.tune ...  /  python -m train.learn ...  /  python -m train.validate --runs 120
python report.py  /  python scorecard.py  /  python -m sim.from_logs -v
```

**In a Linux cloud container** (no pywin32, no cv2): only the pure-Python modules import. These run:
`python3 -m unittest tests.test_sim tests.test_engine tests.test_train tests.test_practice tests.test_knowledge tests.test_scorecard`
(one test in `test_engine`, and parts of `test_report`, import `play.py`/`reader.py` and error out there; that
is expected, not a regression). `engine.runsim`, `engine.tune`, `train.*` and `sim` all work in the cloud,
so offline experiments run there; anything touching `window`/`reader`/`hands`/`gui` can only be checked on Ed's PC.

There is no linter or build step.

## Architecture (the big picture)

**Live loop** (`play.py`, driven by `gui.py` or `batch.py`): capture (`window.py`, Win32 `PrintWindow`) →
`reader.read_state(image)` returns a dict with a `screen` name plus everything on it → `engine/bot.py`
`Brain` turns that into one action → `hands.py` performs it and **re-reads the screen to verify** → repeat.
The planner is re-run after every single action because the game refills cards, merges, and rolls items.

**Coordinates:** all positions are fractions (0.0-1.0) of the 16:9 game picture inside the window (black bars
excluded). `window.frac_to_screen` / `screen_to_frac` convert; `reader` works on the picture scaled to
2560x1440 and `reader.box_to_frac` converts its boxes. Templates only fit that scale.

**Reading never guesses.** Unknown glyphs read as `?`; an unclear value makes the bot stop, save a capture
(`captures/unread_*.png`), and show a red box in the GUI. This is deliberate. New knowledge is added by:
`teach.py` (`LESSONS` list → `templates/glyphs.json` and sprites; additive, `--rebuild` starts over),
`stop_lessons.py` ("Teach it" button; answers saved in `data/user_lessons.json`, which `teach.py` replays),
and `learn_monsters.py` (hover to learn sprites). Monster names on the team come from sprites, not text.

**Decision engine** (`engine/`, see `docs/decision-engine.md`): `planner.py` tries every candidate next action
(buy/sell/item/reroll/fodder loop) with a short greedy look-ahead and scores each resulting board with
`evaluate.py`, which fights it in the simulator against a pool of opponent teams using common random numbers.
`model.py` holds the pure `RunState` and moves (return new states). `arrange.py` does the layout swap search.
`opponents.py` builds "ghost" opponent pools and merges real recorded teams from
`data/opponents_recorded.jsonl`. `choices.py` (trainer/trinket) and `events.py` score non-shop screens on the
same scale. Tunable weights live in `planner.Params`. `explain.py` writes the plain-English thinking log.

**Simulator** (`sim/`, `docs/simulator.md`): pure Python, ~2 ms/battle. Team format is a dict with 6 `slots`
(row-major, top row first; team faces right). `sim/rules.py` tags each rule verified or guessed;
`sim/cases/` are recorded battles checked by `python -m sim --check`.

**Offline evaluation:** `engine/runsim.py` plays whole runs against a pool with a *different seed* from the
one the engine plans with (don't grade it on its own practice opponents). `--field gold` uses
`engine/practice.py`'s Gold-rank field built from real logged battles; it is the benchmark that best matches
real results. Compare changes on the same seeds (STATUS.md uses 24 runs, seeds 3100-3123) and record the
numbers in STATUS.md. Experiments that did not help are kept as off-by-default flags/params.
`train/` is an optional fast learned policy; it is weaker than the engine and only opt-in (`--learned`).

**Data:** `data/*.csv` is game data copied from the Batopedia (Pantra set). `engine/data.py` fallback
tables are only for planning ahead/offline; live play uses what the screen shows. `logs/` (not in git) holds
`run_*.jsonl`, `thinking_*.txt`, `engine_memory.json`; `runs/` keeps selected log sets used as evidence.

## Rules to keep

- **Ranked safety:** Casual is the default. Ranked runs start only via `hands.start_run("ranked")`, reached
  only with `--ranked` (GUI "Play Ranked" box). Do not add other paths that click the Ranked button.
- Never decrypt, unpack or modify the game's files (`.pck`), or read/write its memory. Work only inside this repo.
- `engine.data.MAX_READABLE_LEVEL = 3`: the reader cannot read level 4 yet, so the engine must never create one.
- Tests are driven by screenshots: a `captures/X.png` with `X.expected.json` is a test case. `captures/` is
  gitignored, so add new needed captures with `git add -f`.
- Every `hands.py` action checks state before and after; keep that pattern for new actions.
