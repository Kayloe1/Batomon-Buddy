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
- Ranked has not been played live yet: its screens (rank, rewards, end of run) are expected to stop the bot
  the first time they show, to be taught.
- The shop screen shows no mode, so a run that is already open is played whatever its mode. Ed (2026-11-14)
  does not mind if it continues a Ranked run by accident.

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
