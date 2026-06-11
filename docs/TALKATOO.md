# Talkatoo% mode — complete reference

This document covers everything Talkatoo%: what it is, how it works for
players, how the code implements it, what invariants must hold, and how
to diagnose regressions or "seed says unbeatable" failures.

If you're playing: skip to **[For players](#for-players)**.
If you're hacking the code: read the **[Logic](#logic)** and **[Invariants](#invariants)** sections.
If something's broken: jump to **[Diagnostics](#diagnostics)**.

---

## Overview

**Talkatoo%** is an opt-in seed mode (`talkatoo_mode: true` in your
player YAML) that turns Talkatoo — the cap-wearing bird who normally
hints at moons in each kingdom — into the **only way to get credit for
non-progression moons in your slot's AP-pool**.

The vanilla SMO game lets Mario collect any of the ~880 in-game moons.
Talkatoo% restricts that: Mario can still pick up moons cosmetically,
but **only moons Talkatoo has actually named in his speech bubble count
toward AP**. Other moons trigger a "Blocked by Talkatoo!" message in
the get-cinematic and respawn on save-reload.

The exception is **progression moons** — Multi Moons, boss-fight
clears, Seaside seals, and Bowser's 4-step chain. Those bypass the
block (`isProgressionShine` on the Switch side) so the player can
always advance the kingdom's scenario regardless of Talkatoo.

---

## For players

### What you'll experience

1. **Talkatoo names moons from a window of 3.** Each kingdom has its
   own list. Walking up to Talkatoo and pressing Y three times in a row
   shows three distinct names from that window — call them #1, #2, #3.
2. **Collect a moon Talkatoo named** → AP gets a check, the window
   slides forward, next visit shows a new entry replacing the one you
   collected.
3. **Try to collect a moon Talkatoo HASN'T named** → the get-cinematic
   plays but the moon flashes "Blocked by Talkatoo!" and you don't
   actually get credit. The moon respawns next time you load the save.
4. **Multi Moons / scenario moons** (Cascade Multi Moon Atop the Falls,
   Seaside's 4 seals, Bowser's 4-step chain, etc.) are always
   collectible without Talkatoo naming them — they're how you advance
   each kingdom's story.
5. **"BK Moon #1 / #2 / #3"** in Talkatoo's bubble is a placeholder.
   It means the system has nothing else valid to suggest in that slot
   right now — either you've cleared most of the kingdom's AP-pool, or
   the bridge isn't fully connected yet. There's no in-game moon by
   those names; if Talkatoo says one, just move on.

### Strategy

- **Visit Talkatoo first in every kingdom you reach.** The window of 3
  shows what's collectible in the kingdom RIGHT NOW given your items.
- **You can always make progress somewhere.** If one kingdom's window
  shows moons you can't reach yet (e.g. all need a capture you don't
  have), try another kingdom's Talkatoo. The validator guarantees that
  at any state, at least one kingdom's window has a reachable moon.
- **Items you receive from AP unlock more.** When another player sends
  you a capture (Paragoomba, Bullet Bill, Sherm, …), moons in earlier
  kingdoms that needed that capture become collectible. Re-visit those
  Talkatoos.
- **Cross-kingdom unlocks are expected.** AP's filler algorithm can
  place a Cap-kingdom capture (like Paragoomba) at a Sand-kingdom moon
  location. To unblock the Cap moons that need Paragoomba, you may
  need to collect the Sand moon first. The Talkatoo order accounts
  for this: Sand's first hints will be reachable without Cap's needed
  captures, so you can make progress in Sand before going back to Cap.

### What WON'T happen

- **Soft-lock from Talkatoo.** The validator runs at seed generation
  and refuses to produce a seed where progress is provably impossible
  — `TalkatooOrderError` aborts generation loudly. If your seed
  generated, somewhere in the game there's always at least one moon
  Talkatoo can name that you can collect.
- **Talkatoo naming a moon you can never reach.** If Talkatoo suggests
  a moon, either (a) you can reach it now, (b) you'll be able to reach
  it after collecting some other Talkatoo-named moon (which gives you
  the missing item), or (c) it's a "BK Moon" placeholder.
