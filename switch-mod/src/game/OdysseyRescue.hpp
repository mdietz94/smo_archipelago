// OdysseyRescue — Lost Kingdom softlock prevention.
//
// In vanilla SMO, arriving in Lost Kingdom (stage ClashWorldHomeStage)
// physically grounds the Odyssey ("in disrepair") and blocks backtracking
// until it is released — unique among SMO kingdoms (the only other kingdom
// that grounds-and-blocks is Ruined, handled differently; see below).
//
// In our randomizer the fill may place the kingdom-internal moons required
// to release the Odyssey anywhere in the pre-arrival reachable set
// (Sand/Lake/Wooded/etc.). A player who rushes into Lost without sweeping
// those upstream checks arrives with 0 AP credits for the current kingdom,
// can't release the Odyssey, and can't fly back to grab the moons stranded
// in upstream checks. Permanent softlock.
//
// This module mirrors Kgamer77/SuperMarioOdysseyArchipelago v1.2's
// updatePlayerInfo() fix: a per-frame (throttled) sweep that detects the
// crashed Odyssey state and unconditionally force-repairs it via SMO's own
// GameDataFunction:: entry points. Unlike Kgamer77 we don't gate on local
// moon counts — the user wants free warp regardless of how many local moons
// they've collected.
//
// Ruined Kingdom is intentionally NOT swept. Ruined grounds the Odyssey via
// the Lord of Lightning boss-attack state, which vanilla releases the instant
// the player beats the dragon and collects the Ruined Multi-Moon. AP fill
// pins that Multi-Moon to its vanilla location (the dragon) via the
// "place_item" entry on "Ruined: Battle with the Lord of Lightning!" in
// locations.json, so beating the dragon always repairs the Odyssey and lets
// the player leave. The old Ruined backtrack-repair path is gone: it risked a
// mUnlockWorldNum counter overshoot that made the post-boss autopilot skip
// Bowser straight to Moon.
//
// Bowser's Kingdom IS swept (added 2026-06-19), but via a wholly different
// state than Lost. Bowser's never touches the home-status enum — after Ruined
// the status is RepairedHomeByCrashedBoss, so isCrashHome is false there.
// Departure is instead gated by GameDataHolder::isBossAttackedHomeNext(worldId)
// (per MonsterDruide1/OdysseyDecomp): true while mUnlockWorldNum is at
// Boss(Ruined)/Boss+1(Sky) and the player is physically in Sky(Bowser's). It
// only clears when beating Bowser advances the unlock to Moon. A capturesanity
// player who flies in without the Pokio capture can't beat the RoboBrood (Pokio
// is the only route up the castle) and can't fly out → permanent softlock.
//
// The Bowser's branch advances the unlock to Moon (the sole lever that clears
// isBossAttackedHomeNext) so the player can fly back out and find Pokio, but
// fires ONLY when captureBlocked("Pokio") is true — i.e. a genuinely-stuck
// capturesanity player. For everyone who can beat Bowser legitimately
// (Pokio-owners, and all non-capturesanity seeds where captures are
// synthetically unlocked at HELLO) the gate is left fully intact, so the
// vanilla Bowser→Moon autopilot — the mUnlockWorldNum overshoot footgun above
// — is never perturbed. The branch resolves its 3 symbols independently of the
// Lost branch, so a resolution failure in one can't disable the other.

#pragma once

namespace smoap::game {

// Resolve the 5 GameDataFunction symbols via hk::ro::lookupSymbol and cache
// function pointers in module-local statics. Call from hkMain after sail's
// nn::ro plumbing is up (i.e., alongside the existing
// installDepositKingdomLookupSymbol / installPayShineSnapshotSymbol calls).
//
// If any symbol fails to resolve, the sweep self-disables (logs once on each
// call attempt). All 5 names live in switch-mod/src/hooks/HookSymbols.hpp
// under the "OdysseyRescue" header; mirrored in
// switch-mod/syms/game/SmoApSymbols.sym.
void installOdysseyRescueSymbols();

// Per-frame softlock sweep. Call from drawMainHook (already running per-
// frame). The function itself is cheap (one boolean read in steady state) but
// since the underlying state changes only on stage transitions, throttle to
// ~60 frames at the call site to keep the log surface clean and match
// Kgamer77's proven cadence.
void runOdysseySoftlockSweep();

}  // namespace smoap::game
