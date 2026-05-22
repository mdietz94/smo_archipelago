// Talkatoo% pause-menu mark fix.
//
// Phase 4's TalkatooSpeechHook substitutes Talkatoo's speech bubble with
// AP-pool moon names but leaves the vanilla "Talkatoo named this moon"
// state pointing at SMO's internal picker. The pause-menu Power Moon list
// queries that state and marks the wrong row.
//
// Fix is two trampolines on GameDataFile:
//
//   isOpenShineName(world_id, index) const  — pause-menu getter. We let
//       Orig run (preserving collected / achievement / Hint-Toad reveals)
//       and OR-in our AP-pool named set so the row corresponding to the
//       moon Talkatoo actually said is also marked.
//
//   tryUnlockShineName(world_id, index)     — Talkatoo's vanilla setter.
//       We suppress writes in Talkatoo% mode so the vanilla picker's
//       choice doesn't pollute the menu state. Other unlock paths
//       (unlockAchievementShineName, isGet) are untouched.
//
// Translation: the getter receives (world_id, vanilla_shine_index). Our
// AP set is keyed by global shine_uid. We resolve via
// GameDataFile::findShine(world_id, index) → HintInfo*, then read
// HintInfo::stageName (offset 0x000) + HintInfo::objId (offset 0x098)
// and look the pair up in shine_lookup.hpp. Same offset constants as
// game/MoonApply.cpp's mShineHintList walk.

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <cstddef>
#include <cstdint>

#include "../ap/ApState.hpp"
#include "../ap/shine_lookup.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"

namespace smoap::hooks {

namespace {

// HintInfo field offsets — same as game/MoonApply.cpp.
constexpr std::size_t kHintInfo_StageName  = 0x000;
constexpr std::size_t kHintInfo_ObjId      = 0x098;
constexpr std::size_t kSeadFixedSafeString_mBufferOffset = 0x08;

inline const char* readFixedSafeStringBuffer(const std::uint8_t* fss_addr) {
    return *reinterpret_cast<const char* const*>(
        fss_addr + kSeadFixedSafeString_mBufferOffset);
}

// GameDataFile::findShine(world_id, index) const → const HintInfo*.
// Resolved once at install. Called with `this` as the implicit first arg
// per the Itanium ABI (matches addHackDictionary / forceKillHack pattern).
using FindShineFn = const void* (*)(const void* self, int world_id, int index);
FindShineFn s_findShine = nullptr;

// Cheap diagnostics gate — log the first time each hook fires under
// talkatoo_mode_on. Both demote silently after the first hit.
std::atomic<bool> g_logged_first_isopen{false};
std::atomic<bool> g_logged_first_tryunlock{false};

HkTrampoline<bool, const void*, int, int> isOpenShineNameHook =
    hk::hook::trampoline([](const void* self, int world_id, int index) -> bool {
        const bool vanilla = isOpenShineNameHook.orig(self, world_id, index);

        const bool talkatoo_mode_on =
            smoap::ap::ApState::instance().talkatoo_mode.load(
                std::memory_order_acquire);
        if (!talkatoo_mode_on) return vanilla;

        // Vanilla=true already covers collected + achievement-revealed +
        // any leftover Hint-Toad name reveals; preserve those. We only
        // need to OR-in our AP-pool named set when vanilla returns false.
        if (vanilla) return true;

        if (s_findShine == nullptr) return vanilla;

        const void* hint_info = s_findShine(self, world_id, index);
        if (hint_info == nullptr) return vanilla;

        const auto* hi_bytes = reinterpret_cast<const std::uint8_t*>(hint_info);
        const char* stage = readFixedSafeStringBuffer(hi_bytes + kHintInfo_StageName);
        const char* obj   = readFixedSafeStringBuffer(hi_bytes + kHintInfo_ObjId);
        if (stage == nullptr || obj == nullptr || !stage[0] || !obj[0]) {
            return vanilla;
        }

        const int shine_uid = smoap::game::shineUidByStageObj(stage, obj);
        if (shine_uid < 0) return vanilla;

        const bool named = smoap::ap::ApState::instance().isMoonNamed(shine_uid);

        bool expected = false;
        if (named &&
            g_logged_first_isopen.compare_exchange_strong(
                expected, true, std::memory_order_relaxed)) {
            SMOAP_LOG_INFO("[talkatoo-menu] first AP-named menu hit: "
                           "world_id=%d index=%d stage=%s obj=%s uid=%d",
                           world_id, index, stage, obj, shine_uid);
        }
        return named;
    });

HkTrampoline<void, void*, int, int> tryUnlockShineNameHook =
    hk::hook::trampoline([](void* self, int world_id, int index) {
        const bool talkatoo_mode_on =
            smoap::ap::ApState::instance().talkatoo_mode.load(
                std::memory_order_acquire);
        if (!talkatoo_mode_on) {
            tryUnlockShineNameHook.orig(self, world_id, index);
            return;
        }

        // Suppress: Talkatoo's vanilla picker is the only routine path
        // into this setter (achievements and collection use other
        // mechanisms). Skipping Orig leaves the menu state alone so the
        // only marks are the ones our isOpenShineName hook OR-in from
        // ApState::named_moons_bits.
        bool expected = false;
        if (g_logged_first_tryunlock.compare_exchange_strong(
                expected, true, std::memory_order_relaxed)) {
            SMOAP_LOG_INFO("[talkatoo-menu] suppressing first vanilla "
                           "tryUnlockShineName(world_id=%d index=%d) "
                           "under talkatoo_mode",
                           world_id, index);
        }
    });

}  // namespace

void installTalkatooMenuMarkHook() {
    const ptr fs_addr = hk::ro::lookupSymbol(smoap::sym::kGameDataFileFindShine);
    if (fs_addr == 0) {
        SMOAP_LOG_ERROR("[talkatoo-menu] findShine lookup FAILED — "
                        "isOpenShineName hook will fall through to vanilla "
                        "(no AP-named OR-in)");
    } else {
        s_findShine = reinterpret_cast<FindShineFn>(fs_addr);
        SMOAP_LOG_INFO("[talkatoo-menu] findShine resolved @ 0x%lx",
                       static_cast<unsigned long>(fs_addr));
    }

    SMOAP_LOG_INFO("installing TalkatooMenuMarkHook isOpenShineName -> %s",
                   smoap::sym::kGameDataFileIsOpenShineName);
    isOpenShineNameHook.installAtSym<
        "_ZNK12GameDataFile15isOpenShineNameEii">();

    SMOAP_LOG_INFO("installing TalkatooMenuMarkHook tryUnlockShineName -> %s",
                   smoap::sym::kGameDataFileTryUnlockShineName);
    tryUnlockShineNameHook.installAtSym<
        "_ZN12GameDataFile18tryUnlockShineNameEii">();
}

}  // namespace smoap::hooks
