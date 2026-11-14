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
| `batch.py`, `report.py` | Several Casual runs in a row; statistics from the logs. |
| `learn_monsters.py` | Hovers monsters the reader cannot name and learns them from the info panel. |
| `tests\` | `.venv\Scripts\python.exe -m unittest discover tests` (about a minute). The screenshots they need are tracked in git (`git add -f` a new one). |

Logs: `logs\run_<time>.jsonl` (every shop read, plan, action, battle, Stats numbers), `logs\thinking_<time>.txt`
(the bot's reasoning in plain English), `logs\actions_<date>.jsonl`, `logs\last_stop.txt`,
`logs\batch_summary.jsonl`, `logs\engine_memory.json` (trinkets, rescue, today's buys; reset when a new run is
detected). Opponent teams met are added to `data\opponents_recorded.jsonl` (70 so far).

## The Ranked safeguard
- The New Run dialog has two buttons, Ranked Mode and Casual Mode; the selected one is blue, the other grey.
- `hands.start_casual_run()` presses CONFIRM only after two fresh captures both read "Casual blue, Ranked grey,
  Pantra selected". If the reading is unclear it stops. It never clicks the Ranked button, and no other code
  path presses CONFIRM on that dialog. `hands.confirm_continue()` uses the same two-read rule for a saved run.
- `play.py` only starts a run when given `--new-run` (the control window's first box).
- The shop screen shows no mode, so a run that is already open is played whatever its mode. Ed (2026-11-14)
  does not mind if it continues a Ranked run by accident.

## How to add what the reader does not know yet
When `play.py` stops with "cannot read", the capture is saved as `captures\unread_*.png`. Look at it, add
an entry with the true text to `LESSONS` in `teach.py`, run `teach.py`, then
`reader.py <capture> --expect` to make it a test (check the printed values by eye first). Keep the capture
in git (`git add -f`) so the lesson can be repeated.

## Limits
- **Window size:** templates only fit a 2560 x 1440 game picture. At another size the reader reports a problem.
- **Level 4** is not read (the label font only knows 1 to 3, and its colour was never captured): the bot stops.
  Events and Ultra Candy can make one.
- **Fonts are never complete.** A glyph, name or screen met for the first time stops the run until it is taught.
- Names with unknown letters resolve when exactly one known name fits. `reader.near_miss_name` (a look-alike
  letter such as K read as H) is unfinished and always answers "no match".
- **Not read:** owned trinkets (kept in the engine's memory), the item counter (assumed until the game refuses
  an item), a monster's passive text (the info panel's picture is saved for a person to read).
- **Team names come from sprites** saved when the bot buys a monster, plus card sprites from every shop seen.
  A monster that arrives another way is named by hovering it (`learn_monsters.py`).
- The real-opponent pool is thin late in a run: days 10 to 14 have 13 recorded teams between them.

## Next steps
1. Teach what the next stops show; consider a way to teach from the red box without a coding session.
2. Level 4: capture one, teach the label and its colour.
3. Settle the Day 3 simulator gap (do heals count while heavily poisoned?) with a logged battle.
4. Keep recording opponents; re-run `python -m engine.opponents` and `python -m train.learn` as late-day
   teams accumulate.

## Safety rules (unchanged)
- Never start a Ranked run without Ed saying so.
- Do not decrypt, unpack or modify the game's files, and do not read or write its memory.
- Bots: Ed said on 2026-11-11 that the developer allows bots. (This is Ed's statement; no source is recorded here.)
- Work only inside this folder.
