// Hook on GameDataFile::setGotShine(const ShineInfo*).
//
// M3 status: empty trampoline. Installation verifies the mangled symbol
// resolves. Real callback body (extract kingdom + shine_id from ShineInfo*
// and call ApFrameBridge::reportMoonChecked) lands in M4.

#include "lib.hpp"  // HOOK_DEFINE_TRAMPOLINE
#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../game/MoonApply.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

class GameDataFile;
class ShineInfo;

namespace smoap::hooks {

namespace {
HOOK_DEFINE_TRAMPOLINE(MoonGetHook) {
    static void Callback(GameDataFile* self, const ShineInfo* info) {
        Orig(self, info);
        // M4 fills in: extract (kingdom, shine_id) from info and report.
    }
};
}  // namespace

void installMoonGetHook() {
    SMOAP_LOG_INFO("installing MoonGetHook -> %s", smoap::sym::kGameDataFileSetGotShine);
    softInstallAtSymbol<MoonGetHook>(smoap::sym::kGameDataFileSetGotShine);
}

}  // namespace smoap::hooks
