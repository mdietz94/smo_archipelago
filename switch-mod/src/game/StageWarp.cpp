// Manual stage warp for the PC `/warp` softlock-escape command.
//
// Drains ApState::inbound_warp_pending (set by ApClient when a "warp" wire
// message arrives) and teleports Mario to the requested hub kingdom home stage.
//
// Why this is its own TU (and not in WorldMapSelectHook.cpp): it needs the real
// GameDataHolder type to call the inline GameDataHolder::setStageChanging(),
// which would collide with the lightweight `struct GameDataHolderWriter { void*
// mData; }` wrappers the hook files declare locally. Isolating the heavy
// OdysseyHeaders include here keeps those untouched.
//
// Mechanism (the previous two attempts failed, see git history):
//   * DemoWorldWarp alone — GameDataHolder::changeNextStageWithDemoWorldWarp
//     ends with mIsStageChanging = false, so from in-stage the sequence never
//     picks the pending change up and nothing happens.
//   * WorldWarpHole — resolves its destination through SMO's warp-hole *pairing
//     table* (SystemData/WorldList "WorldLinkInfo", src->dest), so passing a
//     home-stage name lands you at that kingdom's painting-paired destination
//     (Cascade input -> Seaside, Cap input -> Lake), not the literal stage. And
//     nothing pairs to Cascade, so it can't target it at all.
// Fix: DemoWorldWarp DOES write the *literal* selected-kingdom stage (it's the
// world-map "fly to this kingdom" path); we just have to latch the change
// ourselves via setStageChanging() so the sequence executes it.
//
// This stays a pure stage change — it never touches mUnlockWorldNum, so it
// cannot unlock a kingdom or open forward progress (e.g. it can't reach Moon to
// escape Bowser's). The destination string was validated to a Switch-side
// allowlist (Cascade/Cap home stages) when the message was decoded in ApClient.

#include "game/System/GameDataFunction.h"
#include "game/System/GameDataHolder.h"
#include "game/System/GameDataHolderWriter.h"

#include <cstddef>

#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::game {

void tickPendingWarp() {
    auto& st = smoap::ap::ApState::instance();
    if (!st.inbound_warp_pending.load(std::memory_order_acquire)) return;
    void* gdh = st.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!gdh) return;  // scene not ready yet — leave the flag set, retry next frame
    st.inbound_warp_pending.store(false, std::memory_order_release);

    // Snapshot the destination stage onto the stack (the socket worker wrote it
    // before setting the flag we just observed, so it's stable).
    char stage[sizeof(st.warp_dest_stage)];
    std::size_t i = 0;
    for (; i + 1 < sizeof(stage) && st.warp_dest_stage[i] != '\0'; ++i) {
        stage[i] = st.warp_dest_stage[i];
    }
    stage[i] = '\0';
    if (stage[0] == '\0') return;

    GameDataHolder* holder = reinterpret_cast<GameDataHolder*>(gdh);
    GameDataHolderWriter writer(holder);
    // Sets the literal next stage (Cascade/Cap), but leaves mIsStageChanging
    // false; the call routes through WorldMapSelectHook's DemoWorldWarp hook,
    // whose order-gate backstop never substitutes Cascade/Cap (no prereq).
    GameDataFunction::tryChangeNextStageWithDemoWorldWarp(writer, stage);
    // Latch so the HakoniwaSequence actually performs the transition.
    holder->setStageChanging();
    SMOAP_LOG_INFO("[warp] DemoWorldWarp + latch -> '%s' (PC /warp)", stage);
}

}  // namespace smoap::game