- **Locking yourself out of progression.** Multi Moons and story moons
  ignore the Talkatoo% block entirely. You can always advance.

### Save / quit / reconnect behavior

- **Save + quit before collecting a named moon**: when you reload, the
  Switch mod's "Talkatoo has named these" set resets (it's in-memory
  only). The moon goes back to needing Talkatoo to re-name it. **Talk
  to Talkatoo again before trying to collect** — he'll name it on his
  next visit. (This is *intended* behavior, not a limitation — see
  Gap #2 in handoff-talkatoo.md. Persisting the named set is an
  explicit non-goal.)
- **SMOClient restart**: reconnect to AP, the seed's slot_data
  re-populates the order, you keep playing. No state lost on the AP
  side.
- **Switch ↔ bridge reconnect**: HELLO replay re-ships the current
  cursor window. No action needed.
- **Switch crash mid-collection**: the M6 snapshot reconcile picks up
  any check the bridge didn't see. No lost checks.

---

## Logic

### Three layers

```
[ apworld at gen time ]
  build_talkatoo_order() — produces slot_data["talkatoo_order"]
       │  {kingdom: [shine_id_in_global_topological_order, ...]}
       ▼
[ bridge at runtime ]
  _build_talkatoo_pool_phase5() — derives per-kingdom cursor window
       │  Walks each kingdom's order from cursor=smallest-uncollected;
       │  takes 3 entries that are uncollected AND reachable now (A1:
       │  evaluates slot_data["talkatoo_requirements"] vs received items);
       │  ships them per kingdom.
       ▼
[ Switch at runtime ]
  TalkatooSpeechHook substitute hook — picks one entry per visit
       │  Per-kingdom atomic counter cycles 0→1→2→0 across visits;
       │  pads with "BK Moon #N" if pool has <3 entries; writes the
       │  chosen name into Talkatoo's speech bubble UTF-16 buffer.
       ▼
[ Switch at collection time ]
  MoonGetHook block — refuses collect unless named or progression
       │  Reads (stage, obj) from the ShineInfo, resolves to shine_uid
       │  via shine_table.h, checks isMoonNamed(uid) and
       │  isProgressionShine(stage, obj). Block path skips setGotShine
       │  AND paints "Blocked by Talkatoo!" via the cutscene label
       │  pipeline.
       ▼
[ Switch ↔ bridge ↔ AP server ]
  CheckMsg → LocationChecks → RoomUpdate
       │  Bridge cursor recomputes on every RoomUpdate that touches a
       │  moon in any kingdom's order. New window shipped.
```

### Generation-time validator — global sphere-safety

[`apworld/smo_archipelago/talkatoo_order.py`](../apworld/smo_archipelago/talkatoo_order.py)

The validator runs in the `after_fill_slot_data` hook after AP has
placed all items. It:

1. Collects the per-kingdom **pool** — all moon locations in this slot
   that are NOT progression-flagged and NOT captures.
2. Builds a fresh `CollectionState` and sweeps `for_advancements` over
   all of this slot's NON-pool locations. That gives the "baseline
   state before doing pool moons" — captures, progression moons,
   pre-collected items.
3. Runs a **global greedy** across all pool moons (every kingdom
   combined):
   - At each step, find the set of pool moons currently reachable from
     state.
   - If empty → raise `TalkatooOrderError`. The seed is genuinely
     unbeatable.
   - Pick one uniformly at random (sorted for determinism within a rng
     seed); collect its item to advance state; remove from remaining.
   - Repeat.
4. **Projects** the global topological order to per-kingdom orders by
   filtering: kingdom K's order = global positions whose moon is in K.

The result ships in `slot_data["talkatoo_order"] = {kingdom: [shine_id, …]}`.

**Why global, not per-kingdom**: per-kingdom validation would falsely
fail seeds where AP placed a Cap-kingdom prerequisite (e.g. Paragoomba)
at a Sand-kingdom moon. Cap's "sweep over non-Cap locations" wouldn't
include Paragoomba, so Cap's Bonneter-Cap moons would seem unreachable.
Global validation handles this: the global topological order has the
Sand-Paragoomba moon before the Cap-Bonneter moons. The player will
follow the same chain at runtime.

