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
//
// One deliberate departure from Kgamer77, and one alignment back to it:
//   1. (departure) The Ruined block runs BEFORE the Lost block. Ruined's
//      repair path hands off through crashHome → the Lost else-branch's
//      repairHome, and that conversion must complete within a single pass or
//      the Odyssey sits grounded between throttled passes. (Kgamer77 already
//      orders them this way; an interim revision of ours did not.)
//   2. (alignment, restored 2026-06-02) We DO force-unlock Bowser's Kingdom
//      ("Sky") to fix Kgamer77's documented edge case (game repairs the
//      Odyssey in Ruined but skips its own Bowser unlock → half-unlocked
//      Bowser → broken arrival cinematic / frozen camera). The Moon-skip is a
//      mUnlockWorldNum OVERSHOOT: the post-Ruined autopilot switches on that
//      counter (case 11 → Bowser) and unlockNormalWorld() is an unconditional
//      ++, so a double-count (game + us) bumps Bowser→Moon. We avoid being the
//      second increment with a triple gate: isRepairHomeByCrashedBoss(7) AND
//      Bowser-still-locked (isUnlockedWorld false — unlockNextWorld is
//      idempotent so we no-op if the game already did it) AND a multi-pass
//      dwell so the game's own unlock+autopilot settle first. We do NOT write
//      mUnlockWorldNum directly — the exact expected count isn't safely
//      recoverable from the decomp and a bad write corrupts save progression.

#pragma once

namespace smoap::game {

// Resolve the 8 GameDataFunction symbols via hk::ro::lookupSymbol and cache
// function pointers in module-local statics. Call from hkMain after sail's
// nn::ro plumbing is up (i.e., alongside the existing
// installDepositKingdomLookupSymbol / installPayShineSnapshotSymbol calls).
//
// If any symbol fails to resolve, the sweep self-disables (logs once on each
// call attempt). All 8 names live in switch-mod/src/hooks/HookSymbols.hpp
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
