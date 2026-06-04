// See OdysseyRescue.hpp for design context.

#include "OdysseyRescue.hpp"

#include <cstring>

#include <hk/ro/RoUtil.h>

#include "../ap/ApState.hpp"
#include "../hooks/HookSymbols.hpp"
#include "../util/Log.hpp"

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

    // Log throttles — the inner branches are no-ops on virtually every call
    // once the player leaves Lost/Ruined; only state transitions are worth
    // logging. Logging every 600 calls (≈10s at the caller's ~1 call/s
    // throttle × 60 frames) gives a heartbeat without spam.
    static int s_lost_log = 0;
    static int s_ruined_log = 0;

    // --- Ruined Kingdom (MUST run before the Lost block below) ---
    // On arrival the Lord of Lightning grabs the Odyssey (isBossAttackedHome)
    // and blocks BOTH forward and backward flight. To let a player who rushed
    // in with an unswept upstream fly BACK and collect the moons that gate this
    // kingdom, convert the boss-attacked grounding into the generic "crashed"
    // grounding: repairHomeByCrashedBoss clears the boss-attack flag, crashHome
    // moves it to the crashed state, and the Lost block's else-branch below
    // repairHome()s it to a flyable state within THIS SAME pass. Ordering is
    // load-bearing — if the pass ended on crashHome the Odyssey would sit
    // grounded between throttled passes (~1s windows). Mirrors
    // Kgamer77/SuperMarioOdysseyArchipelago v1.2 updatePlayerInfo(), whose
    // Ruined block likewise precedes its Lost block.
    //
    // We do NOT unlockWorld(Sky) *inside* this block — that fires while Mario
    // is mid-fight and bumps mUnlockWorldNum prematurely (the Moon-skip). The
    // Bowser unlock instead lives in the separate edge-case block below, gated
    // on isRepairHomeByCrashedBoss so it only fires on a genuine boss defeat.
    //
    // Ruined's home stage reports as either "AttackWorldHomeStage" or
    // "BossRaidWorldHomeStage" depending on subsystem — match both.
    if (g_fns.isBossAttackedHome(acc)) {
        const char* stage = g_fns.getCurrentStageName(acc);
        const bool is_ruined = stage && (
            std::strcmp(stage, "BossRaidWorldHomeStage") == 0 ||
            std::strcmp(stage, "AttackWorldHomeStage") == 0);
        if (is_ruined) {
            if ((s_ruined_log++ % 600) == 0) {
                SMOAP_LOG_INFO(
                    "OdysseyRescue: Ruined bossAttackedHome (stage=%s) → "
                    "repairByCrashedBoss + crashHome (backtrack enabled)",
                    stage ? stage : "<null>");
            }
            g_fns.repairHomeByCrashedBoss(wr);
            g_fns.crashHome(wr);
        } else {
            // Defensive: boss-attacked home outside Ruined shouldn't happen,
            // but repair so the player isn't stranded.
            g_fns.repairHome(wr);
        }
    }

    // --- Bowser's-Kingdom unlock (Kgamer77 v1.2, verbatim) ---
    // Kgamer77's comment: "Edge case where game repairs odyssey in ruined but
    // doesn't unlock bowser kingdom". Fires once on a genuine Lord-of-Lightning
    // defeat (HomeStatus::RepairedHomeByCrashedBoss==7, which our force-repair
    // cycle above never produces — it lands on 4→5). unlockWorld(Sky) routes to
    // the idempotent GameProgressData::unlockNextWorld(12).
    //
    // KNOWN RISK: an earlier *gated* port (8179e7b) was reported to skip
    // Bowser→Moon via a mUnlockWorldNum double-count (the post-Ruined autopilot
    // switches on that counter; unlockNormalWorld()++ is unconditional). Per the
    // maintainer's decision we now match Kgamer77 EXACTLY — no dwell, no
    // isUnlockedWorld guard, no Ruined-moon gate — and trust their shipped
    // build. If a Ruined→Bowser playtest skips to Moon, revert to the guarded
    // variant in this file's history. See [[project-odyssey-unlockworld-skips-bowser]].
    if (g_fns.isRepairHomeByCrashedBoss(acc)) {
        static int s_bossEdge_log = 0;
        if ((s_bossEdge_log++ % 600) == 0) {
            SMOAP_LOG_INFO(
                "OdysseyRescue: isRepairHomeByCrashedBoss → unlockWorld(Sky)");
        }
        g_fns.unlockWorld(wr, g_fns.getWorldIndexSky());
    }

    // --- Lost Kingdom (also the Ruined crashed→flyable converter) ---
    // Wrecked Odyssey state in Lost: force repair + unlock so the player can
    // backtrack to Wooded. The else-branch ALSO completes the Ruined block
    // above — when crashHome left the Ruined home in the crashed state, this
    // repairHome() makes it flyable in the same pass.
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
            // Crashed home outside Lost: either our own Ruined crashHome (the
            // intended hand-off) or a stray mid-cinematic crash — repair either
            // way so the player isn't stranded.
            g_fns.repairHome(wr);
        }
    }
}

}  // namespace smoap::game
