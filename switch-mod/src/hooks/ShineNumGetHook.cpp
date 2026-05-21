// Hook on GameDataFunction::getCurrentShineNum(GameDataHolderAccessor).
//
// SMO calls this to render the global moon counter. Returns only our AP
// credit for the current kingdom (per M6 phase D — per-kingdom HUD).

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "../ap/ApState.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../util/Log.hpp"

struct GameDataHolderAccessor {
    void* mData;
};

namespace smoap::hooks {

namespace {

using GetCurrentWorldIdNoDevelopFn = int (*)(GameDataHolderAccessor);

std::uint8_t resolveCurrentKingdomBit() {
    auto& s = smoap::ap::ApState::instance();
    void* holder = s.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!holder || !s.get_current_world_id_fn) return 0xff;
    auto fn = reinterpret_cast<GetCurrentWorldIdNoDevelopFn>(s.get_current_world_id_fn);
    GameDataHolderAccessor acc{holder};
    return smoap::game::kingdomBitForWorldId(fn(acc));
}

HkTrampoline<int, GameDataHolderAccessor> shineNumGetHook =
    hk::hook::trampoline([](GameDataHolderAccessor accessor) -> int {
        const int orig = shineNumGetHook.orig(accessor);
        auto& s = smoap::ap::ApState::instance();

        int ap_value = 0;
        const char* kname = "<offline>";
        std::uint8_t bit = resolveCurrentKingdomBit();
        const bool online = s.bridge_connected.load(std::memory_order_relaxed);
        if (online) {
            if (bit < 17) {
                ap_value = s.ap_moons_kingdom[bit].load(std::memory_order_relaxed);
                kname = smoap::game::kingdomForBit(bit);
            } else {
                kname = "<unresolved>";
            }
        }

        static int s_call_count = 0;
        static int s_last_returned = -1;
        static int s_last_orig = -1;
        static std::uint8_t s_last_bit = 0xff;
        const bool first_calls = (s_call_count < 3);
        const bool ret_changed = (ap_value != s_last_returned);
        const bool orig_changed = (orig != s_last_orig);
        const bool bit_changed = (bit != s_last_bit);
        if (first_calls || ret_changed || orig_changed || bit_changed) {
            SMOAP_LOG_INFO("[m6-hook] getCurrentShineNum: smo_natural=%d "
                           "ap_kingdom=%s(bit=%u) ap=%d (call#%d)",
                           orig, kname, bit, ap_value, s_call_count + 1);
        }
        ++s_call_count;
        s_last_returned = ap_value;
        s_last_orig = orig;
        s_last_bit = bit;
        return ap_value;
    });

}  // namespace

void installShineNumGetHook() {
    SMOAP_LOG_INFO("installing ShineNumGetHook -> "
                   "GameDataFunction::getCurrentShineNum");
    shineNumGetHook.installAtSym<
        "_ZN16GameDataFunction18getCurrentShineNumE22GameDataHolderAccessor">();
}

}  // namespace smoap::hooks
