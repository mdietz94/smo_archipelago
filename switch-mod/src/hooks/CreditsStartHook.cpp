// Inline patch at the BL inside StaffRollScene::init that kicks off the
// credits scene. Firing here means the player actually triggered the
// post-wedding credits roll — the only canonical "main game cleared" signal
// SMO emits.
//
// Why not Mushroom Kingdom arrival? A hidden Luncheon painting warps Mario
// to PeachWorld pre-game-clear, so any "first arrival in Mushroom" trigger
// false-positives on the portrait warp. StaffRollScene is the credits-only
// scene class (OdysseyDecomp confirms its registration in
// src/Scene/ProjectSceneFactory.cpp) and only initializes when the wedding
// cutscene chains into the credits — never on the portrait warp, never on
// Darker Side completion, never on save load.
//
// 1.0.0 offset (verified against main.nso by Kgamer77/
// SuperMarioOdysseyArchipelago Mod/patches/codehook.slpatch, MIT):
//   0x4C54A4 -> BL <initDrawSystemInfo>  (inside StaffRollScene::init)
//
// Kgamer77 *replaces* the BL target with their `onCreditsStart` wrapper, then
// calls initDrawSystemInfo manually from inside it. With exlaunch's
// HOOK_DEFINE_INLINE we just intercept BEFORE the BL fires, fire goal, and
// resume — the original BL runs unaffected.

#include "lib.hpp"  // HOOK_DEFINE_INLINE, exl::hook::InlineCtx

#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::hooks {

namespace {

inline constexpr ptrdiff_t kCreditsStartPatchOffset = 0x4C54A4;

HOOK_DEFINE_INLINE(CreditsStartHook) {
    static void Callback(exl::hook::InlineCtx* /*ctx*/) {
        auto& st = smoap::ap::ApState::instance();
        if (st.goal_sent) return;
        st.goal_sent = true;
        SMOAP_LOG_INFO("[credits] StaffRollScene::init reached — reporting goal");
        smoap::ap::reportGoal();
    }
};

}  // namespace

void installCreditsStartHook() {
    SMOAP_LOG_INFO("installing CreditsStartHook @ +0x%lx",
                   static_cast<unsigned long>(kCreditsStartPatchOffset));
    CreditsStartHook::InstallAtOffset(kCreditsStartPatchOffset);
}

}  // namespace smoap::hooks
