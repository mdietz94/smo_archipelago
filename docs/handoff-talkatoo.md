# Talkatoo% Gap #2 — Non-Goal Record

Talkatoo% Phases 4 and 5 shipped 2026-05-21. Gaps #1 and #3 are closed. This file records only Gap #2, which is an **explicit non-goal**.

---

## Gap #2 — Named-set persistence across save+quit (non-goal, by design)

`ApState::named_moons_bits` is in-memory only. Save+quit empties it; on next boot the player has to re-talk to Talkatoo to re-name any moons that were named but not yet collected. That is the intended UX — not a limitation awaiting a fix.

Don't implement persistence here. If a future agent thinks this is a bug, re-read this section: the player explicitly likes the "re-talk to confirm" behavior. The shape a misguided implementation *would* take is documented below only so the next agent can recognize and skip it:

1. Bridge persists `named_moons` per slot in its session state (already has a per-slot context object).
2. New wire message `talkatoo_named` (Switch → bridge, on substitute) + `named_replay` (bridge → Switch, on Connected) carries the bitset.
3. Switch's `markMoonNamed` notifies the bridge over the existing SwitchServer channel; `ApClient` consumes `named_replay` like it consumes other HELLO state.

Decision recorded 2026-05-21, reaffirmed 2026-05-22.

For context on Talkatoo% Phases 4 and 5 (both shipped), see [docs/milestones.md](milestones.md).
