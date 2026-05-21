// Snapshot enumeration. Walks GameDataFile::mShineHintList and emits
// (stage_name, object_id, unique_id) for each owned shine.
//
// Hakkun port: no lib/nx/nx.h dependency — direct memory reads only.

#include "MoonApply.hpp"

#include <cstddef>
#include <cstdint>

#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::game {

namespace {

constexpr std::size_t kGameDataHolder_mGameDataFileOffset = 0x20;
constexpr std::size_t kGameDataFile_mShineHintListOffset  = 0x9A0;
constexpr std::size_t kHintInfo_Size       = 0x238;
constexpr std::size_t kHintInfo_StageName  = 0x000;
constexpr std::size_t kHintInfo_ObjId      = 0x098;
constexpr std::size_t kHintInfo_IsGet      = 0x1D1;
constexpr std::size_t kHintInfo_UniqueID   = 0x1F0;
constexpr std::size_t kSeadFixedSafeString_mBufferOffset = 0x08;
constexpr int         kShineHintListScanCount = 0x400;

inline const char* readFixedSafeStringBuffer(const std::uint8_t* fss_addr) {
    return *reinterpret_cast<const char* const*>(
        fss_addr + kSeadFixedSafeString_mBufferOffset);
}

}  // namespace

void grantShine(const std::string& kingdom, const std::string& shine_id) {
    SMOAP_LOG_INFO("grantShine (stub): %s / %s", kingdom.c_str(), shine_id.c_str());
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
        if (uid == 0) continue;
        ++scanned;
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
    SMOAP_LOG_INFO("installSnapshotSymbols: no-op (HintInfo::isGet read directly)");
}

}  // namespace smoap::game
