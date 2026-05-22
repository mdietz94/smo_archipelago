// Hook on PlayerHackKeeper::startHack(al::HitSensor*, al::HitSensor*, al::LiveActor*).
//
// After Orig, ship the current hack name to the bridge for the AP location
// check, and queue a deferred release if the player hasn't unlocked this
// capture via AP. The release fires from tickPendingUncapture() the moment
// PlayerHackKeeper::isActiveHackStartDemo() returns false — i.e. the
// capture-entry "dive in" cinematic has ended.

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <cstring>

#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"

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

// Gentler release for the 7 inanimate captures in kCapsUsingTryEscape below.
// No actor despawn, safe because those caps have no intro state machine to
// race against teardown. If resolution fails the affected caps fall back to
// forceKillHack (logged once at install time).
using TryEscapeHackFn = void (*)(PlayerHackKeeper*);
TryEscapeHackFn s_tryEscapeHack = nullptr;

// true while the capture-entry "dive in" cinematic is still playing.
// tickPendingUncapture polls per frame and fires the release the moment it
// returns false. Replaces the prior fixed-delay timer table. If resolution
// fails the deny path is disabled (the queued capture stays queued forever)
// — see install log. Failing closed beats firing forceKillHack mid-cinematic
// (the failure mode that pushed earlier versions to the fixed-delay design).
using IsActiveHackStartDemoFn = bool (*)(const PlayerHackKeeper*);
IsActiveHackStartDemoFn s_isActiveHackStartDemo = nullptr;

// Inanimate captures that get the gentler tryEscapeHack release. These are
// stationary props with no intro state machine to race against teardown, so
// the actor-despawn cost of forceKillHack is pure visual noise on them.
// Source: KGamer77/SuperMarioOdysseyArchipelago Mod/source/main.cpp:75
// (the `nonKillCaptures` indices, demangled against their captureListNames).
//
// Cactus, BazookaElectric (Mini Rocket), Tree, RockForest (Boulder),
// Guidepost (Pole), Manhole, HackFork (Volbonan).
constexpr const char* kCapsUsingTryEscape[] = {
    "Cactus",
    "BazookaElectric",
    "Tree",
    "RockForest",
    "Guidepost",
    "Manhole",
    "HackFork",
};

