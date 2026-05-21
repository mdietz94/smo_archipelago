// Trampoline on GameDataFunction::addHackDictionary. Filters writes for
// captures the player hasn't unlocked via AP so the in-game Capture List
// stays in sync with AP state. See AddHackDictionaryHook.hpp for full design.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "AddHackDictionaryHook.hpp"

#include "../ap/ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../util/Log.hpp"

struct GameDataHolderWriter { void* mData; };

namespace smoap::hooks {

namespace {

HkTrampoline<void, GameDataHolderWriter, const char*> addHackDictionaryHook =
    hk::hook::trampoline([](GameDataHolderWriter w, const char* hack_name) -> void {
        if (!hack_name || !*hack_name) {
            addHackDictionaryHook.orig(w, hack_name);
            return;
        }
        if (smoap::ap::ApState::instance().save_load_passthrough.load(
                std::memory_order_acquire)) {
            SMOAP_LOG_INFO("[m7-dict] FIRE hack='%s' allowed=save-load", hack_name);
            addHackDictionaryHook.orig(w, hack_name);
            return;
        }
        const bool blocked = smoap::game::captureBlocked(hack_name);
        SMOAP_LOG_INFO("[m7-dict] FIRE hack='%s' blocked=%d",
                       hack_name, blocked ? 1 : 0);
        if (blocked) return;
        addHackDictionaryHook.orig(w, hack_name);
    });

}  // namespace

void installAddHackDictionaryHook() {
    SMOAP_LOG_INFO("installing AddHackDictionaryHook -> "
                   "GameDataFunction::addHackDictionary");
    addHackDictionaryHook.installAtSym<
        "_ZN16GameDataFunction17addHackDictionaryE20GameDataHolderWriterPKc">();
}

}  // namespace smoap::hooks
