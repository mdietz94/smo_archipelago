// See OdysseyRescue.hpp for design context.

#include "OdysseyRescue.hpp"

#include <cstring>

#include <hk/ro/RoUtil.h>

#include "../ap/ApState.hpp"
#include "../hooks/HookSymbols.hpp"
#include "../util/Log.hpp"
#include "CaptureGate.hpp"

namespace smoap::game {

namespace {

// Match the GameDataHolderAccessor/Writer layout used by other hooks in this
// codebase (see ShineNumGetHook.cpp, AddHackDictionaryHook.cpp). Both are a
// single void* wrapper; the Itanium ABI passes them by value as a single
// pointer-sized argument in x0 on aarch64.
struct GameDataHolderAccessor { void* mData; };
struct GameDataHolderWriter   { void* mData; };

using IsCrashHomeFn               = bool        (*)(GameDataHolderAccessor);
using RepairHomeFn                = void        (*)(GameDataHolderWriter);
using UnlockWorldFn               = void        (*)(GameDataHolderWriter, int);
using GetWorldIndexFn             = int         (*)();
using GetCurrentStageNameFn       = const char* (*)(GameDataHolderAccessor);
using IsBossAttackedHomeNextFn    = bool        (*)(GameDataHolderAccessor, int);

struct ResolvedFns {
    IsCrashHomeFn               isCrashHome               = nullptr;
    RepairHomeFn                repairHome                = nullptr;
    UnlockWorldFn               unlockWorld               = nullptr;
    GetWorldIndexFn             getWorldIndexClash        = nullptr;
    GetCurrentStageNameFn       getCurrentStageName       = nullptr;
    // Bowser's-softlock branch (resolved independently of the Lost branch).
    GetWorldIndexFn             getWorldIndexSky          = nullptr;
    GetWorldIndexFn             getWorldIndexMoon         = nullptr;
    IsBossAttackedHomeNextFn    isBossAttackedHomeNext    = nullptr;
};

ResolvedFns g_fns;
bool        g_ready = false;         // Lost branch (the original 5 symbols)
bool        g_bowser_ready = false;  // Bowser's branch (the 3 new symbols)

template <typename Fn>
bool resolveOne(Fn& slot, const char* mangled, const char* tag) {
    const ptr addr = hk::ro::lookupSymbol(mangled);
    if (addr == 0) {
        SMOAP_LOG_ERROR("OdysseyRescue: %s lookup FAILED", tag);
        slot = nullptr;
        return false;
    }
    slot = reinterpret_cast<Fn>(addr);
    SMOAP_LOG_INFO("OdysseyRescue: %s @ 0x%lx", tag,
                   static_cast<unsigned long>(addr));
    return true;
}

}  // namespace

void installOdysseyRescueSymbols() {
    bool ok = true;
    ok &= resolveOne(g_fns.isCrashHome,
        smoap::sym::kGameDataFunctionIsCrashHome, "isCrashHome");
    ok &= resolveOne(g_fns.repairHome,
        smoap::sym::kGameDataFunctionRepairHome, "repairHome");
    ok &= resolveOne(g_fns.unlockWorld,
        smoap::sym::kGameDataFunctionUnlockWorld, "unlockWorld");
    ok &= resolveOne(g_fns.getWorldIndexClash,
        smoap::sym::kGameDataFunctionGetWorldIndexClash, "getWorldIndexClash");
    ok &= resolveOne(g_fns.getCurrentStageName,
        smoap::sym::kGameDataFunctionGetCurrentStageName,
        "getCurrentStageName");
    g_ready = ok;
    SMOAP_LOG_INFO("OdysseyRescue: Lost-branch symbol resolution %s",
                   ok ? "COMPLETE" : "PARTIAL (Lost sweep disabled)");

    // Bowser's branch resolves independently — a failure here must NOT take
    // down the Lost sweep (and vice-versa). getCurrentStageName is shared and
    // already resolved above.
    bool bok = g_fns.getCurrentStageName != nullptr;
    bok &= resolveOne(g_fns.getWorldIndexSky,
        smoap::sym::kGameDataFunctionGetWorldIndexSky, "getWorldIndexSky");
    bok &= resolveOne(g_fns.getWorldIndexMoon,
        smoap::sym::kGameDataFunctionGetWorldIndexMoon, "getWorldIndexMoon");
    bok &= resolveOne(g_fns.isBossAttackedHomeNext,
        smoap::sym::kGameDataFunctionIsBossAttackedHomeNext,
        "isBossAttackedHomeNext");
    g_bowser_ready = bok;
    SMOAP_LOG_INFO("OdysseyRescue: Bowser's-branch symbol resolution %s",
                   bok ? "COMPLETE" : "PARTIAL (Bowser's sweep disabled)");
}

void runOdysseySoftlockSweep() {
    if (!g_ready && !g_bowser_ready) return;
    void* gdh = smoap::ap::ApState::instance().game_data_holder_cache.load(
        std::memory_order_relaxed);
    if (!gdh) return;
    GameDataHolderAccessor acc{gdh};
    GameDataHolderWriter   wr {gdh};

    // Log throttle — the branches below are a no-op on virtually every call
    // once the player leaves the affected kingdom; only state transitions are
    // worth logging. Logging every 600 calls (≈10s at the caller's ~1 call/s
    // throttle × 60 frames) gives a heartbeat without spam.
    static int s_lost_log = 0;
    static int s_bowser_log = 0;

    // --- Lost Kingdom ---
    // Wrecked Odyssey state in Lost: force repair + unlock so a player who
    // rushed in with an unswept upstream can backtrack to Wooded and collect
    // the moons that gate this kingdom. unlockWorld(getWorldIndexClash())
    // unlocks the world Mario is already in (Lost), so it doesn't perturb the
    // post-kingdom autopilot the way pre-unlocking the *next* world would.
    //
    // Ruined Kingdom is deliberately NOT handled here. Ruined grounds the
    // Odyssey via the Lord of Lightning's boss-attack state, which vanilla
    // clears the moment the player beats the dragon and collects the Ruined
    // Multi-Moon. We keep that Multi-Moon pinned to its vanilla location (the
    // dragon) in AP fill — see apworld locations.json "place_item" on
    // "Ruined: Battle with the Lord of Lightning!" — so beating the dragon
    // always repairs the Odyssey and lets the player leave. No sweep needed,
    // and crucially no risk of the counter-overshoot bug that the old Ruined
    // backtrack path triggered (post-boss autopilot skipping Bowser → Moon).
    if (g_ready && g_fns.isCrashHome(acc)) {
        const char* stage = g_fns.getCurrentStageName(acc);
        if (stage && std::strcmp(stage, "ClashWorldHomeStage") == 0) {
            if ((s_lost_log++ % 600) == 0) {
                SMOAP_LOG_INFO(
                    "OdysseyRescue: Lost crashHome → repair + unlock");
            }
            g_fns.repairHome(wr);
            g_fns.unlockWorld(wr, g_fns.getWorldIndexClash());
        } else {
            // Crashed home outside Lost: a stray mid-cinematic crash — repair
            // so the player isn't stranded.
            g_fns.repairHome(wr);
        }
    }

    // --- Bowser's Kingdom ---
    // Bowser's grounds the Odyssey too, but NOT via the home-status enum that
    // isCrashHome reads (after Ruined the status is RepairedHomeByCrashedBoss,
    // so the branch above never fires here). Instead departure is gated by
    // isBossAttackedHomeNext(acc, Sky): true while mUnlockWorldNum sits at
    // Boss(Ruined)/Boss+1(Sky) and the player is in SkyWorldHomeStage. It only
    // clears when beating Bowser advances the unlock to Moon — which a
    // capturesanity player without the Pokio capture can never do, since Pokio
    // is the sole way up the castle to the RoboBrood. They also can't fly out
    // → permanent softlock.
    //
    // Clear the gate by advancing the unlock to Moon (the only lever for
    // isBossAttackedHomeNext), so the player can fly back out to find Pokio.
    // This is gated hard on captureBlocked("Pokio"): it is FALSE for
    // non-capturesanity seeds (every capture is synthetically unlocked at
    // HELLO) and for any player who already owns Pokio, so the rescue fires
    // ONLY for a genuinely-stuck player. Players who can beat Bowser keep the
    // untouched vanilla Bowser→Moon autopilot — whose mUnlockWorldNum
    // overshoot is the documented footgun that got the old Ruined backtrack
    // path removed. Once Moon is unlocked the gate reads false and the branch
    // self-quiesces; unlockWorld(Moon) is idempotent if it somehow re-enters.
    if (g_bowser_ready &&
        g_fns.isBossAttackedHomeNext(acc, g_fns.getWorldIndexSky())) {
        const char* stage = g_fns.getCurrentStageName(acc);
        if (stage && std::strcmp(stage, "SkyWorldHomeStage") == 0 &&
            captureBlocked("Pokio")) {
            if ((s_bowser_log++ % 600) == 0) {
                SMOAP_LOG_INFO(
                    "OdysseyRescue: Bowser's grounded + no Pokio → unlock Moon");
            }
            g_fns.unlockWorld(wr, g_fns.getWorldIndexMoon());
        }
    }
}

}  // namespace smoap::game
