"""Every battle rule the simulator uses, with where it comes from.

status:
  verified  - reproduced numerically from a recorded battle (see sim/cases/)
  old-notes - the game's keyword tooltip text as copied into the September Kayloe1/batomon notes.
              The game has been updated since, so these are UNVERIFIED until a recorded battle checks
              them. Only Day 2 and Day 3 (2026-11-11) count as verification.
  observed  - seen in play but not checked numerically by this simulator
  guess     - not known; chosen to be plausible. Change it here when a battle disproves it.

Engine switches (values the code reads) are in SETTINGS. The RULES list is documentation and is
printed by `python -m sim --rules`.
"""

SETTINGS = {
    # Seconds between Poison ticks, and whether ticks run on one battle clock (t = 1, 2, 3...).
    "poison_interval": 1.0,
    "burn_interval": 0.5,
    # Status damage against a shield is multiplied by this.
    "status_vs_shield": 0.85,
    # How Shock adds damage to an attack: "flat" adds the stacks to every hit,
    # "percent" adds stacks% to every hit, "none" ignores Shock.
    "shock_mode": "flat",
    # Shock gained at an instant boosts hits only from the next instant on: two Bumblebolts casting
    # together both dealt 63 (7 casts), and a Panbud casting with them got 2+6+10 Shock (2026-11-12).
    "shock_next_instant": True,
    # Every cast at an instant happens before deaths are checked: the opponent's Craghorn still cast
    # at 18 s in a battle our casts ended at 18 s (2026-11-12).
    "deaths_after_instant": True,
    # Heal cannot raise HP above the maximum.
    "heal_cap": True,
    # GUESS (2026-11-15): a heal does nothing while its trainer has at least this much Poison
    # (None = heals always count). Logged battles fit a threshold between 60 and 74; 70 is in between.
    "heal_blocked_at_poison": 70,
    # Sudden Death: begins at 30 s, deals 8 * 2^((t - 30) / 3) every 0.5 s to both trainers.
    "sudden_death_start": 30.0,
    "sudden_death_interval": 0.5,
    "sudden_death_base": 8.0,
    # At equal times the player's side acts first, then the opponent's.
    "player_first_on_ties": True,
    # Shortest effective cooldown after Cooldown Speed. Without a floor, stacking speed (Galvanine Lv4
    # gains +50% per cast) makes casts come faster without end and the battle never finishes.
    "min_cooldown": 0.25,
    # Longest battle before it is called a draw.
    "time_limit": 300.0,
    # Stop chains of triggers that trigger each other.
    "max_trigger_depth": 8,
}