### Bridge — cursor window

[`apworld/smo_archipelago/client/context.py`](../apworld/smo_archipelago/client/context.py)
`SMOContext._build_talkatoo_pool_phase5()`.

On AP `Connected`:
1. Read `slot_data["talkatoo_order"]` into `self.talkatoo_order`.
2. Call `_derive_and_push_talkatoo_pool()` → ships per-kingdom window
   to the Switch via `TalkatooPoolMsg`.

For each kingdom:
- **Cursor** = smallest index in the order whose location is NOT yet
  in `self.checked_locations`.
- **Window** = walk the order from cursor, take the first 3 entries
  that are NOT in `checked_locations` **and are reachable right now**
  (A1 — see below).

The cursor advances when front entries get collected; mid-window
collected entries are skipped on the next push. The walk-and-filter
gives the player a stable 3-slot rotation that updates incrementally
as moons collect.

#### Runtime reachability filter (A1, 2026-06-10)

The fixed `talkatoo_order` alone is **not** sphere-safe in multiworld.
The validator builds it with a solo collect model — "collecting moon[i]
grants the item placed at moon[i], which unlocks moon[i+1]." In a
multiworld your moons' items are mostly destined for OTHER players, and
your own gates (a `|T-Rex|` capture, a `KingdomMoons(Cascade,5)` entry
threshold) are satisfied by items you RECEIVE on a schedule set by other
players. So the cursor can stall on three moons that all need an item
you haven't received, while reachable moons sit just past the window —
an artificial block AP's fill never imposed. (Observed live 2026-06-10:
T-Rex moons at the front of Cascade gating the rest of the kingdom while
T-Rex lived in another player's world.)

The fix: the apworld also ships
`slot_data["talkatoo_requirements"]` — per-moon and per-region access
requirements resolved to `|Item:count|` boolean expressions (every
`{Func()}` baked out at gen time). The bridge evaluates them against the
player's RECEIVED-item counts
([`client/reachability.py`](../apworld/smo_archipelago/client/reachability.py))
and skips any window entry that isn't reachable now — region BFS over the
kingdom graph AND the moon's own requires. A moon is named only if its
kingdom is enterable and its own gate is satisfied.

The re-derive fires on TWO axes now: `RoomUpdate` (checked-locations
changed) AND `ReceivedItems` (a capture or kingdom moon arrived — see
`_process_received_items`). Back-compat: a seed from an apworld that
predates A1 ships no `talkatoo_requirements`; the model is then empty
(`has_data` False) and the window is unfiltered — exactly the pre-A1
behavior. Gen + wire details:
[`talkatoo_requirements.py`](../apworld/smo_archipelago/talkatoo_requirements.py).

On AP `RoomUpdate`: if the delta contains any moon in any kingdom's
order, recompute and re-ship. Other check types (captures, non-pool
moons, other-game collects) short-circuit so we don't burn wire
bandwidth.

Bridge also tracks `_talkatoo_ever_shipped` per session and sends
`moons=[]` clears for kingdoms that drop out of a push (window
exhausted, seed swap). Without that, the Switch's per-kingdom pool
storage would retain stale entries that Talkatoo would re-suggest.

### Switch — substitute hook + block

[`switch-mod/src/hooks/TalkatooSpeechHook.cpp`](../switch-mod/src/hooks/TalkatooSpeechHook.cpp)
+ [`MoonGetHook.cpp`](../switch-mod/src/hooks/MoonGetHook.cpp).

**Substitute hook** trampolines `GameDataFunction::tryFindShineMessage`
(the runtime moon-name-message resolver). When the caller's vtable is
Poetter (the Talkatoo actor class) AND `talkatoo_mode_on` is true:

1. Read the per-kingdom pool for the current kingdom (derived from
   `world_id` via `kingdomBitForWorldId`).
2. If pool has 1-3 real entries: pad with `kHardcodedProbe[k]` to fill
   3 slots, then `pick_idx = per-kingdom-counter % 3`. Picks deterministically
   cycle across visits.
