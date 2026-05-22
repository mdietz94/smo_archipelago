// Hook on rs::getGrowFlowerTime(const al::LiveActor*, const al::PlacementId*).
//
// Returns 1 when Orig returns a non-zero planted timestamp; passes 0 through
// unchanged. The seed/flower actor compares (now - returned_time) against a
// bloom threshold — returning 1 makes the elapsed delta enormous in any
// timebase SMO uses (Unix-epoch seconds, nn::time ticks, etc.), so a planted
// pot advances to bloomed past its threshold.
//
// OBSERVED BEHAVIOR (2026-05-22, Ryujinx + Sand Kingdom Tostarena pot):
// the spawned actor caches its level at spawn time, so the visible bloom
// happens on the NEXT stage entry rather than mid-frame. Plant seed → save
// & reload (or leave & re-enter the kingdom) → pot is fully grown with moon
// ready to collect. Real-time wait collapses from 20–60 min to one area
// transition. The "instant" version of this would require also hooking
// rs::getGrowFlowerGrowLevel to force the visible level on the live actor,
// or hooking the seed actor's tick directly — neither was needed to get
// the wait removed in practice.
//
// CRITICAL: 0 is SMO's sentinel for "this pot has never been planted".
// Returning 0 unconditionally — what we tried first, and what
// MrKatzenGaming/BTT-Studio's "Refresh Seeds" feature does deliberately —
// makes SMO erase the pot's planted state and the seed item on save/reload.
// We pass orig==0 through to preserve unplanted pots, and substitute 1
// only when orig is a real timestamp.
//
// Symbol target is the rs:: wrapper rather than GameDataFile::getGrowFlowerTime
// because the latter is inlined in 1.0.0 main.nso. The rs:: wrapper is
// verified-in-dynsym via MrKatzenGaming/BTT-Studio's pinned offset
// 0x004dd230, and resolved here at build time by sail (see fakesymbols.so).
// See HookSymbols.hpp:kRsGetGrowFlowerTime for full provenance.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "../util/Log.hpp"

#include <cstdint>

namespace al { class LiveActor; class PlacementId; }

namespace smoap::hooks {

namespace {

HkTrampoline<std::uint64_t, const al::LiveActor*, const al::PlacementId*>
    growSeedInstantHook = hk::hook::trampoline(
        [](const al::LiveActor* actor, const al::PlacementId* pid)
            -> std::uint64_t {
            const std::uint64_t orig_v = growSeedInstantHook.orig(actor, pid);
            return (orig_v == 0) ? 0 : 1;
        });

}  // namespace

void installGrowSeedInstantHook() {
    SMOAP_LOG_INFO("installing GrowSeedInstantHook -> rs::getGrowFlowerTime");
    growSeedInstantHook.installAtSym<
        "_ZN2rs17getGrowFlowerTimeEPKN2al9LiveActorEPKNS0_11PlacementIdE">();
}

}  // namespace smoap::hooks
