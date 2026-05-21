// Hook on PlayerHackKeeper::startHack(al::HitSensor*, al::HitSensor*, al::LiveActor*).
//
// After Orig, the hack actor is bound and `self->getCurrentHackName()`
// returns the canonical hack name (e.g. "Goomba", "Kuribo", "Frog"). We
// forward the raw name to the bridge, which resolves it against
// capture_map.json into the apworld-canonical cap name.
//
// We resolve PlayerHackKeeper::getCurrentHackName via nn::ro::LookupSymbol
// at install time (storing the fn pointer) so we never depend on the
// link-time presence of SMO's internal symbols. M7 flips this hook into
// REPLACE-mode for cap gating.

#include "lib.hpp"
#include "lib/nx/nx.h"
#include "nn/ro.h"
#include <cstring>
#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

class PlayerHackKeeper;
namespace al { class HitSensor; class LiveActor; }

namespace smoap::hooks {

namespace {

// `const char* PlayerHackKeeper::getCurrentHackName() const`
// Mangled: _ZNK16PlayerHackKeeper18getCurrentHackNameEv
constexpr const char* kGetCurrentHackNameSym =
    "_ZNK16PlayerHackKeeper18getCurrentHackNameEv";

using GetCurrentHackNameFn = const char* (*)(const PlayerHackKeeper*);
GetCurrentHackNameFn s_getCurrentHackName = nullptr;

// `void PlayerHackKeeper::forceKillHack()` — M7 deny path (default).
// (cancelHack was tried first; logged BLOCKED + ran clean but did not actually
//  release Mario when called from inside the startHack callback. See
//  HookSymbols.hpp comment for forceKillHack rationale.)
//
// Considered alternative: PlayerHackKeeper::endHack (SMO's canonical
// voluntary-release path used for Y-press). Prototyped 2026-05-17; T-Rex
// CRASHED ~530ms after endHack returned cleanly when its exeHackStart state
// machine null-deref'd on the cleared keeper. forceKillHack does additional
// synchronous teardown that prevents the actor from continuing its intro
// state machine after release, so the actor can't race. Visual cost of
// forceKillHack: captured enemy despawns on release.
using ForceKillHackFn = void (*)(PlayerHackKeeper*);
ForceKillHackFn s_forceKillHack = nullptr;

// `void PlayerHackKeeper::tryEscapeHack()` — gentler M7 deny path for
// inanimate captures. Doesn't despawn the captured actor; just releases
// Mario. Safe for kCapsUsingTryEscape below because those captures have no
// intro state machine that could race against the release. KGamer77's
// SuperMarioOdysseyArchipelago uses the same split (Mod/source/main.cpp:75)
// for the same 7 caps. If resolution fails, the deny path falls through to
// forceKillHack (logged once at install time).
using TryEscapeHackFn = void (*)(PlayerHackKeeper*);
TryEscapeHackFn s_tryEscapeHack = nullptr;

// `bool PlayerHackKeeper::isActiveHackStartDemo() const` — true while the
// capture-entry "dive in" cinematic is still playing. tickPendingUncapture
// polls this per frame and fires the release the moment it returns false.
// Replaces the prior fixed-delay table. If resolution fails the deny path
// is disabled (the queued capture stays queued forever) — see install log.
// Failing closed beats firing forceKillHack mid-cinematic (the failure mode
// that pushed us to the fixed-delay design in the first place).
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

HOOK_DEFINE_TRAMPOLINE(CaptureStartHook) {
    static void Callback(PlayerHackKeeper* self,
                         al::HitSensor* a, al::HitSensor* b, al::LiveActor* target) {
        Orig(self, a, b, target);
        if (!s_getCurrentHackName || !self) return;
        const char* name = s_getCurrentHackName(self);
        if (!name || !*name) return;

        SMOAP_LOG_INFO("CaptureStartHook: hack_name=%s", name);

        // Decide blocked-vs-allowed once: same answer drives BOTH whether we
        // credit the AP check AND whether we queue the M7 forceKillHack.
        // captureBlocked returns false for unknown caps (fail-open via
        // captureBitFor==0xff), so non-tracked captures continue to behave
        // as before.
        const bool blocked = smoap::game::captureBlocked(name);

        // Capturesanity: only credit the check when the player owns the
        // unlock. A blocked capture is yanked back to Mario as soon as the
        // capture-entry cinematic ends (release queued below, drained by
        // tickPendingUncapture) — sending the check before then would credit
        // a "capture" the player never actually got to keep. When
        // capturesanity is OFF, the bridge pushes synthetic unlocks for
        // every cap at HELLO time, so `blocked` is false and behavior
        // matches the pre-gate path. AP location checks are idempotent, so
        // re-touching after the unlock arrives still credits cleanly.
        if (!blocked) {
            smoap::ap::reportCaptureChecked(name);
        }

        // M7: deny captures the player hasn't unlocked via AP. The actual
        // release call is deferred to tickPendingUncapture() running from
        // drawMain — both because firing inline doesn't release Mario (state
        // machine isn't fully entered yet) and because the wait lasts as
        // long as the capture-entry cinematic plays, which is a funnier UX
        // ("captured the enemy and got yanked back out" beat).
        //
        // Gate: tickPendingUncapture polls isActiveHackStartDemo and fires
        // the moment it returns false. No fixed wall-clock delay — the prior
        // per-cap timer table was a proxy for "is the cinematic over yet?"
        // and the actual signal is strictly better information.
        if (blocked) {
            if (s_forceKillHack && s_isActiveHackStartDemo) {
                auto& st = smoap::ap::ApState::instance();
                // Phase 1.5a: stash the cap name we're queuing for so
                // tickPendingUncapture can verify the keeper still holds the
                // same capture at release time (vs. SMO having released it
                // for any reason — Y-press, env death, scene change, etc.).
                std::size_t i = 0;
                for (; i < sizeof(st.pending_kill_hack_name) - 1
                        && name[i]; ++i) {
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
    }
};
}  // namespace

void installCaptureStartHook() {
    SMOAP_LOG_INFO("installing CaptureStartHook -> %s", smoap::sym::kPlayerHackKeeperStartHack);
    softInstallAtSymbol<CaptureStartHook>(smoap::sym::kPlayerHackKeeperStartHack);

    // Resolve getCurrentHackName once. If lookup fails we log it; the hook
    // still installs (Orig runs as normal) and we just won't report captures.
    uintptr_t addr = 0;
    const Result rc = nn::ro::LookupSymbol(&addr, kGetCurrentHackNameSym);
    if (R_FAILED(rc)) {
        SMOAP_LOG_ERROR("getCurrentHackName lookup FAILED rc=0x%x", rc);
    } else {
        s_getCurrentHackName = reinterpret_cast<GetCurrentHackNameFn>(addr);
        SMOAP_LOG_INFO("getCurrentHackName resolved @ 0x%lx", addr);
    }

    // M7: resolve forceKillHack. If this fails we fall through to a logged
    // warning on the deny path (capture goes ungated) rather than crashing.
    uintptr_t fkh_addr = 0;
    const Result rc2 = nn::ro::LookupSymbol(&fkh_addr, smoap::sym::kPlayerHackKeeperForceKillHack);
    if (R_FAILED(rc2)) {
        SMOAP_LOG_ERROR("forceKillHack lookup FAILED rc=0x%x — M7 deny path disabled", rc2);
    } else {
        s_forceKillHack = reinterpret_cast<ForceKillHackFn>(fkh_addr);
        SMOAP_LOG_INFO("forceKillHack resolved @ 0x%lx", fkh_addr);
    }

    // M7: resolve tryEscapeHack. Failure is non-fatal — kCapsUsingTryEscape
    // captures fall back to forceKillHack (same end state, modulo the
    // captured-actor despawn visual). Logged once at install time so the
    // fallback is visible.
    uintptr_t teh_addr = 0;
    const Result rc3 = nn::ro::LookupSymbol(&teh_addr, smoap::sym::kPlayerHackKeeperTryEscapeHack);
    if (R_FAILED(rc3)) {
        SMOAP_LOG_WARN("tryEscapeHack lookup FAILED rc=0x%x — inanimate caps "
                       "fall back to forceKillHack", rc3);
    } else {
        s_tryEscapeHack = reinterpret_cast<TryEscapeHackFn>(teh_addr);
        SMOAP_LOG_INFO("tryEscapeHack resolved @ 0x%lx", teh_addr);
    }

    // M7: resolve isActiveHackStartDemo — required for the deny-path gate.
    // If resolution fails the deny path is disabled (CaptureStartHook logs
    // and lets the capture through ungated). Failing closed beats firing
    // forceKillHack mid-cinematic, which crashes T-Rex (the failure mode
    // that pushed prior versions to the fixed-delay design).
    uintptr_t iah_addr = 0;
    const Result rc4 = nn::ro::LookupSymbol(&iah_addr, smoap::sym::kPlayerHackKeeperIsActiveHackStartDemo);
    if (R_FAILED(rc4)) {
        SMOAP_LOG_ERROR("isActiveHackStartDemo lookup FAILED rc=0x%x — M7 "
                        "deny path disabled (captures ungated)", rc4);
    } else {
        s_isActiveHackStartDemo = reinterpret_cast<IsActiveHackStartDemoFn>(iah_addr);
        SMOAP_LOG_INFO("isActiveHackStartDemo resolved @ 0x%lx", iah_addr);
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
    // outlives any individual capture (it's owned by the Player actor), so
    // the read is safe; only its bound-cap pointer changes per release.
    // If a different cap is now active and IS also blocked, CaptureStartHook
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
