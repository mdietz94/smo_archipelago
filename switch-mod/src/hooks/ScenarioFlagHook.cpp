// Hook on GameDataFile::setMainScenarioNo(int).
//
// M3: empty trampoline. M4 reports scenario via reportStatus.

#include "lib.hpp"
#include "../ap/ApFrameBridge.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

class GameDataFile;

namespace smoap::hooks {

namespace {
HOOK_DEFINE_TRAMPOLINE(ScenarioFlagHook) {
    static void Callback(GameDataFile* self, int scenario_no) {
        Orig(self, scenario_no);
        // M4 fills in: smoap::ap::reportStatus("", scenario_no, -1);
    }
};
}  // namespace

void installScenarioFlagHook() {
    SMOAP_LOG_INFO("installing ScenarioFlagHook -> %s", smoap::sym::kGameDataFileSetMainScenarioNo);
    softInstallAtSymbol<ScenarioFlagHook>(smoap::sym::kGameDataFileSetMainScenarioNo);
}

}  // namespace smoap::hooks
