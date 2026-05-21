// CapMessage text-lookup trampolines + rs:: function-pointer wiring.

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include "../ui/CappyMessenger.hpp"
#include "../util/Log.hpp"

namespace al { class IUseMessageSystem; }

namespace smoap::ui {
using TryShowCapMessagePriorityLowFn =
    bool (*)(const void*, const char*, int, int);
using IsActiveCapMessageFn = bool (*)(const void*);
void setCappyMessengerRsCalls(TryShowCapMessagePriorityLowFn tryShow,
                              IsActiveCapMessageFn isActive);
}  // namespace smoap::ui

namespace smoap::hooks {

namespace {

bool s_logged_is_exist = false;
bool s_logged_get_str  = false;

HkTrampoline<bool, const al::IUseMessageSystem*, const char*, const char*>
    isExistLabelInSystemMessageHook = hk::hook::trampoline(
        [](const al::IUseMessageSystem* sys, const char* mstxt,
           const char* label) -> bool {
            const char16_t* sub =
                smoap::ui::CappyMessenger::instance().lookupSubstitution(label);
            if (sub) {
                if (!s_logged_is_exist) {
                    SMOAP_LOG_INFO("[cappy-hook] isExistLabelInSystemMessage "
                                   "mstxt='%s' label='%s' -> SYNTHESIZED true",
                                   mstxt ? mstxt : "<null>", label);
                    s_logged_is_exist = true;
                }
                return true;
            }
            return isExistLabelInSystemMessageHook.orig(sys, mstxt, label);
        });

HkTrampoline<const char16_t*, const al::IUseMessageSystem*, const char*, const char*>
    getSystemMessageStringHook = hk::hook::trampoline(
        [](const al::IUseMessageSystem* sys, const char* mstxt,
           const char* label) -> const char16_t* {
            const char16_t* sub =
                smoap::ui::CappyMessenger::instance().lookupSubstitution(label);
            if (sub) {
                if (!s_logged_get_str) {
                    SMOAP_LOG_INFO("[cappy-hook] getSystemMessageString "
                                   "mstxt='%s' label='%s' -> SUBSTITUTED ourBuf",
                                   mstxt ? mstxt : "<null>", label);
                    s_logged_get_str = true;
                }
                return sub;
            }
            return getSystemMessageStringHook.orig(sys, mstxt, label);
        });

HkTrampoline<bool, const al::IUseMessageSystem*, const char*, const char*>
    isExistLabelInStageMessageHook = hk::hook::trampoline(
        [](const al::IUseMessageSystem* sys, const char* mstxt,
           const char* label) -> bool {
            if (smoap::ui::CappyMessenger::instance().lookupSubstitution(label)) {
                return true;
            }
            return isExistLabelInStageMessageHook.orig(sys, mstxt, label);
        });

HkTrampoline<const char16_t*, const al::IUseMessageSystem*, const char*, const char*>
    getStageMessageStringHook = hk::hook::trampoline(
        [](const al::IUseMessageSystem* sys, const char* mstxt,
           const char* label) -> const char16_t* {
            const char16_t* sub =
                smoap::ui::CappyMessenger::instance().lookupSubstitution(label);
            if (sub) return sub;
            return getStageMessageStringHook.orig(sys, mstxt, label);
        });

}  // namespace

void installCappyMessageTextHooks() {
    SMOAP_LOG_INFO("installing CapMessage SYSTEM message-text hooks");
    isExistLabelInSystemMessageHook.installAtSym<
        "_ZN2al27isExistLabelInSystemMessageEPKNS_17IUseMessageSystemEPKcS4_">();
    getSystemMessageStringHook.installAtSym<
        "_ZN2al22getSystemMessageStringEPKNS_17IUseMessageSystemEPKcS4_">();

    SMOAP_LOG_INFO("installing CapMessage STAGE message-text hooks (defensive)");
    isExistLabelInStageMessageHook.installAtSym<
        "_ZN2al26isExistLabelInStageMessageEPKNS_17IUseMessageSystemEPKcS4_">();
    getStageMessageStringHook.installAtSym<
        "_ZN2al21getStageMessageStringEPKNS_17IUseMessageSystemEPKcS4_">();
}

void installCappyMessengerSymbols() {
    const ptr addr_tryShow = hk::ro::lookupSymbol(
        "_ZN2rs28tryShowCapMessagePriorityLowEPKN2al18IUseSceneObjHolderEPKcii");
    if (addr_tryShow == 0) {
        SMOAP_LOG_ERROR("[cappy] tryShowCapMessagePriorityLow LookupSymbol FAILED");
        return;
    }
    SMOAP_LOG_INFO("[cappy] tryShowCapMessagePriorityLow @ 0x%lx",
                   static_cast<unsigned long>(addr_tryShow));

    const ptr addr_isActive = hk::ro::lookupSymbol(
        "_ZN2rs18isActiveCapMessageEPKN2al18IUseSceneObjHolderE");
    if (addr_isActive == 0) {
        SMOAP_LOG_ERROR("[cappy] isActiveCapMessage LookupSymbol FAILED");
        return;
    }
    SMOAP_LOG_INFO("[cappy] isActiveCapMessage @ 0x%lx",
                   static_cast<unsigned long>(addr_isActive));

    smoap::ui::setCappyMessengerRsCalls(
        reinterpret_cast<smoap::ui::TryShowCapMessagePriorityLowFn>(addr_tryShow),
        reinterpret_cast<smoap::ui::IsActiveCapMessageFn>(addr_isActive));
    SMOAP_LOG_INFO("[cappy] rs:: function pointers wired into CappyMessenger");
}

}  // namespace smoap::hooks
