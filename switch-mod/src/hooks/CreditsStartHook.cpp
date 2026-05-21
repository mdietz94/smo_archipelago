// Strategy A: inline-replace the BL inside StaffRollScene::init at
// +0x4C54A4 (the call to al::Scene::initDrawSystemInfo). Verified by
// Kgamer77/SMOO-Plus-Hakkun, the only other public Hakkun-based SMO
// Archipelago project — same offset, same pattern, same Hakkun primitive.
//
// Why this beats Strategy B (trampoline on StaffRollScene::init's entry):
//   - The credits scene's class registration sets up plenty of other init
//     paths; trampolining the function entry would fire on EVERY init,
//     including any future internal callers we don't want to catch.
//   - 0x4C54A4 sits inside the function body, after the early-return paths
//     for failed scenario state. By the time control reaches this BL, we
//     KNOW the staff-roll cutscene is committed.
//
// hk::hook::writeBranchLinkAtMainOffset overwrites a single BL at the
// given offset with a BL to our callback. The callback must reproduce
// whatever the original BL was doing — here, calling Scene::
// initDrawSystemInfo(info) — to avoid breaking SMO's init sequence.
// Production exlaunch's HOOK_DEFINE_INLINE at the same offset intercepts
// BEFORE the BL and lets the original run; we replace and re-call instead.
// Both result in equivalent observable behavior.

#include "hk/hook/InstrUtil.h"
#include "hk/types.h"

#include "al/Library/Scene/Scene.h"
#include "Project/Scene/SceneInitInfo.h"

#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::hooks {

namespace {

inline constexpr ptrdiff_t kCreditsStartPatchOffset = 0x4C54A4;

// Replaces the BL at +0x4C54A4. The original BL inside
// StaffRollScene::init calls al::Scene::initDrawSystemInfo(info); we
// intercept here, fire the AP goal once, then call through to
// initDrawSystemInfo so the credits scene initializes normally.
//
// SceneInitInfo is 584 bytes (sizeof check in OdysseyHeaders); AArch64
// AAPCS passes it by reference regardless of `&` vs by-value declaration,
// so this signature is ABI-equivalent to Kgamer77's
// `void onCreditsStart(al::Scene*, const al::SceneInitInfo)` form.
void onCreditsStart(al::Scene* thisPtr, const al::SceneInitInfo& info) {
    auto& st = smoap::ap::ApState::instance();
    if (!st.goal_sent) {
        st.goal_sent = true;
        SMOAP_LOG_INFO("[credits] StaffRollScene::init reached BL @+0x%lx — "
                       "reporting goal",
                       static_cast<unsigned long>(kCreditsStartPatchOffset));
        smoap::ap::reportGoal();
    }
    thisPtr->initDrawSystemInfo(info);
}

}  // namespace

void installCreditsStartHook() {
    SMOAP_LOG_INFO("installing CreditsStartHook @ +0x%lx (Strategy A)",
                   static_cast<unsigned long>(kCreditsStartPatchOffset));
    hk::hook::writeBranchLinkAtMainOffset(kCreditsStartPatchOffset,
                                           &onCreditsStart);
}

}  // namespace smoap::hooks
