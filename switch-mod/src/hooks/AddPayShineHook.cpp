// Hook on GameDataFunction::addPayShine + addPayShineCurrentAll.
//
// THE chokepoint for moon "spend" in SMO. After Orig runs, snapshot
// per-kingdom PayShineNum via ApState::buildPaySnapshot and queue the
// snapshot for the worker. Bridge derives outstanding = lifetime_received_AP
// − PayShineNum.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "AddPayShineHook.hpp"

#include "../ap/ApState.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../util/Log.hpp"

#include <cstdint>

struct GameDataHolderWriter   { void* mData; };
struct GameDataHolderAccessor { void* mData; };

namespace smoap::hooks {

namespace {

using GetCurrentWorldIdNoDevelopFn = int (*)(GameDataHolderAccessor);

int resolveCurrentKingdomBit() {
    auto& s = smoap::ap::ApState::instance();
    void* holder = s.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!holder || !s.get_current_world_id_fn) return 0xff;
    auto fn = reinterpret_cast<GetCurrentWorldIdNoDevelopFn>(s.get_current_world_id_fn);
    GameDataHolderAccessor acc{holder};
    const int world_id = fn(acc);
    return smoap::game::kingdomBitForWorldId(world_id);
}

void queuePaySnapshot(const char* tag) {
    auto& s = smoap::ap::ApState::instance();
    smoap::ap::ApState::PendingPaySnapshot ps{};
    if (!s.buildPaySnapshot(ps)) {
        SMOAP_LOG_WARN("[%s] snapshot build FAILED — bridge won't see "
                       "this deposit until next snapshot", tag);
        return;
    }
    if (!s.pending_pay_snapshots.push(ps)) {
        SMOAP_LOG_WARN("[%s] pending_pay_snapshots ring full — dropping "
                       "(bridge will catch up on next snapshot)", tag);
    }
}

HkTrampoline<void, GameDataHolderWriter, int> addPayShineHook =
    hk::hook::trampoline([](GameDataHolderWriter writer, int count) -> void {
        auto& s = smoap::ap::ApState::instance();
        if (!s.bridge_connected.load(std::memory_order_relaxed)) {
            SMOAP_LOG_WARN("[m6-deposit] addPayShine count=%d BLOCKED (bridge offline)",
                           count);
            return;
        }
        const std::uint8_t bit = static_cast<std::uint8_t>(resolveCurrentKingdomBit());
        addPayShineHook.orig(writer, count);
        SMOAP_LOG_INFO("[m6-deposit] addPayShine count=%d kingdom=%s(bit=%u); "
                       "queuing PaySnapshot for bridge",
                       count,
                       bit < 17 ? smoap::game::kingdomForBit(bit) : "<unknown>",
                       bit);
        queuePaySnapshot("m6-deposit");
    });

HkTrampoline<void, GameDataHolderWriter> addPayShineAllHook =
    hk::hook::trampoline([](GameDataHolderWriter writer) -> void {
        auto& s = smoap::ap::ApState::instance();
        if (!s.bridge_connected.load(std::memory_order_relaxed)) {
            SMOAP_LOG_WARN("[m6-deposit] addPayShineCurrentAll BLOCKED (bridge offline)");
            return;
        }
        const std::uint8_t bit = static_cast<std::uint8_t>(resolveCurrentKingdomBit());
        addPayShineAllHook.orig(writer);
        SMOAP_LOG_INFO("[m6-deposit-all] addPayShineCurrentAll kingdom=%s(bit=%u); "
                       "queuing PaySnapshot for bridge",
                       bit < 17 ? smoap::game::kingdomForBit(bit) : "<unknown>",
                       bit);
        queuePaySnapshot("m6-deposit-all");
    });

}  // namespace

void installAddPayShineHook() {
    SMOAP_LOG_INFO("installing AddPayShineHook -> GameDataFunction::addPayShine");
    addPayShineHook.installAtSym<
        "_ZN16GameDataFunction11addPayShineE20GameDataHolderWriteri">();
}

void installAddPayShineAllHook() {
    SMOAP_LOG_INFO("installing AddPayShineAllHook -> "
                   "GameDataFunction::addPayShineCurrentAll");
    addPayShineAllHook.installAtSym<
        "_ZN16GameDataFunction21addPayShineCurrentAllE20GameDataHolderWriter">();
}

}  // namespace smoap::hooks
