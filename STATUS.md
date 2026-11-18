# Status (2026-11-14)

Batomon Buddy plays whole **Casual** runs of Batomon Showdown by reading the game window and clicking.
The decision engine (`engine/`) picks every shop action by simulating battles (`sim/`) against a pool of
opponent teams, including real ones the bot recorded. On 2026-11-14 it **won a run: 10 badges on day 14**
(Bug Catcher). Earlier Casual runs: Swim Coach won (10 badges, day 11), Chef and Chemist reached 6 badges.

How often it still needs help (sessions in `logs\` that ended with the bot stopping itself on something it
did not understand): 16 of 17 on 11-11, 24 of 27 on 11-12, 14 of 20 on 11-13, 4 of 7 on 11-14. Every recent
stop was a first-time sight (the "Take All" trinket dialog, a capital K read as H, the "Rank Max" label).
Stopping is deliberate: the bot never guesses an unreadable value.

Real-run timing (6 runs, 53 minutes): battles 33%, clicking and checking about a fifth, thinking 15%
(median 2 s per decision, never over 5 s), reading the Stats window 12%.

## What works (tested live and by 236 tests)
| Part | Status |
|---|---|
| `window.py` | Finds and captures the window (works while covered). Fractions are measured against the 16:9 game picture. |
| `glyphs.py` | Reads the pixel fonts by matching glyph templates in `templates\glyphs.json`. An unknown glyph is `?`, never a guess. |
| `reader.py` | `read_state(image)`. Screens: `new_run`, `trainer_pick`, `shop`, `matchmaking`, `battle`, `result`, `stats`, `event`, `gift_box`, `trinket_pick`, `take_one_confirm`, `select_target`, `overflow`, `cash_in`, `run_over`, `title`, `main_menu`, `main_menu_continue`, `continue_confirm`, `blank` (fade), else `unknown`. On the shop it reads lives, day, badges, trainer, trainer HP, money, shop rank and odds, reroll price (FREE = 0), lock state, 5 shop cards (name, price, stat badges, rarity, shiny), 6 team and 4 bench slots (level 1 to 3, badges, name by sprite). Also event effect lines, trinket names, both teams on the battle screen, the Stats window and the end HP bars. |
| `teach.py` | Adds the glyphs and sprites of the labelled captures in its `LESSONS` list to what the reader knows (never deletes `glyphs.json`; a `.bak` is kept; `--rebuild` starts from nothing). |
| `hands.py` | Buy, use item, move (drag), sell, reroll, lock, battle, confirm, events, gifts, trinkets (incl. Take All), trainer pick, select target, overflow, Cash In, Continue, `start_casual_run()`. Each action reads the state first and checks it afterwards; no click is sent while another window covers the game. |
| `play.py` | The main loop (see `docs\decision-engine.md`). `--simple` uses the old rules, `--learned` the learned policy, `--new-run` starts a Casual run when none is open. Stops on anything unclear and at the end of the run. |
| `gui.py` | The control window (`control_window.bat`): Start, Pause, Stop, status, the thinking log, a red box with the reason for a stop. |
| `engine\` | Planner, model, evaluator, opponent pool, layout search, events, trinkets, trainers. `docs\decision-engine.md`. |
| `sim\` | Offline battle simulator, checked against recorded battles. `docs\simulator.md`, `python -m sim --rules`. |
| `train\` | Offline practice and a learned policy (optional). `docs\training.md`. |
| `batch.py`, `report.py` | Several runs in a row (`--ranked` for Ranked); statistics from the logs. |
| `scorecard.py` | Double-click `scorecard.bat`: a page with one row per run (mode, trainer, result, badges, last day, lives lost, stops), Ranked and Casual apart. Rank before / after is not read yet. |
| `learn_monsters.py` | Hovers monsters the reader cannot name and learns them from the info panel. |
| `tests\` | `.venv\Scripts\python.exe -m unittest discover tests` (about a minute). The screenshots they need are tracked in git (`git add -f` a new one). |

Logs: `logs\run_<time>.jsonl` (every shop read, plan, action, battle, Stats numbers), `logs\thinking_<time>.txt`
(the bot's reasoning in plain English), `logs\actions_<date>.jsonl`, `logs\last_stop.txt`,
`logs\batch_summary.jsonl`, `logs\engine_memory.json` (trinkets, rescue, today's buys; reset when a new run is
detected). Opponent teams met are added to `data\opponents_recorded.jsonl` (70 so far).

## Ranked Mode (allowed by Ed on 2026-11-14, opt-in)
- The New Run dialog has two buttons, Ranked Mode and Casual Mode; the selected one is blue, the other grey.
- `hands.start_run(mode)` presses CONFIRM only after two fresh captures both read "<mode> blue, the other grey,
  Pantra selected". If the reading is unclear it stops. Casual is the default; the Ranked button is clicked
  only by `start_run("ranked")`, which `play.py` uses only with `--ranked` (the control window's
  **Play Ranked** box, off by default; `batch.py --ranked`).
- `play.py` only starts a run when given `--new-run` (the control window's first box, or the Ranked box).
- The Continue panel (a saved run after the game restarts): Confirm is pressed for "CASUAL MODE", and for
  "RANKED MODE" only with `--ranked` and only once `templates\continue_ranked_mode.png` has been saved from a
  real panel. Until then a saved Ranked run stops there with a capture.
- The shop screen shows no mode, so a run that is already open is played whatever its mode. Ed (2026-11-14)
  does not mind if it continues a Ranked run by accident.
- Played live overnight 2026-11-14/15: 23 Ranked runs, 8 won; rank went from Silver 8 to Gold 5 (logs in
  `runs\2026-11-15-ranked`). At Gold it won 1 of 10 runs (6.8 badges on average).

### Why it loses at Gold (2026-11-15, from the overnight logs)
- From day 5 on its team stops levelling up: the six fielded monsters add up to about 7-8 levels on days 5-10,
  while Gold opponents reach 10-11 by days 8-10. It buys single monsters and sells them again, and rarely
  finishes a merge.
- It is too confident there: about 70% expected wins on days 5-10, about 50% real. Even against the very team it
  met, the simulator gave it 74% where it really won 60%: the opponent's trinkets and trainer power are not seen.
- A mode-only opponent pool (Ranked teams only) barely changed its guesses.
- **Practice field:** `python -m engine.runsim --field gold` plays offline runs against the Gold teams it met,
  with the unseen strength estimated from each real battle (`engine/practice.py`, `data/practice_gold.jsonl`).
  The unchanged bot averages about 6-7 badges there, like the real Gold runs, so it can judge fixes offline.
- Tried against it (24 runs each, seeds 3100-3123): unchanged 7 won / 6.3 badges; `POWER_RAMP` 0.45 (expect
  stronger opponents) 7 / 6.0; `copy_progress` 0.10 (value pairs more) 6 / 6.1; holding single Lv1 bench
  monsters + Lv2 bonus 2 / 4.79; keeping all pairs unconditionally + Lv2 bonus 5 / 5.67; extra mid-game
  ramp (`MID_RAMP` 0.25) 2 / 5.38 (pushing ghosts past the 50/50 mark makes single shop buys look flat).
  Original shopping and ramp settings kept.
- **Kept improvements (2026-11-15):**
  - Ghost pools are cached on disk (`data/ghost_pools_cache.json`) and `runsim.py` / `train/learn.py` use all
    available CPU cores (`cpu_count - 2`), cutting 24-run practice tests from ~25 minutes to ~6.5 minutes.
  - `Brain` (`engine/bot.py`, `engine/model.py`) now records the exact prices paid for monsters during a run
    (including `$0` for free monsters from potions or events and discounted prices from Coupon / Bug Catcher),
    so the planner no longer overestimates what selling a free or discounted monster will refund.

### Overnight 2026-11-17/18 (Ranked, unattended)
- Rank: Gold 3 -> Gold 2 -> Gold 1 -> **Platinum 8** (first 3 finished runs: 10, 10, 9 badges), then up and
  down to **Platinum 3** by 06:42. 15 runs finished between 20:31 and 07:10: 5 won, 7.6 badges on average,
  no stops after 23:16. Logs: `logs\overnight_1118.txt` and `logs\run_20261117_*`, `run_20261118_*`.
- **Monitor turned off = no reading.** Windows moved the game to the 1920x1080 screen and shrank it; the reader
  needs exactly 2560x1440 (scaled pictures read 0 of 42 labelled captures, even at 1% smaller).
  `window.fit_to_canonical()` (PR #52) puts the window back, borderless, at 2560x1440 on a big enough monitor
  before every look. With the big monitor off it cannot help and the bot stops.
- **Background mode cannot drag.** With `--background`, clicks work but sells and moves fail ("slot is still
  occupied"), even with a slow drag, and hovering to learn a monster needs the real mouse. The game seems to
  follow the real pointer while dragging. Overnight runs use the normal mode (`batch.py --runs 0 --ranked`).
- A shop action the game ignored (the shop reads exactly as before) is now sent once more (PR #53).
- Taught: a blue "6" in the shop odds bar (Rank 6 shop).
- **Practice tests** (`--field gold`, 96 paired runs, seeds 3100/4100/5100/6100 x24; new baseline on current
  main: 14 won / 5.67 badges): `k_quick` 16 (more opponents per decision) **14 -> 23 won, 6.38 badges
  (+0.71 +/- 0.47) - now the default**; `reroll_samples` 6: +0.38 here and +0.30 on seeds 7100-10100 (not
  significant alone); `reserve` 10: no change at all (the typical-price rule sets the reserve); `money` 0:
  -0.16; `w_next2` 0.5: +0.00. Later checks: `k_quick` 16 on fresh seeds 7100-9100 (72 runs) -0.14, so its
  true gain is probably smaller than +0.71; `reroll_samples` 6 on top of `k_quick` 16: +0.20 (26 won) - **now
  the default too** (positive in all three comparisons). Both together vs the old defaults: +0.90 on seeds
  3100-6100 and +0.25 on fresh seeds 7100-10100; 192 runs, 31 -> 46 won. `k_quick` 24: about the same as 16, not used. Helper scripts: `logs\gold_diag\run96.sh`, `cmp96.py` (git-ignored).

## How to add what the reader does not know yet
**Text on the shop screen, without Claude (new, 2026-11-14, `stop_lessons.py`):** when the red box shows a
stop with a capture, press **Teach it**. The window shows the part of the picture the bot could not read
and what it made of it; type what the game says (`Kickrane $40`, `DAY 9`, `Rank 4  67% 30% 3% 0% 0%`, ...)
and press Teach. Nothing changes unless the number of characters fits the picture and the capture then
reads as typed; a letter that contradicts one the bot is sure of (it read K as H) needs a second click,
"Teach anyway". The glyphs are used at once (press Start again); the answer is saved in
`data\user_lessons.json`, which `teach.py` replays, so `teach.py --rebuild` does not lose it. Without the
window: `python stop_lessons.py [capture]`. It cannot teach sprites, level labels, stat badges, event or
trinket lines, or new screens, and a name missing from `data\names.json`; the window says so, and
"Copy this for Claude" is still the way for those. (The button was built and tested without a screen;
the logic is covered by `tests\test_stop_lessons.py`, the window itself has had no live run yet.)

**Shop card names the bot misreads, by itself (new, 2026-11-14):** when a card's name does not read (an
unknown letter, or a letter read wrong, like K as H in "Hickrane"), the reader looks at the card's picture.
If the picture is clearly one monster's saved card picture (`templates\card_sprites\`) and the text fits that
name with at most one letter off, the card is named and the run goes on. The bot then teaches itself the
letters with the same checks as **Teach it** (`stop_lessons.self_teach_cards`, saved in `data\user_lessons.json`
with a `captures\self_taught_*.png`), and says so in the thinking log. Both have to agree: a picture with no
saved match (a Shiny, a monster never seen in a shop) or text that is two letters off still stops. Checked on
every saved shop capture: 131 cards named right by picture, none wrong. Not yet used in a live run.

**Anything else:** the capture is saved as `captures\unread_*.png`. Look at it, add
an entry with the true text to `LESSONS` in `teach.py`, run `teach.py`, then
`reader.py <capture> --expect` to make it a test (check the printed values by eye first). Keep the capture
in git (`git add -f`) so the lesson can be repeated.

## Limits
- **Window size:** templates only fit a 2560 x 1440 game picture. At another size the reader reports a problem.
- **Level 4** is not read (the label font only knows 1 to 3, and its colour was never captured): the bot stops.
  Stopgap: the engine never makes one (`engine.data.MAX_READABLE_LEVEL = 3`: no level-up event on a level 3,
  no Ultra Candy). A level 4 that arrives anyway stops the run with a message saying so. When one is
  captured, teach the label and set that constant to 4.
- **Any unexpected error** (a covering window, a bug, the mouse-corner emergency stop) ends the session as a
  normal stop: a note in the log and `last_stop.txt`, a `crash_*.png`, a beep and the red box. `batch.py` exits
  with 1 when it stopped for help and 0 when every run finished.
- **Fonts are never complete.** A glyph, name or screen met for the first time stops the run until it is taught.
- Names with unknown letters resolve when exactly one known name fits. A shop card name with one letter read
  wrong (K read as H) and no picture to confirm it is named by `reader.near_miss_name` only when the name has
  at least 5 letters, exactly one letter differs, and no other known name is that close (2026-11-15). Such a
  name is not used to teach letters; two letters off, or one wrong plus one unknown, still stops.
- **Not read:** owned trinkets (kept in the engine's memory), the item counter (assumed until the game refuses
  an item), a monster's passive text (the info panel's picture is saved for a person to read).
- **Team names come from sprites** saved when the bot buys a monster, plus card sprites from every shop seen.
  A monster that arrives another way is named by hovering it (`learn_monsters.py`).
- The real-opponent pool is thin late in a run: days 10 to 14 have 13 recorded teams between them.

## Next steps
1. Teach what the next stops show. The red box now has **Teach it** for shop-screen text (see above):
   use it on the next stops and note which kinds of stop it could not handle. Sprites, level labels, stat
   badges and event / trinket lines are the obvious next kinds to add. First live use of the button is
   still to do.
2. Level 4: capture one, teach the label and its colour.
3. Heals while poisoned: the simulator now guesses that heals do nothing at 60+ Poison (fits five logged
   battles, `docs\simulator.md`). Confirm it with a Replay of a battle with heals at 50 to 150 Poison.
4. Keep recording opponents; re-run `python -m engine.opponents` and `python -m train.learn` as late-day
   teams accumulate.

## Safety rules (unchanged)
- Start Ranked runs only when Ed ticks Ranked (`--ranked`); Casual stays the default.
- Do not decrypt, unpack or modify the game's files, and do not read or write its memory.
- Bots: Ed said on 2026-11-11 that the developer allows bots. (This is Ed's statement; no source is recorded here.)
- Work only inside this folder.
