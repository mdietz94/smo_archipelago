// See AddHackDictionaryHook.hpp for design rationale.

#include "lib.hpp"

#include "AddHackDictionaryHook.hpp"

#include "../ap/ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

// 1-pointer trivially-copyable wrapper, Itanium-ABI-passed in x0. Same
// shape as the mirror in CaptureGate.cpp and AddPayShineHook.cpp — not
// worth a shared header for one struct.
struct GameDataHolderWriter { void* mData; };

namespace smoap::hooks {

namespace {

HOOK_DEFINE_TRAMPOLINE(AddHackDictionaryHook) {
    static void Callback(GameDataHolderWriter w, const char* hack_name) {
        // Unidentifiable writes pass through — we can't sensibly gate
        // something we can't name, and dropping a NULL write would just
        // mask a different bug somewhere upstream.
        if (!hack_name || !*hack_name) {
            Orig(w, hack_name);
            return;
        }

        // SaveLoadHook flips this around `Orig(initializeData)` so the
        // save's recorded captures can rehydrate the dictionary without
        // the filter clobbering them. Bridge rehello will reconcile
        // captures_unlocked to AP state right afterward.
        if (smoap::ap::ApState::instance().save_load_passthrough.load(
                std::memory_order_acquire)) {
            SMOAP_LOG_INFO("[m7-dict] FIRE hack='%s' allowed=save-load",
                           hack_name);
            Orig(w, hack_name);
            return;
        }

        const bool blocked = smoap::game::captureBlocked(hack_name);
        SMOAP_LOG_INFO("[m7-dict] FIRE hack='%s' blocked=%d",
                       hack_name, blocked ? 1 : 0);
        if (blocked) return;  // Capture List parity: don't pollute.
        Orig(w, hack_name);
    }
};

}  // namespace

void installAddHackDictionaryHook() {
    SMOAP_LOG_INFO("installing AddHackDictionaryHook -> %s",
                   smoap::sym::kGameDataFunctionAddHackDictionary);
    softInstallAtSymbol<AddHackDictionaryHook>(
        smoap::sym::kGameDataFunctionAddHackDictionary);
}

}  // namespace smoap::hooks