3. If pool is empty: fall back to the hardcoded probe ("BK Moon #1/#2/#3")
   via the same counter cycle. Indicates "bridge has no real moons here."
4. Mark real picks (not padding) as named via `markMoonNamed(shine_uid)`.
   Padding picks skip `markMoonNamed` (no shine_uid).
5. Return the substitute UTF-16 string; SMO's bubble paint pipeline
   renders it.

When `talkatoo_mode_on` is false: early-return vanilla. Non-Talkatoo%
players see normal Talkatoo behavior.

**Block hook** lives inside the existing `MoonGetHook` trampoline on
`GameDataFile::setGotShine` (the universal chokepoint for all 5 Shine
collection entry points). Block path runs when:

```
talkatoo_mode_on
  AND moon's (stage, obj) resolves to a shine_uid in shine_table.h
  AND NOT isMoonNamed(shine_uid)
  AND NOT isProgressionShine(stage, obj)
```

Block path skips `Orig` (no `setGotShine` write) AND paints "Blocked
by Talkatoo!" via the cutscene's `pending_moon_label` pipeline.

---

## Invariants

These are load-bearing — break them and the system desyncs.

### 1. Global topological order

`slot_data["talkatoo_order"]` is a topological sort of the slot's
non-progression moon pool. Each position's moon is reachable from
items at the moons at lower positions PLUS the baseline sweep state.

Enforced by: `find_safe_permutation_with_oracle` in `talkatoo_order.py`.
Tested by: `test_progress_anywhere_invariant_holds_across_global_order`.

### 2. Runtime "progress anywhere"

At any runtime state, Talkatoo's window is empty across ALL kingdoms
ONLY when the slot genuinely has no reachable non-progression moon —
i.e. exactly when the player should be waiting on AP for an item from
another world. Whenever a reachable pool moon exists, some kingdom's
window names it.

This is enforced at RUNTIME by the A1 reachability filter (the bridge
evaluates `talkatoo_requirements` against received items), NOT by the
fixed `talkatoo_order` alone. In a solo seed the validator's global
topological order is already followable in sequence; in multiworld it is
not (items at your own moons flow to other players — see "Runtime
reachability filter" above), so the runtime filter is what makes
"progress anywhere" hold. When no requirements shipped (pre-A1 apworld),
this degrades to the weaker fixed-order guarantee.

Tested by: `test_cross_kingdom_unlock_via_global_greedy` (gen order),
`test_talkatoo_reachability.py` + `test_commands.py::test_phase5_window_
skips_moons_gated_by_unreceived_item` (runtime filter).

### 3. Cursor monotonicity

The bridge cursor for a kingdom = smallest uncollected index in that
kingdom's order. Advances monotonically as moons collect; never goes
backward.

Enforced by: `_compute_talkatoo_cursor` walks the order linearly
checking `loc_id in self.checked_locations`.

### 4. Window-of-3 freshness

The window the bridge ships = first 3 uncollected entries from cursor,
NOT just `order[cursor:cursor+3]`. Critical: mid-window collected
entries must drop out, else Talkatoo re-suggests them indefinitely.

Tested by: `test_connected_phase5_window_skips_mid_window_checks`.

### 5. Per-kingdom storage isolation

The Switch's `ApState::talkatoo_pools[bit]` is per-kingdom-bit (0..16).
A `TalkatooPoolMsg` for kingdom X only writes to slot X. Talkatoo's
substitute hook reads the slot indexed by Mario's current `world_id`.

Cross-kingdom leakage between slots is impossible by construction.

### 6. Pool dropout sends a clear

When a kingdom drops out of the bridge's build (window empty, or seed
swap), the bridge sends `moons=[]` to clear the Switch's slot. Without
this, stale moons from the prior push linger.

Tested by: `test_talkatoo_pool_clears_dropped_kingdoms`.

### 7. Progression moons bypass the block

Progression-flagged locations (`progression: true` in locations.json)
are NEVER in the pool, NEVER in talkatoo_order, and ALWAYS collectible
via the `isProgressionShine` bypass in the block hook.

