// Hook on PlayerHackKeeper::startHack(al::HitSensor*, al::HitSensor*, al::LiveActor*).
//
// After Orig, ship the current hack name to the bridge for the AP location
// check, and queue a deferred forceKillHack if the player hasn't unlocked
// this capture via AP.

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <cstring>

#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../util/Log.hpp"

class PlayerHackKeeper;
namespace al { class HitSensor; class LiveActor; }

namespace smoap::hooks {

namespace {

constexpr const char* kGetCurrentHackNameSym =
    "_ZNK16PlayerHackKeeper18getCurrentHackNameEv";

using GetCurrentHackNameFn = const char* (*)(const PlayerHackKeeper*);
GetCurrentHackNameFn s_getCurrentHackName = nullptr;

using ForceKillHackFn = void (*)(PlayerHackKeeper*);
ForceKillHackFn s_forceKillHack = nullptr;

constexpr int kDeferredKillMs = 4000;

struct CapKillDelayOverride {
    const char* hack_name;
    int delay_ms;
};
constexpr CapKillDelayOverride kCapKillDelayOverrides[] = {
    {"TRex", 6000},
    {"Killer", 2000},
    {"Fastener", 2000},
};

int deferredKillMsForCap(const char* hack_name) {
    if (!hack_name || !*hack_name) return kDeferredKillMs;
    const std::size_t n = std::strlen(hack_name);
    for (const auto& e : kCapKillDelayOverrides) {
        const std::size_t en = std::strlen(e.hack_name);
        if (en == n && std::memcmp(hack_name, e.hack_name, n) == 0) {
            return e.delay_ms;
        }
    }
    return kDeferredKillMs;
}

HkTrampoline<void, PlayerHackKeeper*, al::HitSensor*, al::HitSensor*, al::LiveActor*>
    captureStartHook = hk::hook::trampoline(
        [](PlayerHackKeeper* self, al::HitSensor* a, al::HitSensor* b,
           al::LiveActor* target) -> void {
            captureStartHook.orig(self, a, b, target);
            if (!s_getCurrentHackName || !self) return;
            const char* name = s_getCurrentHackName(self);
            if (!name || !*name) return;

            SMOAP_LOG_INFO("CaptureStartHook: hack_name=%s", name);

            const bool blocked = smoap::game::captureBlocked(name);

            if (!blocked) {
                smoap::ap::reportCaptureChecked(name);
            }

            if (blocked) {
                if (s_forceKillHack) {
                    auto& st = smoap::ap::ApState::instance();
                    std::size_t i = 0;
                    for (; i < sizeof(st.pending_kill_hack_name) - 1 && name[i]; ++i) {
                        st.pending_kill_hack_name[i] = name[i];
                    }
                    st.pending_kill_hack_name[i] = '\0';
                    const int delay_ms = deferredKillMsForCap(name);
                    st.pending_kill_keeper.store(self, std::memory_order_release);
                    st.pending_kill_at_ms.store(
                        smoap::ap::ApState::nowMs() + delay_ms,
                        std::memory_order_release);
                    SMOAP_LOG_INFO(
                        "CaptureStartHook: BLOCKED hack=%s — check suppressed; "
                        "forceKillHack queued in %dms%s",
                        name, delay_ms,
                        (delay_ms != kDeferredKillMs) ? " (per-cap override)" : "");
                } else {
                    SMOAP_LOG_ERROR(
                        "CaptureStartHook: hack=%s blocked but forceKillHack unresolved",
                        name);
                }
            }
        });

}  // namespace

void installCaptureStartHook() {
    SMOAP_LOG_INFO("installing CaptureStartHook -> PlayerHackKeeper::startHack");
    captureStartHook.installAtSym<
        "_ZN16PlayerHackKeeper9startHackEPN2al9HitSensorES2_PNS0_9LiveActorE">();

    const ptr addr = hk::ro::lookupSymbol(kGetCurrentHackNameSym);
    if (addr == 0) {
        SMOAP_LOG_ERROR("getCurrentHackName lookup FAILED");
    } else {
        s_getCurrentHackName = reinterpret_cast<GetCurrentHackNameFn>(addr);
        SMOAP_LOG_INFO("getCurrentHackName resolved @ 0x%lx",
                       static_cast<unsigned long>(addr));
    }

    const ptr fkh_addr = hk::ro::lookupSymbol("_ZN16PlayerHackKeeper13forceKillHackEv");
    if (fkh_addr == 0) {
        SMOAP_LOG_ERROR("forceKillHack lookup FAILED — M7 deny path disabled");
    } else {
        s_forceKillHack = reinterpret_cast<ForceKillHackFn>(fkh_addr);
        SMOAP_LOG_INFO("forceKillHack resolved @ 0x%lx",
                       static_cast<unsigned long>(fkh_addr));
    }
}

void tickPendingUncapture() {
    if (!s_forceKillHack) return;
    auto& st = smoap::ap::ApState::instance();
    void* keeper = st.pending_kill_keeper.load(std::memory_order_acquire);
    if (!keeper) return;
    if (smoap::ap::ApState::nowMs() <
            st.pending_kill_at_ms.load(std::memory_order_acquire)) {
        return;
    }
    bool name_ok = false;
    if (s_getCurrentHackName) {
        const char* cur = s_getCurrentHackName(
            static_cast<const PlayerHackKeeper*>(keeper));
        if (cur && *cur) {
            bool match = true;
            for (std::size_t i = 0; i < sizeof(st.pending_kill_hack_name); ++i) {
                const char want = st.pending_kill_hack_name[i];
                const char got = cur[i];
                if (want != got) { match = false; break; }
                if (want == '\0') break;
            }
            name_ok = match;
            if (!match) {
                SMOAP_LOG_INFO(
                    "M7 pending kill SKIPPED keeper=%p — cap changed: "
                    "queued='%s' now='%s'",
                    keeper, st.pending_kill_hack_name, cur);
            }
        } else {
            SMOAP_LOG_INFO(
                "M7 pending kill SKIPPED keeper=%p — no active cap "
                "(queued='%s'; player or env released first)",
                keeper, st.pending_kill_hack_name);
        }
    } else {
        name_ok = true;
        SMOAP_LOG_WARN(
            "M7 pending kill firing blind (getCurrentHackName unresolved)");
    }
    st.pending_kill_keeper.store(nullptr, std::memory_order_release);
    st.pending_kill_hack_name[0] = '\0';
    if (!name_ok) return;
    st.synthetic_uncapture_this_frame = true;
    SMOAP_LOG_INFO("M7 deferred forceKillHack firing on keeper=%p", keeper);
    s_forceKillHack(static_cast<PlayerHackKeeper*>(keeper));
    smoap::game::playSE_NG();
}

}  // namespace smoap::hooks
