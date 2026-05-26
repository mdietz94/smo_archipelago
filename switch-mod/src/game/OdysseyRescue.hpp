// OdysseyRescue — Lost + Ruined Kingdom softlock prevention.
//
// In vanilla SMO, arriving in Lost Kingdom (stage ClashWorldHomeStage)
// physically grounds the Odyssey ("in disrepair") and arriving in Ruined
// Kingdom (stage reported as either BossRaidWorldHomeStage or
// AttackWorldHomeStage depending on subsystem — we match both) grounds it
// via the Lord of Lightning boss-attack state. Both kingdoms also block
// backtracking until the Odyssey is released — unique among SMO kingdoms.
//
// In our randomizer the fill may place the kingdom-internal moons required
// to release the Odyssey anywhere in the pre-arrival reachable set
// (Sand/Lake/Wooded/etc.). A player who rushes into Lost/Ruined without
// sweeping those upstream checks arrives with 0 AP credits for the current
// kingdom, can't release the Odyssey, and can't fly back to grab the moons
// stranded in upstream checks. Permanent softlock.
//
// This module mirrors Kgamer77/SuperMarioOdysseyArchipelago v1.2's
// updatePlayerInfo() fix: a per-frame (throttled) sweep that detects the
// crashed / boss-attacked Odyssey state and unconditionally force-repairs it
// via SMO's own GameDataFunction:: entry points. Unlike Kgamer77 we don't
// gate on local moon counts — the user wants free warp regardless of how
// many local moons they've collected.

#pragma once

namespace smoap::game {

// Resolve the 10 GameDataFunction symbols via hk::ro::lookupSymbol and cache
// function pointers in module-local statics. Call from hkMain after sail's
// nn::ro plumbing is up (i.e., alongside the existing
// installDepositKingdomLookupSymbol / installPayShineSnapshotSymbol calls).
//
// If any symbol fails to resolve, the sweep self-disables (logs once on each
// call attempt). All 10 names live in switch-mod/src/hooks/HookSymbols.hpp
// under the "OdysseyRescue" header; mirrored in
// switch-mod/syms/game/SmoApSymbols.sym.
void installOdysseyRescueSymbols();

// Per-frame softlock sweep. Call from drawMainHook (already running per-
// frame). The function itself is cheap (3 boolean reads in steady state) but
// since the underlying state changes only on stage transitions, throttle to
// ~60 frames at the call site to keep the log surface clean and match
// Kgamer77's proven cadence.
void runOdysseySoftlockSweep();

}  // namespace smoap::game