Tested by: `test_progression_set_matches_audit` (data) +
`test_connected_handler_filters_progression_moons_from_talkatoo_pool` (wire).

### 8. mode-off is fully inert

When `talkatoo_mode_on` is false on the Switch, the substitute hook
returns vanilla and the block path doesn't fire. Non-Talkatoo% players
see no behavioral changes.

Enforced by: early `return vanilla` in `TryFindShineMessageHook::Callback`.

---

## Cross-kingdom progression

This is the part most likely to surprise hackers used to per-kingdom
sphere-safety. **Talkatoo% is sphere-safe globally, not per-kingdom.**

### The model

The player's collected pool moons form a **set** S, not a sequence.
At any state, the set of reachable pool moons depends on the items at S
plus the baseline sweep state. The validator guarantees at least one
moon in (pool \ S) is reachable as long as |S| < |pool|.

### Worked example

Suppose AP placed the Paragoomba capture (a Cap-kingdom enemy
capture) at a Sand pool moon location, e.g. `Sand: Above a Long
Wall`. And Cap has two pool moons that need Paragoomba:
`Cap: Bonneter Cap Coin` and `Cap: Long Beak Coin`.

**Per-kingdom validation would fail Cap**: sweeping Cap's non-pool
locations doesn't reach Sand: Above a Long Wall (it's in Sand, gated
by the Cap→Cascade→Sand chain). Paragoomba never enters Cap's sweep
state. Cap's two paragoomba-needing moons are "unreachable" in Cap's
isolation.

**Global validation handles it cleanly**:
1. Greedy starts with everything reachable from precollected
   (captures with no item gate, etc.).
2. Cap moons that DON'T need Paragoomba are reachable. Some go first.
3. As Cap moons are collected and items propagate, Cascade unlocks.
4. Cascade moons go. Sand unlocks.
5. `Sand: Above a Long Wall` gets picked at some global position.
   Collecting it adds Paragoomba to state.
6. Now `Cap: Bonneter Cap Coin` and `Cap: Long Beak Coin` become
   reachable. They get picked later in the global order.

**Per-kingdom projection**:
- Cap's order = `[non-paragoomba moons..., Bonneter, Long Beak]`
  (paragoomba-needing moons at the tail).
- Sand's order = `[..., Above a Long Wall, ...]` (paragoomba-granting
  moon somewhere in the middle).

**Runtime experience**:
1. Player enters Cap. Cap Talkatoo's window = first 3 of Cap's order
   (paragoomba-free). Player collects, gets items.
2. Player reaches Cascade. Same pattern.
3. Player reaches Sand. Sand Talkatoo's window includes
   `Sand: Above a Long Wall` somewhere. Player collects, receives
   Paragoomba.
4. Player returns to Cap. Cap Talkatoo's cursor has advanced (front
   moons collected). Cap's current window includes the
   paragoomba-needing moons. Now reachable.

The user is **never stuck** in a single kingdom — they can always go
to another kingdom and find a Talkatoo with a reachable suggestion.
Items propagate cross-kingdom; previously-stuck moons unblock.

### Why this is safer than per-kingdom

Per-kingdom would have raised `TalkatooOrderError` at gen time for the
above scenario — refusing to generate a seed that's actually playable.

The cost is: each kingdom's order might have moons that aren't
immediately reachable when you first arrive. That's fine — visit
multiple kingdoms, follow the items.

---

## Diagnostics

### How to verify the system is working

**Bridge log**:
```
[talkatoo] mode=True phase5 pool={'Cap': 3, 'Cascade': 3, ...}
```
Per-kingdom counts should be ≤3 (the cursor window). `phase5 pool=`
indicates the slot_data shipped `talkatoo_order` correctly. If you see
`fallback pool=` instead, the apworld didn't ship `talkatoo_order` —
old apworld build, regenerate the seed.

