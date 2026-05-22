# Talkatoo% follow-ups — handoff plan

Phase 4 (named-set + collection block + Multi Moon exemption) shipped 2026-05-21
and was Ryujinx-verified end-to-end. Two known gaps remain:

- **Gap #1** (small, ~30 min) — bridge-side filter of progression moons from
  `talkatoo_pool`.
- **Gap #3** (significant, ~1–2 days) — Phase 5 sphere-safe ordering: the
  apworld-side validator + slot_data wire + bridge cursor + Switch consumer
  that prevents fresh-start soft-locks.

Gap #2 (named-set persistence across save+quit) is an explicit non-goal — the
in-memory-only behavior is the intended UX (re-talk to Talkatoo after a
save+quit), not a TODO awaiting prioritization.

This doc is the brief for the next agent. Read [Phase 4 in
milestones.md](milestones.md#phase-4--talkatoo-mode) first for context, then
the relevant gap section below.

---

## Gap #1 — Bridge-side filter of progression moons

**Status: closed 2026-05-21.** Implemented as a filter in
[client/context.py](../apworld/smo_archipelago/client/context.py)'s
`_derive_and_push_talkatoo_pool`, backed by a new
`DataPackage.is_progression_location` query that loads the flag from
locations.json on construction (both filesystem and zipped-package
paths). Tests added in
[test_datapackage.py](../apworld/smo_archipelago/tests/test_datapackage.py)
(loader spot-check + degenerate fallbacks) and
[test_commands.py](../apworld/smo_archipelago/tests/test_commands.py)
(end-to-end Connected handler pushes a Cascade pool with the 2
progression entries dropped and the 2 non-progression entries kept).

### What was wrong

The bridge ships every uncollected AP-pool moon to the Switch as part of
`TalkatooPool`, including the 22 moons flagged `progression: true` in
[locations.json](../apworld/smo_archipelago/data/locations.json). When the
substitute hook picks an AP-pool entry to put in Talkatoo's bubble, it can
land on a Multi Moon. The player goes to collect it — fine, the
[isProgressionShine](../switch-mod/src/ap/shine_lookup.hpp) bypass in
`MoonGetHook` lets it through. But Talkatoo just "spent" a hint slot on a
moon the player was going to get for free anyway. Wasted hint.

Not broken; just inefficient. The `progression: true` data is already in the
apworld; we just don't filter on it in the bridge.

### What to change

**One file**, the bridge-side `Connected` handler that builds the per-kingdom
talkatoo_pool message.

[apworld/smo_archipelago/client/context.py](../apworld/smo_archipelago/client/context.py)
already derives the per-kingdom pool from
`missing_locations ∪ checked_locations`. Add a filter step that removes
locations whose name is in a `progression: true` set loaded from
locations.json.

Rough shape (sketch — exact symbols depend on current handler shape):

```python
# Load once at module import. locations.json ships inside the apworld zip;
# Archipelago unzips it on world load so the path is stable.
_PROGRESSION_NAMES: frozenset[str] = frozenset(
    loc["name"]
    for loc in _load_apworld_locations_json()  # whatever your accessor is
    if loc.get("progression", False)
)

def _build_talkatoo_pool_for_kingdom(self, kingdom: str, locs: Iterable[Location]) -> TalkatooPoolMsg:
    moons = []
    for loc in locs:
        if loc.name in _PROGRESSION_NAMES:
            continue  # always-collectible per Phase 4 — no point hinting it
        moons.append(loc.shine_id)  # or whatever field you use
    return TalkatooPoolMsg(kingdom=kingdom, moons=moons, ...)
```

### How to test

1. **Unit test**: add to
   [test_progression_moons.py](../apworld/smo_archipelago/tests/test_progression_moons.py)
   a case that builds a fake talkatoo_pool input including a progression
   moon and asserts it gets filtered.
2. **End-to-end**: generate a Talkatoo% seed (existing
   [smo_talkatoo.yaml](../apworld/smo_archipelago/tests/seeds/smo_talkatoo.yaml)),
   boot Ryujinx, talk to Talkatoo in Cascade. With the filter, Multi Moon
   Atop the Falls should NEVER appear in his bubble. Test by talking to him
   many times in a kingdom whose AP-pool is small enough that the Multi
   Moon would otherwise rotate in.

### Acceptance criteria

- Talkatoo never speaks a progression moon's name.
- Progression moons still collect normally (already handled by Phase 4's
  `isProgressionShine` bypass — the filter is bridge-side, doesn't change
  Switch behavior on collection).
- Apworld pytest suite (`331+ passed`) stays green.

---

## Gap #3 — Phase 5: Sphere-safe ordering

**Status: closed 2026-05-21.** Implemented as a greedy random-tiebreak
validator in [apworld/.../talkatoo_order.py](../apworld/smo_archipelago/talkatoo_order.py),
wired into `after_fill_slot_data` in
[hooks/World.py](../apworld/smo_archipelago/hooks/World.py), consumed
by the bridge via a per-kingdom cursor in
[client/context.py](../apworld/smo_archipelago/client/context.py).

**Notable deviation from the original sketch:** the validator initially
ran with state = precollected only, which the handoff doc sketched.
That model failed loud on the default option set — Bowser's kingdom's
34 AP-pool moons aren't reachable without items from Cap → Ruined.
Fixed by sweeping advancement items from all of this slot's non-pool
locations BEFORE running greedy: the state at cursor=0 represents
"player has just entered this kingdom with the items they earned to
get here." See the docstring for the right-pessimism-level rationale.

**Files touched:**
- `apworld/smo_archipelago/talkatoo_order.py` (new) — validator
- `apworld/smo_archipelago/hooks/World.py` — `after_fill_slot_data` wire
- `apworld/smo_archipelago/client/context.py` — bridge cursor consumer
  + RoomUpdate handler
- `apworld/smo_archipelago/tests/test_talkatoo_order.py` (new) — 14
  validator unit tests using stub reachability oracles (no AP needed)
- `apworld/smo_archipelago/tests/test_commands.py` — 6 bridge cursor
  tests (Connected, RoomUpdate, skip-reship gates)
- `apworld/smo_archipelago/tests/test_apworld_generation.py` —
  `talkatoo_mode` scenario added (gated on SMOAP_LIVE_AP=1)
- `docs/milestones.md` — Phase 5 narrative

**Switch-side hook unchanged.** TalkatooSpeechHook.cpp's `index % n`
picker handles n=3 from the bridge naturally — no code change needed.

### What was wrong

The substitute hook picks AP-pool moons via
`(world_id, vanilla_index) % pool_size`. The mapping has no awareness of
which moons are currently REACHABLE given the player's received items.
Possible — and likely on fresh-start seeds — that Talkatoo's first three
visits in a kingdom all name moons the player can't get without a Capture
or Cap item they haven't received yet. Player has to leave the kingdom and
come back (or load a different save), and if every kingdom is similarly
gated, hard soft-lock.

The Multi Moon exemption from Phase 4 catches the worst case (scenario
advancement) but doesn't help with: "Sand: Bullet Bill Maze Break Through!"
named while the player doesn't have Capture: Bullet Bill yet.

### What to build

A sphere-safe ordered list per kingdom, computed at apworld generation
time using the apworld's existing logic graph, shipped to the bridge via
`slot_data`, advanced by the bridge as moons collect, and consumed by the
Switch substitute hook as the "next 3" replacing today's randomized fold.

Invariant: **at any point in the ordered list, the next 3 entries contain
≥1 moon reachable from items earned by collecting prior entries + the
slot's starting inventory.**

### Why baked at gen time (B1) and not bridge-runtime (B2) or server-side (B3)

Per the [Phase 5 design discussion in the roadmap conversation](#) we
agreed on B1 because:

- **Decouples logic from runtime.** Bridge becomes a dumb cursor advancer;
  no Archipelago logic graph evaluation in SMOClient process.
- **Deterministic + replayable.** Same seed → same Talkatoo experience.
- **Failure mode is loud.** If the gen-time validator can't find a valid
  permutation for some kingdom (over-constrained seed), generation fails
  with an actionable error — better than runtime "no reachable moons"
  silence.
- **Unit-testable.** Pure Python validator over apworld logic; can be
  fuzzed without Switch / network.

### Data flow

```
[gen time]
  apworld/smo_archipelago/_setup/talkatoo_order.py  (new)
    │
    │ for each kingdom in this slot's AP-pool:
    │   - Build a CollectionState seeded with start inventory
    │   - Try random permutations until the invariant holds
    │   - "Invariant" = for every prefix index i in the list,
    │     of the next 3 entries (L[i:i+3]) at least one is
    │     reachable from items earned by collecting L[:i] + start
    │   - Cap retries; raise InvalidWorldError on giving up
    │
    └─► slot_data["talkatoo_order"] = {
            "Cap":     ["shine_id_1", "shine_id_2", ...],
            "Cascade": [...],
            ...,
        }

[connect time, bridge]
  context.py Connected handler:
    │
    │ pool_cursor = {kingdom: 0 for kingdom in slot_data["talkatoo_order"]}
    │ Persist to memory; rebuilt on reconnect from slot_data + checked_locations
    │
    └─► talkatoo_pool sent per kingdom contains exactly:
            slot_data["talkatoo_order"][kingdom][cursor:cursor+3]

[runtime, bridge]
  On every MoonGetHook check (un-blocked moon):
    │
    │ Find the moon in slot_data["talkatoo_order"][kingdom]
    │ If position == cursor: cursor += 1
    │ Else: leave cursor (player collected out-of-window)
    │ Resend talkatoo_pool for this kingdom with the new window
    │
    └─► Switch sees updated pool, next Talkatoo visit picks from new 3

[runtime, switch]
  TalkatooSpeechHook.cpp substitute hook:
    │
    │ Pool is now exactly 3 entries (not full AP-pool). Pick via the
    │ same (world_id, vanilla_index) fold — but since pool size is 3,
    │ the result is one of the 3 ordered entries.
    │
    │ Code change is trivial — the hook already reads `n = pool size`.
    │ Just remove the AP-pool-aware filtering that's now redundant.
    │
    └─► Talkatoo speaks a moon from the cursor-window.
```

### Implementation chunks (suggested order)

1. **`apworld/smo_archipelago/_setup/talkatoo_order.py`** — validator +
   permutation builder. Takes the world's `MultiWorld`, slot id, and the
   AP-pool location list. Returns `dict[kingdom_short_name, list[shine_id]]`
   or raises `InvalidWorldError`. Pure function; trivially unit-testable.
   Algorithm sketch:
   ```python
   def build_order(world, slot, pool_per_kingdom):
       result = {}
       rng = random.Random(world.random.getrandbits(64))
       for kingdom, locs in pool_per_kingdom.items():
           best = _find_safe_permutation(world, slot, kingdom, locs, rng)
           if best is None:
               raise InvalidWorldError(
                   f"talkatoo_mode: kingdom {kingdom!r} has no sphere-safe "
                   f"order of size {len(locs)} (window=3). Consider "
                   f"enabling more captures or disabling tight categories."
               )
           result[kingdom] = best
       return result

   def _find_safe_permutation(world, slot, kingdom, locs, rng, *, retries=1000):
       for _ in range(retries):
           order = locs[:]
           rng.shuffle(order)
           if _is_sphere_safe(world, slot, order, window=3):
               return order
       return None

   def _is_sphere_safe(world, slot, order, *, window):
       """For every prefix i, at least one of order[i:i+window] is
       reachable from items in the multiworld that the slot earns by
       collecting order[:i] + their start inventory."""
       state = world.get_state()  # blank slot state w/ start inv
       for i in range(len(order)):
           upcoming = order[i:i+window]
           if not any(world.can_reach_location(loc, slot, state)
                      for loc in upcoming):
               return False
           # Collect order[i]'s item to advance state for next iteration.
           state.collect(world.get_location(order[i], slot).item)
       return True
   ```

2. **Wire the validator into generation.** In
   [apworld/smo_archipelago/World.py](../apworld/smo_archipelago/World.py)'s
   `fill_slot_data` (or wherever slot_data is built), call
   `build_order(...)` when `talkatoo_mode` is on; stash result under key
   `talkatoo_order`. When off, the key is absent.

3. **Bridge consumes the cursor.**
   [apworld/smo_archipelago/client/context.py](../apworld/smo_archipelago/client/context.py):
   on `Connected`, read `slot_data["talkatoo_order"]`. Build initial
   cursor: count how many entries from the front are in
   `checked_locations`. Push `TalkatooPoolMsg` per kingdom containing only
   the cursor-window of 3 names.

4. **Bridge advances the cursor.** Add a hook in the bridge's incoming
   `LocationChecks` handler: when a check arrives, look up its position
   in the kingdom's order list; if it's at the cursor, advance. Resend
   `TalkatooPoolMsg` for that kingdom.

5. **Switch consumer simplification.** The current `pickThreeUncollected
   FromKingdom` in
   [TalkatooSpeechHook.cpp](../switch-mod/src/hooks/TalkatooSpeechHook.cpp)
   does `Fisher-Yates shuffle` over the full pool. With Phase 5 the
   bridge already sends just 3, so the picker can be simpler — just
   index into the 3. Keep the fold for variety within the 3 entries
   across visits.

### Testing strategy

- **Unit**: `apworld/smo_archipelago/tests/test_talkatoo_order.py` —
  build a synthetic world with a known logic graph + AP-pool, assert the
  validator finds a sphere-safe order. Fuzz with multiple seeds.
- **Integration**: extend
  [test_apworld_generation.py](../apworld/smo_archipelago/tests/test_apworld_generation.py)
  to generate a Talkatoo% seed and assert `slot_data["talkatoo_order"]`
  is populated with valid kingdom keys.
- **End-to-end**: hardest part. The current
  [smo_talkatoo.yaml](../apworld/smo_archipelago/tests/seeds/smo_talkatoo.yaml)
  seed should generate without `InvalidWorldError`. A pessimistic seed
  (everything excluded except a small AP-pool) might fail the validator
  — that's correct behavior (loud failure), document the error message
  in the option's `description`.
- **Manual**: fresh-start Ryujinx run on a Talkatoo% seed, deliberately
  avoid collecting moons in Cascade and progressing through scenario
  edges. Confirm Talkatoo always names at least one reachable moon.

### Risks worth flagging

1. **Logic graph eval cost.** The apworld's rules cover captures, kingdom
   unlocks, Multi-Moon prereqs, shop-purchase moons. Per-permutation
   evaluation is O(pool_size × rule_complexity); the per-kingdom
   AP-pools top out at ~62 moons (Sand). 1000 retries × 62 pos × rule
   eval is fine offline.
2. **Some seeds may be unsatisfiable.** Tight option combos
   (capturesanity off + minimal Peace categories) could yield a kingdom
   with <3 reachable moons total. Validator fails loud; option desc
   needs to mention "expects sufficient pool size".
3. **Trap items.** The apworld places traps as AP items at moon
   locations. The validator must consider a trap-bearing location as
   "filling a slot" (advances cursor) but NOT as an item that unlocks
   downstream (its item is a trap, not progression). Archipelago's
   `CollectionState.collect()` handles this naturally for non-progression
   items; just make sure the validator uses the post-fill state.
4. **Multi Moon interaction.** Per Gap #1, Multi Moons should NOT be in
   `talkatoo_order` at all (they're filtered out at the bridge already).
   Drop them in the apworld-side `pool_per_kingdom` step, before the
   validator runs.

### Acceptance criteria

- Apworld generates Talkatoo% seeds without errors on the default option set.
- `slot_data["talkatoo_order"]` present and per-kingdom lists are a valid
  permutation of (AP-pool minus progression moons) for that kingdom.
- A fresh-start Ryujinx run through Cascade → Sand → Lake collects all
  named moons + Multi Moons without ever encountering a "no reachable
  named moon" state.
- Apworld pytest suite extended with at least one validator test +
  one integration test.

---

## Other Phase 4 follow-up (Gap #2 — non-goal, by design)

Named-set persistence across save+quit is **explicitly not a goal**.
Currently `ApState::named_moons_bits` is in-memory only — save+quit
empties it, and on next boot the player has to re-talk to Talkatoo to
re-name any moons that were named but not collected before quit. That's
the intended UX, not a limitation awaiting a fix.

Don't implement persistence here. If a future agent thinks this is a bug,
re-read this section: the player explicitly likes the "re-talk to confirm"
behavior. The shape a misguided implementation *would* take is documented
below only so the next agent can recognize and skip it:

1. Bridge persists `named_moons` per slot in its session state (already
   has a per-slot context object).
2. New wire message `talkatoo_named` (Switch → bridge, on substitute) +
   `named_replay` (bridge → Switch, on Connected) carries the bitset.
3. Switch's `markMoonNamed` notifies the bridge over the existing
   SwitchServer channel; `ApClient` consumes `named_replay` like it
   consumes other HELLO state.

Decision recorded 2026-05-21, reaffirmed 2026-05-22.
