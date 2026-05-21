// Hook on GameDataFile::setMainScenarioNo(int).
//
// Reports scenario flag changes to the bridge via reportStatus.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "../ap/ApFrameBridge.hpp"
#include "../util/Log.hpp"

class GameDataFile;

namespace smoap::hooks {

namespace {

HkTrampoline<void, GameDataFile*, int> scenarioFlagHook =
    hk::hook::trampoline([](GameDataFile* self, int scenario_no) -> void {
        scenarioFlagHook.orig(self, scenario_no);
        SMOAP_LOG_INFO("ScenarioFlagHook: scenario_no=%d", scenario_no);
        smoap::ap::reportStatus(/*stage_name=*/nullptr, scenario_no);
    });

}  // namespace

void installScenarioFlagHook() {
    SMOAP_LOG_INFO("installing ScenarioFlagHook -> GameDataFile::setMainScenarioNo");
    scenarioFlagHook.installAtSym<"_ZN12GameDataFile17setMainScenarioNoEi">();
}

}  // namespace smoap::hooks
