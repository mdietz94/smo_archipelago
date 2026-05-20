// Hook on GameDataFunction::getCurrentShineNum(GameDataHolderAccessor).
//
// SMO calls this to render the global moon counter (HUD top-left "x/N") and
// to gate Odyssey-fueling. We trampoline through orig() but DELIBERATELY
// DROP orig and return only our AP credit for the CURRENT kingdom.
//
// M6 phase D — per-kingdom HUD (formerly sum-across-all-kingdoms): an
// out-of-current-kingdom credit (e.g. Wooded moon while Mario is in Cap)
// does not contribute to Cap's HUD. This mirrors vanilla post-clear
// per-kingdom-summed behavior and makes the spendable-here total
// unambiguous.
//
// M6 phase D — freeze on bridge-offline: returns 0 so the Odyssey UI refuses
// fuel. Combined with AddPayShineHook blocking on the same flag, vanilla
// PayShine can't drift from our AP credit across a disconnect.
//
// orig is still logged so we can diagnose mismatches between SMO's natural
// counter and the AP credit total. The visual UX of "natural counter shows
// 0 after a local pickup" is intentional for M6; a dedicated AP HUD
// overlay lands in M8.

#include "lib.hpp"  // HOOK_DEFINE_TRAMPOLINE
#include "../ap/ApState.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

// Minimal layout mirror — avoids pulling in lunakit-vendor's full
// GameDataHolderAccessor.h. Itanium ABI passes a single-pointer trivially-
// copyable class in x0, so this is calling-convention-compatible.
struct GameDataHolderAccessor {
    void* mData;
};

namespace smoap::hooks {

namespace {

using GetCurrentWorldIdNoDevelopFn = int (*)(GameDataHolderAccessor);

// Returns the kingdom bit Mario is currently in, or 0xff when not resolvable.
// Mirror of AddPayShineHook's helper — both consult ApState's cached game
// data holder + resolved function pointer.
std::uint8_t resolveCurrentKingdomBit() {
    auto& s = smoap::ap::ApState::instance();
    void* holder = s.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!holder || !s.get_current_world_id_fn) return 0xff;
    auto fn = reinterpret_cast<GetCurrentWorldIdNoDevelopFn>(s.get_current_world_id_fn);
    GameDataHolderAccessor acc{holder};
    return smoap::game::kingdomBitForWorldId(fn(acc));
}

HOOK_DEFINE_TRAMPOLINE(ShineNumGetHook) {
    static int Callback(GameDataHolderAccessor accessor) {
        const int orig = Orig(accessor);  // called for diagnostics + side effects
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

        // Throttle: log first few calls (proves the hook is firing at all)
        // and any time the returned value OR the orig changes.
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
                           "ap_kingdom=%s(bit=%u) ap=%d returned (call#%d%s%s%s%s)",
                           orig, kname, bit, ap_value,
                           s_call_count + 1,
                           !online ? " offline" : "",
                           ret_changed && !first_calls ? " ap-changed" : "",
                           orig_changed && !first_calls ? " natural-changed" : "",
                           bit_changed && !first_calls ? " kingdom-changed" : "");
        }
        ++s_call_count;
        s_last_returned = ap_value;
        s_last_orig = orig;
        s_last_bit = bit;
        return ap_value;
    }
};

}  // namespace

void installShineNumGetHook() {
    SMOAP_LOG_INFO("installing ShineNumGetHook -> %s",
                   smoap::sym::kGameDataFunctionGetCurrentShineNum);
    softInstallAtSymbol<ShineNumGetHook>(
        smoap::sym::kGameDataFunctionGetCurrentShineNum);
}

}  // namespace smoap::hooks
