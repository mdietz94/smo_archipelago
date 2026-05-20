// M6 phase C: snapshot enumeration. Walks GameDataFile::mShineHintList and
// emits (stage_name, object_id, unique_id) for each owned shine into the
// caller-supplied callback. Worker-thread safe — no allocations, only raw
// pointer reads against in-game memory.
//
// PRIOR BUG (2026-05-19, fixed by reading HintInfo::mIsGet directly).
// Earlier revision called GameDataFile::isGotShine(int) passing each
// HintInfo's mUniqueID. That overload takes a per-world *shine index*
// (small dense integer 0..40-ish), NOT a global unique ID. The two
// numeric ranges overlap, so the function would return true whenever
// the int we passed happened to equal an actually-collected per-world
// index. Concrete case observed: Mario picks up Cascade "Our First
// Power Moon" (Cascade-local index 31, global UID 205). isGotShine(31)
// in Cascade-world context returns true. We walked the full hint list
// passing UIDs; the entry whose mUniqueID == 31 — "Snow: Running the
// Flower Road" — emitted as a phantom "collected" shine on every
// reconnect. Source: MonsterDruide1/OdysseyDecomp GameDataFile.h. The
// fix is to read mIsGet on each HintInfo directly, which the game sets
// on the matching HintInfo when setGotShine fires. No UID/index ambiguity.

#include "MoonApply.hpp"

#include <cstddef>
#include <cstdint>

#include "lib/nx/nx.h"           // Result, R_FAILED
#include "../ap/ApState.hpp"
#include "../hooks/HookSymbols.hpp"
#include "../util/Log.hpp"

namespace smoap::game {

namespace {

// Layout offsets (verified against lunakit-vendor headers; comments cite the
// source line so future spot-checks land in seconds):

// GameDataHolder.h:94 — first non-vtable field is GameDataFile* mGameDataFile.
constexpr std::size_t kGameDataHolder_mGameDataFileOffset = 0x20;

// GameDataFile.h:463 — `HintInfo *mShineHintList; // 0x9A0`. Pointer to a
// dynamically-allocated array of HintInfo, one per known shine.
constexpr std::size_t kGameDataFile_mShineHintListOffset = 0x9A0;

// GameDataFile.h:86 — `static_assert(sizeof(HintInfo) == 0x238)`.
constexpr std::size_t kHintInfo_Size = 0x238;

// HintInfo fields (offsets from GameDataFile.h:57-83 + OdysseyDecomp):
constexpr std::size_t kHintInfo_StageName  = 0x000;  // FixedSafeString<0x80>
constexpr std::size_t kHintInfo_ObjId      = 0x098;  // FixedSafeString<0x80>
// "isGet" / collected flag. Lunakit's header labels offset 0x1D1 as
// `unkBool1` adjacent to `mIsMoonRock` (0x1D0), `mIsAchievement` (0x1D2),
// etc. — OdysseyDecomp identifies this slot as the "collected" bool that
// setGotShine flips. VERIFIED 2026-05-19 via [hintinfo-probe] in
// MoonGetHook on a fresh Cascade obj214 pickup:
//   [hintinfo-probe] bytes @0x1D0..0x1D5 = 00 01 00 00 00 00
// Only the byte at 0x1D1 transitions to 1 on collection.
constexpr std::size_t kHintInfo_IsGet      = 0x1D1;  // bool
constexpr std::size_t kHintInfo_UniqueID   = 0x1F0;  // int (kept for diagnostic logging)

// sead::FixedSafeString layout: vtable at +0x0, then `char* mBuffer` at +0x8
// pointing at the inline `mInlineBuffer` at +0x18. Reading mBuffer gives a
// const char* equivalent to FixedSafeString::cstr() — no symbol bind needed
// and no allocation.
constexpr std::size_t kSeadFixedSafeString_mBufferOffset = 0x08;

// lunakit's custom findShine(int shineUid) scans 0x400 entries unconditionally
// (see GameDataFile.h:362-369), so mShineHintList is sized for ≥ 0x400.
constexpr int kShineHintListScanCount = 0x400;

inline const char* readFixedSafeStringBuffer(const std::uint8_t* fss_addr) {
    return *reinterpret_cast<const char* const*>(
        fss_addr + kSeadFixedSafeString_mBufferOffset);
}

}  // namespace

void grantShine(const std::string& kingdom, const std::string& shine_id) {
    SMOAP_LOG_INFO("grantShine (stub): %s / %s", kingdom.c_str(), shine_id.c_str());
    // M6:
    //   GameDataHolder* gdh = al::tryGetGameDataHolder();
    //   if (!gdh) return;
    //   ShineId sid = mapShineId(kingdom, shine_id);
    //   if (gdh->isGetShine(sid)) return;
    //   gdh->setShineGet(sid);  // also bumps moon counter / opens gates
}

bool extractShineCoords(std::string& out_kingdom, std::string& out_shine_id) {
    out_kingdom.clear();
    out_shine_id.clear();
    return false;
}

void enumerateOwnedShines(ShineEnumerationCallback cb, void* ctx) {
    void* gdh = smoap::ap::ApState::instance().game_data_holder_cache.load(
        std::memory_order_relaxed);
    if (!gdh) {
        SMOAP_LOG_WARN("[snapshot] enumerateOwnedShines skipped: gdh=%p", gdh);
        return;
    }
    const auto* gdh_bytes = reinterpret_cast<const std::uint8_t*>(gdh);
    void* gdf = *reinterpret_cast<void* const*>(
        gdh_bytes + kGameDataHolder_mGameDataFileOffset);
    if (!gdf) {
        SMOAP_LOG_WARN("[snapshot] enumerateOwnedShines: GameDataFile* is null");
        return;
    }
    const auto* gdf_bytes = reinterpret_cast<const std::uint8_t*>(gdf);
    const auto* hint_base = *reinterpret_cast<const std::uint8_t* const*>(
        gdf_bytes + kGameDataFile_mShineHintListOffset);
    if (!hint_base) {
        SMOAP_LOG_WARN("[snapshot] enumerateOwnedShines: mShineHintList is null");
        return;
    }

    int scanned = 0;
    int emitted = 0;
    for (int i = 0; i < kShineHintListScanCount; ++i) {
        const std::uint8_t* h = hint_base + (i * kHintInfo_Size);
        const int uid = *reinterpret_cast<const int*>(h + kHintInfo_UniqueID);
        if (uid == 0) continue;  // unused / sentinel slot
        ++scanned;
        // Read the HintInfo's own "isGet" flag — set by SMO's setGotShine on
        // the matching HintInfo when Mario picks up that shine. Direct byte
        // read; no symbol lookup, no UID-vs-index ambiguity.
        const bool is_get = *(h + kHintInfo_IsGet) != 0;
        if (!is_get) continue;
        const char* stage = readFixedSafeStringBuffer(h + kHintInfo_StageName);
        const char* obj   = readFixedSafeStringBuffer(h + kHintInfo_ObjId);
        if (!stage || !obj || !stage[0] || !obj[0]) continue;
        cb(ctx, stage, obj, uid);
        ++emitted;
    }
    SMOAP_LOG_INFO("[snapshot] enumerateOwnedShines scanned=%d emitted=%d",
                   scanned, emitted);
}

void installSnapshotSymbols() {
    // No symbols to resolve anymore — enumeration reads HintInfo::isGet
    // directly. Kept as a stub so call sites in main.cpp don't churn.
    SMOAP_LOG_INFO("installSnapshotSymbols: no-op (HintInfo::isGet read directly)");
}

}  // namespace smoap::game
