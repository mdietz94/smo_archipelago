// See OdysseyRescue.hpp for design context.

#include "OdysseyRescue.hpp"

#include <cstring>

#include <hk/ro/RoUtil.h>

#include "../ap/ApState.hpp"
#include "../hooks/HookSymbols.hpp"
#include "../util/Log.hpp"
#include "KingdomUnlock.hpp"

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
using CrashHomeFn                 = void        (*)(GameDataHolderWriter);
using UnlockWorldFn               = void        (*)(GameDataHolderWriter, int);
using IsBossAttackedHomeFn        = bool        (*)(GameDataHolderAccessor);
using RepairHomeByCrashedBossFn   = void        (*)(GameDataHolderWriter);
using IsRepairHomeByCrashedBossFn = bool        (*)(GameDataHolderAccessor);
using GetWorldIndexFn             = int         (*)();
using GetCurrentStageNameFn       = const char* (*)(GameDataHolderAccessor);

struct ResolvedFns {
    IsCrashHomeFn               isCrashHome               = nullptr;
    RepairHomeFn                repairHome                = nullptr;
    CrashHomeFn                 crashHome                 = nullptr;
    UnlockWorldFn               unlockWorld               = nullptr;
    IsBossAttackedHomeFn        isBossAttackedHome        = nullptr;
    RepairHomeByCrashedBossFn   repairHomeByCrashedBoss   = nullptr;
    IsRepairHomeByCrashedBossFn isRepairHomeByCrashedBoss = nullptr;
    GetWorldIndexFn             getWorldIndexClash        = nullptr;
    GetWorldIndexFn             getWorldIndexSky          = nullptr;
    GetCurrentStageNameFn       getCurrentStageName       = nullptr;
};

ResolvedFns g_fns;
bool        g_ready = false;

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
    ok &= resolveOne(g_fns.crashHome,
        smoap::sym::kGameDataFunctionCrashHome, "crashHome");
    ok &= resolveOne(g_fns.unlockWorld,
        smoap::sym::kGameDataFunctionUnlockWorld, "unlockWorld");
    ok &= resolveOne(g_fns.isBossAttackedHome,
        smoap::sym::kGameDataFunctionIsBossAttackedHome, "isBossAttackedHome");
    ok &= resolveOne(g_fns.repairHomeByCrashedBoss,
        smoap::sym::kGameDataFunctionRepairHomeByCrashedBoss,
        "repairHomeByCrashedBoss");
    ok &= resolveOne(g_fns.isRepairHomeByCrashedBoss,
        smoap::sym::kGameDataFunctionIsRepairHomeByCrashedBoss,
        "isRepairHomeByCrashedBoss");
    ok &= resolveOne(g_fns.getWorldIndexClash,
        smoap::sym::kGameDataFunctionGetWorldIndexClash, "getWorldIndexClash");
    ok &= resolveOne(g_fns.getWorldIndexSky,
        smoap::sym::kGameDataFunctionGetWorldIndexSky, "getWorldIndexSky");
    ok &= resolveOne(g_fns.getCurrentStageName,
        smoap::sym::kGameDataFunctionGetCurrentStageName,
        "getCurrentStageName");
    g_ready = ok;
    SMOAP_LOG_INFO("OdysseyRescue: symbol resolution %s",
                   ok ? "COMPLETE" : "PARTIAL (sweep disabled)");
}

