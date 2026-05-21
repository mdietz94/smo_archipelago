// Hook on GameDataFunction::getGotShineNum(GameDataHolderAccessor, s32 file_id).
//
// We trampoline through orig() and DELIBERATELY DROP orig — only the AP-credit
// total is returned. Kept hooked as defense; see production switch-mod's
// comment block for the OdysseyDecomp file_id/world_id semantics audit.

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

int sumAllKingdomCredits() {
    int total = 0;
    auto& s = smoap::ap::ApState::instance();
    for (auto& a : s.ap_moons_kingdom) {
        total += a.load(std::memory_order_relaxed);
    }
    return total;
}

HkTrampoline<int, GameDataHolderAccessor, int> shineNumByWorldGetHook =
    hk::hook::trampoline([](GameDataHolderAccessor accessor, int world_id) -> int {
        const int orig = shineNumByWorldGetHook.orig(accessor, world_id);
        auto& s = smoap::ap::ApState::instance();
        if (!s.bridge_connected.load(std::memory_order_relaxed)) {
            return 0;
        }
        const int credit = sumAllKingdomCredits();
        const int bit = (world_id >= 0 && world_id < 17) ? world_id : -1;

        static int s_call_count = 0;
        static int s_last_returned[17] = {};
        static int s_last_orig[17] = {};
        static bool s_inited = false;
        if (!s_inited) {
            for (int i = 0; i < 17; ++i) { s_last_returned[i] = -1; s_last_orig[i] = -1; }
            s_inited = true;
        }
        const bool first_calls = (s_call_count < 6);
        const bool valid_bit = (bit >= 0 && bit < 17);
        const bool ret_changed = valid_bit && (credit != s_last_returned[bit]);
        const bool orig_changed = valid_bit && (orig != s_last_orig[bit]);
        if (first_calls || ret_changed || orig_changed) {
            const char* kname = (bit >= 0 && bit < 17)
                ? smoap::game::kingdomForBit(static_cast<std::uint8_t>(bit))
                : "<oob>";
            SMOAP_LOG_INFO("[m6-hook] getGotShineNum: worldId=%d (our bit=%d, "
                           "name=%s) smo_natural=%d credit=%d (call#%d)",
                           world_id, bit, kname, orig, credit, s_call_count + 1);
        }
        ++s_call_count;
        if (valid_bit) { s_last_returned[bit] = credit; s_last_orig[bit] = orig; }
        return credit;
    });

}  // namespace

void installShineNumByWorldGetHook() {
    SMOAP_LOG_INFO("installing ShineNumByWorldGetHook -> "
                   "GameDataFunction::getGotShineNum");
    shineNumByWorldGetHook.installAtSym<
        "_ZN16GameDataFunction14getGotShineNumE22GameDataHolderAccessori">();
}

}  // namespace smoap::hooks