**Switch log** (Ryujinx log, or SMOClient log once the bridge connects):
```
[talkatoo] applied kingdom=Cascade moons=3
[talkatoo] substituting: world_id=1 kingdom_bit=1 shine_index=N mode=1 -> AP pick 0/3 'Moon Name'
```
`mode=1` = Talkatoo% active. `AP pick K/3` = picked slot K of the
3-entry window. Different `mode=1` lines should cycle K=0, 1, 2 across
consecutive visits in the same kingdom.

**Block firing** (when you try to collect a non-named moon):
```
[talkatoo-block] BLOCKED collection stage=WaterfallWorldHomeStage obj=obj3284 uid=1145 (not named by Talkatoo)
```
Expected for non-progression moons that Talkatoo hasn't named yet.

### Common regression signatures

| Symptom | Likely cause |
|---|---|
| Talkatoo re-suggests a moon you JUST collected | Mid-window filter regression. Check that `_build_talkatoo_pool_phase5` walks-and-filters `checked_locations`, not just slices `order[cursor:cursor+3]`. |
| Talkatoo names >3 distinct moons per kingdom across consecutive visits | Bridge falling back to Phase 4 (pool of N, Switch shuffles 3). Check `slot_data["talkatoo_order"]` is present in the running seed. Probably old seed from before Phase 5 was deployed. |
| Same moon repeats 2x before cycling | Per-kingdom counter not used. Check `g_talkatoo_visit_counters[bit]` is being incremented in `TalkatooSpeechHook.cpp` substitute path. |
| "BK Moon #1" shown when there should be real moons | Either (a) bridge isn't connected to AP yet (talkatoo_mode_on false → falls to probe), (b) bridge hasn't shipped the kingdom's pool yet (early-session window), (c) the kingdom has no AP-pool moons (e.g. all collected, or none in pool to begin with). |
| Non-Talkatoo% player sees "BK Moon" in vanilla speech | `talkatoo_mode_on` gate at the top of `TryFindShineMessageHook::Callback` is bypassed. Check the gate is the FIRST early-return after the Poetter vtable filter. |
| Talkatoo names moon, player collects, but block fires anyway | `markMoonNamed(shine_uid)` not called for the substitute pick. Check `shineUidByDisplayName` returns ≥0 for the chosen ASCII string; padding picks (BK Moon) intentionally skip this. |
| Generation fails with `TalkatooOrderError` | The validator's GLOBAL greedy got stuck — see next section. |

### Unbeatable seeds — triage

If `TalkatooOrderError` raises at generation, the validator could not
find a global topological order. This means **some pool moon is
genuinely unreachable** from any combination of pool + non-pool items
this slot's option set produces.

Triage steps:

1. **Run the validator on the same options without Talkatoo%.** Set
   `talkatoo_mode: false` in your YAML and try again. If generation
   still fails (with an AP-level error, not `TalkatooOrderError`), the
   apworld's logic graph is broken for this option set — fix that
   first.

2. **Check capturesanity.** With `capturesanity: false` and aggressive
   peace toggle off-cases, capture items aren't in the AP pool at all
   but rules still reference them. The result: every rule using
   `|Sherm|` etc. always evaluates true (per `RegionalCap` etc. in
   `hooks/Rules.py`). If you turned capturesanity OFF and STILL hit
   `TalkatooOrderError`, the problem isn't captures.

3. **Inspect the validator's `[talkatoo-order] kingdom=X ordered N moons`
   log lines.** Generation logs one per kingdom. The kingdom that
   FAILED to log is where the global greedy got stuck. Look at the
   apworld logs (`%APPDATA%/Archipelago/logs/Generate_*.txt`) for the
   last `kingdom=` line before the error.

4. **Look for items placed at unreachable locations.** AP's filler
   sometimes places progression items at locations the apworld's
   rules say are unreachable. The validator's sweep won't pick those
   up. Verify by:
   ```
   .meatballsap-decoder slot=1 .archipelago | grep -i "your stuck moon name"
   ```
   And confirm the item placed there isn't itself stuck.

5. **Reduce the pool.** Disable the `include_X_peace_moons` toggles
   for the affected kingdom; this drops trim-able moons from the AP
   pool. If the validator now succeeds, one of the dropped moons had
   the unsatisfiable requirement.