void runOdysseySoftlockSweep() {
    if (!g_ready) return;
    void* gdh = smoap::ap::ApState::instance().game_data_holder_cache.load(
        std::memory_order_relaxed);
    if (!gdh) return;
    GameDataHolderAccessor acc{gdh};
    GameDataHolderWriter   wr {gdh};

    // Log throttles — we expect the inner branches to be no-ops on virtually
    // every call once the player leaves Lost/Ruined; the only interesting
    // events are state transitions. Logging every 600 calls (= 10s at 60fps
    // throttle of 1 call/s × 60 frames) gives us a heartbeat without spam.
    static int s_lost_log = 0;
    static int s_ruined_log = 0;
    static int s_bossEdge_log = 0;

    // --- Lost Kingdom ---
    // Wrecked Odyssey state: detect via isCrashHome, force repair + unlock.
    if (g_fns.isCrashHome(acc)) {
        const char* stage = g_fns.getCurrentStageName(acc);
        if (stage && std::strcmp(stage, "ClashWorldHomeStage") == 0) {
            if ((s_lost_log++ % 600) == 0) {
                SMOAP_LOG_INFO(
                    "OdysseyRescue: Lost crashHome → repair + unlock");
            }
            g_fns.repairHome(wr);
            g_fns.unlockWorld(wr, g_fns.getWorldIndexClash());
        } else {
            // Defensive: crashed home outside Lost shouldn't happen, but if a
            // mid-cinematic state ever crashes the home elsewhere, repair so
            // the player isn't stranded.
            g_fns.repairHome(wr);
        }
    }

    // --- Ruined Kingdom ---
    // Boss-attacked Odyssey state: detect via isBossAttackedHome, force
    // repair-by-crashed-boss + crash transition. Kgamer77's commented-out
    // moon-count branch for Ruined crashed; we ship only the unconditional
    // repair path that did work for them. Ruined's home stage reports as
    // either "AttackWorldHomeStage" or "BossRaidWorldHomeStage" depending
    // on the subsystem — match both so the specific repair path fires
    // regardless of which one the runtime reports.
    if (g_fns.isBossAttackedHome(acc)) {
        const char* stage = g_fns.getCurrentStageName(acc);
        const bool is_ruined = stage && (
            std::strcmp(stage, "BossRaidWorldHomeStage") == 0 ||
            std::strcmp(stage, "AttackWorldHomeStage") == 0);
        if (is_ruined) {
            if ((s_ruined_log++ % 600) == 0) {
                SMOAP_LOG_INFO(
                    "OdysseyRescue: Ruined bossAttackedHome (stage=%s) → "
                    "repairByCrashedBoss + crashHome",
                    stage ? stage : "<null>");
            }
            g_fns.repairHomeByCrashedBoss(wr);
            g_fns.crashHome(wr);
        } else {
            g_fns.repairHome(wr);
        }
    }

    // --- Bowser unlock edge case (Kgamer77 v1.2 — apworld-gated) ---
    // Kgamer77's v1.2 unconditionally calls unlockWorld(Sky) whenever
    // isRepairHomeByCrashedBoss holds — that bypassed our apworld's
    // KingdomMoons(Ruined, 3) gate in regions.json, letting the player fly
    // to Bowser after defeating Lord of Lightning with 0 Ruined AP credits.
    // Gate the force-unlock on the same 3-credit threshold the apworld
    // logic graph enforces, so vanilla-style "have enough moons" still
    // gates forward progression while backtracking (handled by the Ruined
    // block above clearing the boss-attacked state) stays free.
    if (g_fns.isRepairHomeByCrashedBoss(acc)) {
        const std::uint8_t ruined_bit = kingdomBitFor("Ruined");
        int ruined_credits = -1;
        if (ruined_bit < 17) {
            ruined_credits = smoap::ap::ApState::instance()
                .ap_moons_kingdom[ruined_bit].load(std::memory_order_relaxed);
        }
        if (ruined_credits >= 3) {
            if ((s_bossEdge_log++ % 600) == 0) {
                SMOAP_LOG_INFO(
                    "OdysseyRescue: isRepairHomeByCrashedBoss + ruined_credits=%d "
                    "→ unlock Sky (apworld gate met)", ruined_credits);
            }
            g_fns.unlockWorld(wr, g_fns.getWorldIndexSky());
        } else {
            if ((s_bossEdge_log++ % 600) == 0) {
                SMOAP_LOG_INFO(
                    "OdysseyRescue: isRepairHomeByCrashedBoss but "
                    "ruined_credits=%d < 3 — leaving Bowser locked "
                    "(apworld KingdomMoons(Ruined, 3) gate not yet met)",
                    ruined_credits);
            }
        }
    }
}

}  // namespace smoap::game
