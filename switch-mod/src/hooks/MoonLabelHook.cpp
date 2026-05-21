// M6 phase A.5 — Channel A: moon-get cutscene label substitution.
//
// Three trampolines, one per moon-cutscene state-machine entry point.

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <cstdint>

#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::hooks {

namespace {

using SetPaneStringFormatFn = void (*)(void* /*iuse_layout*/,
                                       const char* /*pane*/,
                                       const char* /*fmt*/, ...);
SetPaneStringFormatFn g_set_pane_string_format = nullptr;

constexpr const char* kPaneName = "TxtScenario";

void applyPendingLabel(void* self, std::size_t layout_offset) {
    if (g_set_pane_string_format == nullptr) return;
    if (self == nullptr) return;

    char buf[smoap::ap::kPendingMoonLabelCap];
    if (!smoap::ap::ApState::instance().tryTakePendingMoonLabel(buf)) {
        return;
    }
    if (buf[0] == '\0') return;

    auto* layout_actor = *reinterpret_cast<void* const*>(
        reinterpret_cast<const std::uint8_t*>(self) + layout_offset);
    if (layout_actor == nullptr) {
        SMOAP_LOG_WARN("[moon_label] no LayoutActor at self+0x%zx; dropping",
                       layout_offset);
        return;
    }
    void* iuse_layout = reinterpret_cast<void*>(
        reinterpret_cast<std::uint8_t*>(layout_actor) + 8);

    SMOAP_LOG_INFO("[moon_label] applying text='%s' on pane '%s' (layout=%p)",
                   buf, kPaneName, iuse_layout);
    g_set_pane_string_format(iuse_layout, kPaneName, "%s", buf);
}

HkTrampoline<void, void*> moonGetLabelRegularHook =
    hk::hook::trampoline([](void* self) -> void {
        moonGetLabelRegularHook.orig(self);
        applyPendingLabel(self, 0x20);
    });

HkTrampoline<void, void*> moonGetLabelMainHook =
    hk::hook::trampoline([](void* self) -> void {
        moonGetLabelMainHook.orig(self);
        applyPendingLabel(self, 0x40);
    });

HkTrampoline<void, void*> moonGetLabelGrandHook =
    hk::hook::trampoline([](void* self) -> void {
        moonGetLabelGrandHook.orig(self);
        applyPendingLabel(self, 0x40);
    });

}  // namespace

void installMoonLabelHook() {
    SMOAP_LOG_INFO("resolving M6-phase-A.5 cutscene label helper");
    const ptr addr = hk::ro::lookupSymbol(
        "_ZN2al19setPaneStringFormatEPNS_10IUseLayoutEPKcS3_z");
    if (addr == 0) {
        SMOAP_LOG_ERROR("[moon_label] LookupSymbol FAILED — Channel A disabled");
    } else {
        SMOAP_LOG_INFO("[moon_label] setPaneStringFormat @ 0x%lx",
                       static_cast<unsigned long>(addr));
        g_set_pane_string_format = reinterpret_cast<SetPaneStringFormatFn>(addr);
    }

    SMOAP_LOG_INFO("installing 3 M6-phase-A.5 MoonLabelHook trampolines");
    moonGetLabelRegularHook.installAtSym<
        "_ZN23StageSceneStateGetShine10exeDemoGetEv">();
    moonGetLabelMainHook.installAtSym<
        "_ZN27StageSceneStateGetShineMain15exeDemoGetStartEv">();
    moonGetLabelGrandHook.installAtSym<
        "_ZN28StageSceneStateGetShineGrand15exeDemoGetStartEv">();
}

}  // namespace smoap::hooks
