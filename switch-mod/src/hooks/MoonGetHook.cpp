// Hook on GameDataFile::setGotShine(const ShineInfo*).
//
// Reads (stageName, objectId, shineId) from the ShineInfo* via the layout
// mirror in game/ShineInfoLayout.hpp and ships the raw IDs to the bridge.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../game/ShineInfoLayout.hpp"
#include "../util/Log.hpp"

#include <cstdint>

class GameDataFile;
class ShineInfo;

namespace smoap::hooks {

namespace {

// Quick sanity check: do the first few bytes of a string pointer look like
// ASCII? If the offset is wrong we'll get random bytes or kernel addresses;
// using strlen / %s on those is fatal.
bool stringSane(const char* s) {
    if (!s) return false;
    auto p = reinterpret_cast<std::uintptr_t>(s);
    if (p < 0x10000) return false;
    for (int i = 0; i < 8; ++i) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c == 0) return i > 0;
        if (c < 0x20 || c > 0x7e) return false;
    }
    return true;
}

HkTrampoline<void, GameDataFile*, const ShineInfo*> moonGetHook =
    hk::hook::trampoline([](GameDataFile* self, const ShineInfo* info) -> void {
        moonGetHook.orig(self, info);
        if (!info) return;
        const char* stage = smoap::game::shine_info_layout::stageName(info);
        const char* obj   = smoap::game::shine_info_layout::objectId(info);
        const int   uid   = smoap::game::shine_info_layout::shineId(info);

        const bool stage_ok = stringSane(stage);
        const bool obj_ok   = stringSane(obj);
        if (stage_ok && obj_ok) {
            SMOAP_LOG_INFO("MoonGetHook: reporting stage=%s id=%s uid=%d",
                           stage, obj, uid);
            smoap::ap::reportMoonChecked(stage, obj, uid);
        } else {
            SMOAP_LOG_WARN("MoonGetHook: insane string ptrs stage_ok=%d obj_ok=%d — "
                           "offsets in ShineInfoLayout.hpp likely wrong; dropping",
                           stage_ok ? 1 : 0, obj_ok ? 1 : 0);
        }
    });

}  // namespace

void installMoonGetHook() {
    SMOAP_LOG_INFO("installing MoonGetHook -> GameDataFile::setGotShine");
    moonGetHook.installAtSym<"_ZN12GameDataFile11setGotShineEPK9ShineInfo">();
}

}  // namespace smoap::hooks