6. **Last resort — `accessibility: minimal`.** Talkatoo% with
   `accessibility: minimal` only requires the victory to be reachable,
   not every check. The validator still tries to order every pool
   moon, but failure is less likely if AP is allowed to leave some
   moons strictly unreachable. (Some Talkatoo% moons may then be
   genuinely uncollectable in your run, but you won't soft-lock.)

### Testing surface

```
# Apworld python tests (unit + integration; ~10s):
python -m pytest apworld/smo_archipelago/tests/

# Just the Talkatoo% surface:
python -m pytest apworld/smo_archipelago/tests/test_talkatoo_order.py
python -m pytest apworld/smo_archipelago/tests/test_progression_moons.py
python -m pytest apworld/smo_archipelago/tests/test_commands.py -k talkatoo
python -m pytest apworld/smo_archipelago/tests/test_switch_server.py -k talkatoo

# Switch-mod C++ host tests (subset that touches shine_lookup):
# See .claude/skills/smo-host-tests/SKILL.md
```

Test coverage map:

| Concern | Test |
|---|---|
| Validator algorithm | `test_talkatoo_order.py` (all `test_*` functions) |
| Cross-kingdom unlock | `test_cross_kingdom_unlock_via_global_greedy` |
| "Progress anywhere" invariant | `test_progress_anywhere_invariant_holds_across_global_order` |
| Loud-fail on unreachable | `test_unreachable_from_start_returns_none`, `test_unreachable_in_middle_returns_none` |
| Sphere-safety window=3 | `test_sphere_safe_window_3_lets_skipped_unlock_succeed`, `test_sphere_safe_window_3_fails_on_isolated_gap` |
| Progression filter (Gap #1) | `test_progression_moons.py`, `test_connected_handler_filters_progression_moons_from_talkatoo_pool` |
| Cursor advance on collect | `test_roomupdate_slides_cursor_when_check_lands_in_order` |
| Mid-window filter (Phase 5 fix) | `test_connected_phase5_window_skips_mid_window_checks` |
| Cursor skips already-checked | `test_connected_phase5_cursor_skips_already_checked` |
| Empty kingdom dropout | `test_connected_phase5_empty_window_when_all_collected` |
| Stale pool clear | `test_talkatoo_pool_clears_dropped_kingdoms` |
| No spurious clears | `test_talkatoo_pool_no_redundant_clears` |
| Disable resets tracker | `test_talkatoo_disable_resets_tracker` |
| RoomUpdate skip-unrelated | `test_roomupdate_skips_reship_when_check_unrelated_to_talkatoo` |
| mode-off no-op | `test_roomupdate_skips_when_talkatoo_mode_off`, `test_connected_handler_honors_slot_data_talkatoo_mode_off` |
| Live AP gen scenario | `test_apworld_generation.py::test_smo_generation_solo[talkatoo_mode-…]` (SMOAP_LIVE_AP=1) |
| End-to-end Switch ↔ bridge | manual: see Ryujinx playtest in PR test plan |

### Known limitations

- **Gap #2 (in-memory named_set) — intentional, not a limitation.**
  Save+quit empties `ApState::named_moons_bits`; moons Talkatoo had
  named but the player hadn't collected before quit need re-naming.
  This is the *intended* UX (re-talk to confirm), explicitly not a
  TODO. See handoff-talkatoo.md Gap #2 before implementing persistence.

- **HELLO-before-Connected window.** When SMO boots before the
  SMOClient ↔ AP connection completes, the Switch's `talkatoo_mode_on`
  stays false until the bridge pushes its first `TalkatooPoolMsg`.
  Player could collect non-progression moons freely during the few
  seconds before the bridge connects. Minor competitive concern;
  acceptable for casual play.

- **In-kingdom captures are swept** (validator assumes you'll capture
  them as needed). If a Cap pool moon needs Paragoomba and Talkatoo
  names it before you've captured one, you have to capture Paragoomba
  in-kingdom first. Not a soft-lock — captures have no item gate.

- **Per-kingdom visit counter resets on SMO restart.** Cycle starts at
  0 again. Cosmetic only.