RULES = [
    # --- time ---
    ("Each monster first acts when its full cooldown has passed (no head start), then every cooldown.",
     "verified", "Day 2 and Day 3 battles: every cast count on both Stats screens matches."),
    ("Cooldown Speed +X% divides the cooldown by (1 + X/100); bonuses add up.",
     "verified", "batomon.com /mechanics/cooldown-speed (Balance 27): max(0.1s, round_to_0.1s(base / max(0.1, 1 + speed) + max(0, added))). Simulator keeps continuous timing."),
    ("Cooldown Speed cannot bring a cooldown below 0.25 s (SETTINGS min_cooldown).",
     "guess", "batomon.com /mechanics/cooldown-speed gives a 0.1 s floor; simulator keeps 0.25 s in SETTINGS min_cooldown for self-accelerating loop safety."),
    ("Charge N s moves the cooldown timer forward by N s; a full timer casts at once; excess Charge is discarded.",
     "verified", "batomon.com /mechanics/charge: Dracana, Ironcore, and Steamscuttle cannot receive Charge."),
    ("Several events at the same instant: casts first (player's side, slots 1-6, then the opponent's), then Burn and Poison ticks.",
     "guess", "batomon.com /mechanics/same-tick-resolution orders Shield -> Damage -> status application -> Heal; exact cast-vs-tick clock ordering and player-first ties are simulator conventions."),
    ("Sudden Death begins at 30 s, dealing 8 * 2^((t - 30) / 3) every 0.5 s to both trainers; at 300 s the battle is called a draw.",
     "verified", "batomon.com /mechanics/sudden-death and 2026-11-15 long battles (42.0 s, 38.0 s, 37.5 s match exact cast counts)."),
    ("A trainer loses the moment its HP reaches 0 after same-instant casts resolve. If both reach 0 on the same tick it is a draw in the simulator.",
     "guess", "batomon.com /mechanics/same-tick-resolution says ties at 0 HP award the win to the player; simulator calls draws on simultaneous lethal hits (tested by tests/test_sim.py)."),
    # --- damage ---
    ("Monsters hit the enemy trainer, never enemy monsters. Shield absorbs damage before HP.",
     "verified", "batomon.com /mechanics/damage and /mechanics/team-hp: Shield absorbs direct Damage before shared team HP."),
    ("Poison deals its stacks as damage once per second, on the battle clock (t = 1, 2, 3 ...), and never decays.",
     "verified", "batomon.com /mechanics/poison and Day 2/3 battles: 1 s tick, no natural decay."),
    ("Burn deals its stacks twice a second and loses 1 stack after each tick.",
     "verified", "batomon.com /mechanics/burn: ticks every 0.5 s equal to current stacks, then loses 1 stack."),
    ("Shock triggers a separate hit equal to the target's Shock stacks on direct Damage (not status/sudden death) and does not decay.",
     "verified", "batomon.com /mechanics/shock: Shock applied on a cast resolves after direct Damage and boosts subsequent hits."),
    ("Poison, Burn, and Shock ticks do 15% less damage while the trainer has a shield, and hit the shield first.",
     "verified", "batomon.com /mechanics/shield and /mechanics/burn /poison /shock: 15% reduction against Shield before Shield absorbs it."),
    ("A heal does nothing while its trainer has 70 Poison or more (SETTINGS heal_blocked_at_poison).",
     "guess", "Fits 2026-11-15 battles: heals at 60 Poison counted (03:48:41), while Day 3 Rich Lady (74 and 101 Poison) and Day 5 (110+ Poison) needed them blocked."),
    ("Heal cannot raise HP above the maximum.",
     "verified", "batomon.com /mechanics/heal: Heal restores shared team HP without exceeding that battle's maximum."),
    ("Multicast N repeats the whole active N times; each repeat resolves On Cast (1 total trigger counted on Stats).",
     "verified", "batomon.com /mechanics/multicast and /mechanics/on-cast: Multicast N means N total casts (0.1 s apart in game; same instant here)."),
    ("'On Cast' passives run once per cast, after all Multicast repeats.",
     "guess", "Simulator runs On Cast once per cast sequence; /mechanics/on-cast notes On Cast resolves per repeat in game."),
    ("'Deal' numbers in the Stats screen are the hit damage only; Poison, Burn and Shock ticks are not in 'Dealt'.",
     "verified", "Day 2: Venopuff 'Dealt 25' = 5 casts x 5, while its Poison did ~164."),
    ("'Trigger this/ally' makes the monster cast at once without resetting its own timer.",
     "verified", "batomon.com /mechanics/triggering: starts the target's cast sequence without resetting current cooldown progress."),
    # --- positions ---
    ("Team slots 1-3 are the top row and 4-6 the bottom row, left to right; your team faces right.",
     "verified", "batomon.com /mechanics/adjacency-and-targeting: 3x2 grid facing right."),
    ("Behind = left neighbour in the row; In front = right neighbour; Above = same column, top row.",
     "verified", "batomon.com /mechanics/adjacency-and-targeting: Front and behind follow the row; above and below follow the column."),
    ("Adjacent = left, right, above or below; not diagonal. Link Cable makes all allies adjacent.",
     "verified", "batomon.com /mechanics/adjacency-and-targeting and Day 3 battle: orthogonal neighbours only."),
    ("Opposite = the enemy monster in the same slot number.",
     "verified", "batomon.com /mechanics/adjacency-and-targeting: mirrored slot on the enemy board."),
    # --- stats on the cards ---
    ("When a slot gives 'stats' (the badges the reader sees on the shop screen), those replace the Batopedia numbers. Trinket and trainer bonuses that change badges are then assumed to be in them already.",
     "guess", "Fire Bell's +5 Burn did show on the card (step 2 notes); whether Wood Sword, Small Club, Chef etc. also show is not checked."),
    ("Cooldown and Multicast bonuses from trinkets are always applied (badges do not show cooldown).",
     "guess", ""),
    ("Trainer HP comes from the input (the reader reads it on the shop screen); +HP trinkets are not added again.",
     "guess", ""),
    # --- statuses ---
    ("Knockout stops a monster from acting and disables its abilities. Protect cancels one Knockout.",
     "verified", "batomon.com /mechanics/knockout and /mechanics/protect: Knockout disables casts and Ongoing effects; Protect spends 1 stack to prevent it."),
    ("Self-knockout monsters (Stingarde, Electranade, Pyronade) act once and are then knocked out.",
     "observed", "Day 3 Electranade cast once."),
    ("Debuffs that Runerock/Sirenade remove are Poison, Burn, and Shock on your own trainer; Sirenade's percentage rounds up.",
     "verified", "batomon.com /mechanics/debuff-removal: Runerock removes flat stacks; Sirenade removes a percentage rounded up to a whole stack."),
    ("Noxnimbus' bonus Poison goes to adjacent Toxic allies only (not itself).",
     "verified", "Day 3: Noxnimbus stayed at 5 Poison, the Spinarai beside it went 5 -> 17 after 4 casts."),
    ("Mosslug deals its damage first, then gains its On Cast damage.",
     "verified", "Day 2: 5 + 25 + 45 = 75 dealt in 3 casts; Day 3: 15 -> 55 after 2 casts."),
    ("Celestia's disable hits Ongoing monsters in its row on both sides (or enemies only when Shiny).",
     "verified", "batomon.com /mechanics/ability-disable: Normal targets both teams in its row; Shiny targets enemies in that row."),
    ("The Dragon Egg has a 10 s cooldown and deals 1 damage.",
     "guess", "Day 2: 1 damage, 1 trigger in a ~19 s battle, so its cooldown is 9.5-19 s."),
    ("Random effects (Tempo Charm, Reapra Lv4) use a seeded random generator.",
     "guess", ""),
    ("Trainer HP per day: 300, 500, 800, 1400, 2400, 3800, 5700, 8300 (days 1-8).",
     "observed", "HP bars in the 2026-11-11 runs. Later days are not in the table; pass hp."),
]

NOT_SIMULATED = [
    "Shop-phase effects (Guardiant, Cinderfly and Shogapede growth, Brawlmantis/Ninflora/Kickrane On Victory, Gildshell, Berroon, Plunderbird $, Craghorn items) - they change the badges the reader sees or economy, not the battle.",
    "Shiny variants beyond explicit rules (SHINY_BATTLE_START, SHINY_PARAM_MULT, SHINY_COOLDOWN_MULT, Shiny Celestia, Shiny Shrinell).",
    "Trinkets: Magic Lasso, and Holy Grail/Mystic Incense/Rainbow Pearl/Ultra Duplicator non-battle effects.",
    "Complex shop-transform/summon passives (Aerophim, Riglet/Rigalord, Omnichrome, Cosmivore, Goldora, Gachapod, Sarudo, Ignit evolution) and non-combat event monsters.",
]