bool capUsesTryEscape(const char* hack_name) {
    if (!hack_name || !*hack_name) return false;
    const std::size_t n = std::strlen(hack_name);
    for (const char* entry : kCapsUsingTryEscape) {
        const std::size_t en = std::strlen(entry);
        if (en == n && std::memcmp(hack_name, entry, n) == 0) return true;
    }
    return false;
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

            // Capturesanity: only credit the check when the player owns the
            // unlock. A blocked capture is yanked back to Mario as soon as
            // the capture-entry cinematic ends (release queued below, drained
            // by tickPendingUncapture) — sending the check before then would
            // credit a "capture" the player never actually got to keep. When
            // capturesanity is OFF, the bridge pushes synthetic unlocks for
            // every cap at HELLO time, so `blocked` is false and behavior
            // matches the pre-gate path. AP location checks are idempotent,
            // so re-touching the same cap after AP grant still ships fine.
            if (!blocked) {
                smoap::ap::reportCaptureChecked(name);
            }

            // M7: deny captures the player hasn't unlocked via AP. The actual
            // release call is deferred to tickPendingUncapture() running from
            // drawMain — both because firing inline doesn't release Mario
            // (state machine isn't fully entered yet) and because the wait
            // lasts as long as the capture-entry cinematic plays, which is
            // a funnier UX ("captured the enemy and got yanked back out" beat).
            //
            // Gate: tickPendingUncapture polls isActiveHackStartDemo and fires
            // the moment it returns false. No fixed wall-clock delay — the
            // prior per-cap timer table was a proxy for "is the cinematic
            // over yet?" and the actual signal is strictly better information.
            if (blocked) {
                if (s_forceKillHack && s_isActiveHackStartDemo) {
                    auto& st = smoap::ap::ApState::instance();
                    // Phase 1.5a: stash the cap name we're queuing for so
                    // tickPendingUncapture can verify the keeper still holds
                    // the same capture at release time (vs. SMO having
                    // released it for any reason — Y-press, env death, scene
                    // change, etc.).
                    std::size_t i = 0;
                    for (; i < sizeof(st.pending_kill_hack_name) - 1 && name[i]; ++i) {
                        st.pending_kill_hack_name[i] = name[i];
                    }
                    st.pending_kill_hack_name[i] = '\0';
                    st.pending_kill_keeper.store(self, std::memory_order_release);
                    const bool tryEscape = capUsesTryEscape(name)
                        && s_tryEscapeHack != nullptr;
                    SMOAP_LOG_INFO(
                        "CaptureStartHook: BLOCKED hack=%s — check suppressed; "
                        "%s queued until capture-entry demo ends",
                        name, tryEscape ? "tryEscapeHack" : "forceKillHack");
                } else {
                    SMOAP_LOG_ERROR(
                        "CaptureStartHook: hack=%s blocked but deny path disabled "
                        "(forceKillHack=%p isActiveHackStartDemo=%p) — capture "
                        "goes through ungated",
                        name, (void*)s_forceKillHack,
                        (void*)s_isActiveHackStartDemo);
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

    const ptr fkh_addr = hk::ro::lookupSymbol(smoap::sym::kPlayerHackKeeperForceKillHack);
    if (fkh_addr == 0) {
        SMOAP_LOG_ERROR("forceKillHack lookup FAILED — M7 deny path disabled");
    } else {
        s_forceKillHack = reinterpret_cast<ForceKillHackFn>(fkh_addr);
        SMOAP_LOG_INFO("forceKillHack resolved @ 0x%lx",
                       static_cast<unsigned long>(fkh_addr));
    }

    // M7: resolve tryEscapeHack. Failure is non-fatal — kCapsUsingTryEscape
    // captures fall back to forceKillHack (same end state, modulo the
    // captured-actor despawn visual). Logged once at install time so the
    // fallback is visible.
    const ptr teh_addr = hk::ro::lookupSymbol(smoap::sym::kPlayerHackKeeperTryEscapeHack);
    if (teh_addr == 0) {
        SMOAP_LOG_WARN("tryEscapeHack lookup FAILED — inanimate caps fall "
                       "back to forceKillHack");
    } else {
        s_tryEscapeHack = reinterpret_cast<TryEscapeHackFn>(teh_addr);
        SMOAP_LOG_INFO("tryEscapeHack resolved @ 0x%lx",
                       static_cast<unsigned long>(teh_addr));
    }

    // M7: resolve isActiveHackStartDemo — required for the deny-path gate.
    // If resolution fails the deny path is disabled (CaptureStartHook logs
    // and lets the capture through ungated). Failing closed beats firing
    // forceKillHack mid-cinematic, which crashes T-Rex (the failure mode
    // that pushed prior versions to the fixed-delay design).
    const ptr iah_addr = hk::ro::lookupSymbol(smoap::sym::kPlayerHackKeeperIsActiveHackStartDemo);
    if (iah_addr == 0) {
        SMOAP_LOG_ERROR("isActiveHackStartDemo lookup FAILED — M7 deny path "
                        "disabled (captures ungated)");
    } else {
        s_isActiveHackStartDemo = reinterpret_cast<IsActiveHackStartDemoFn>(iah_addr);
        SMOAP_LOG_INFO("isActiveHackStartDemo resolved @ 0x%lx",
                       static_cast<unsigned long>(iah_addr));
    }
}

// Called once per frame from DrawMainHook::Callback. Polls the queued
// keeper's isActiveHackStartDemo flag and fires the release the moment the
// capture-entry "dive in" cinematic ends — no fixed delay, the demo flag is
// the actual signal we used to approximate with per-cap timer entries.
//
// Phase 1.5b re-verify guard: SMO may have already released the capture
// during the wait window (player Y-press, env death, scene change, save
// load). Re-read getCurrentHackName(keeper) and skip if it no longer matches
// the cap we queued for. See pending_kill_hack_name in ApState.hpp.
//
// Release path branch: tryEscapeHack for the 7 inanimate captures in
// kCapsUsingTryEscape (no actor despawn, safe because they have no intro
// state machine to race), forceKillHack for everything else (synchronous
// teardown that prevents the captured actor from continuing its intro and
// crashing on the cleared keeper — required for T-Rex; see HookSymbols.hpp).
void tickPendingUncapture() {
    if (!s_forceKillHack || !s_isActiveHackStartDemo) return;
    auto& st = smoap::ap::ApState::instance();
    void* keeper = st.pending_kill_keeper.load(std::memory_order_acquire);
    if (!keeper) return;
    // Gate: wait for the dive-in cinematic to end. Polling per frame matches
    // KGamer77's Mod/source/main.cpp:73 gate (modulo their 3-frame poll
    // throttle); firing while the demo is still active is the no-op /
    // crash-prone window the prior fixed-delay design was working around.
    if (s_isActiveHackStartDemo(
            static_cast<const PlayerHackKeeper*>(keeper))) {
        return;
    }
    // Phase 1.5b: PRIMARY GUARD against stale-keeper kills. The keeper
    // pointer is stable per-stage (singleton on PlayerActorHakoniwa) but its
    // *content* mutates whenever the player captures something else. If the
    // player captured A (blocked), grabbed an item Y-press release, then
    // captured B (also blocked), we'd see the same keeper pointer but
    // already re-queued a fresh deferred kill for it — letting this stale
    // entry fire would double-kill.
    bool name_ok = false;
    bool use_try_escape = false;
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
            if (match) {
                use_try_escape = s_tryEscapeHack != nullptr
                    && capUsesTryEscape(cur);
            } else {
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
        // Without getCurrentHackName we can't verify, so fall through to
        // historical behavior (fire blind, forceKillHack only). Should never
        // happen in practice since the hook install logs the lookup result.
        name_ok = true;
        SMOAP_LOG_WARN(
            "M7 pending kill firing blind (getCurrentHackName unresolved)");
    }
    // Clear FIRST so we don't double-fire if the release itself takes more
    // than one frame to settle and tickPendingUncapture runs again before
    // the keeper state machine catches up. Also clears the pending-name slot
    // so the next BLOCKED queue starts from a clean state.
    st.pending_kill_keeper.store(nullptr, std::memory_order_release);
    st.pending_kill_hack_name[0] = '\0';
    if (!name_ok) return;
    st.synthetic_uncapture_this_frame = true;
    if (use_try_escape) {
        SMOAP_LOG_INFO("M7 deferred tryEscapeHack firing on keeper=%p", keeper);
        s_tryEscapeHack(static_cast<PlayerHackKeeper*>(keeper));
    } else {
        SMOAP_LOG_INFO("M7 deferred forceKillHack firing on keeper=%p", keeper);
        s_forceKillHack(static_cast<PlayerHackKeeper*>(keeper));
    }
    smoap::game::playSE_NG();
}

}  // namespace smoap::hooks
