// Hook on GameDataFile::setGotShine(const ShineInfo*).
//
// Reads (stageName, objectId, shineId) from the ShineInfo* via the layout
// mirror in game/ShineInfoLayout.hpp (no transitive lunakit-vendor pull-in)
// and ships the raw IDs to the bridge. The bridge resolves them against
// shine_map.json into the AP location name.

#include "lib.hpp"  // HOOK_DEFINE_TRAMPOLINE
#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../game/MoonApply.hpp"
#include "../game/ShineInfoLayout.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

#include <atomic>
#include <cstdint>
#include <cstring>

class GameDataFile;
class ShineInfo;

namespace smoap::hooks {

namespace {

// One-shot HintInfo "isGet" offset probe. After Orig() flips whichever byte
// represents "collected" on the matching HintInfo, walk mShineHintList,
// find the HintInfo whose mStageName + mObjId match the just-collected
// shine, and dump bytes 0x1D0..0x1D5 of that HintInfo. The byte showing
// `0x01` is the real isGet offset — used to validate the
// kHintInfo_IsGet = 0x1D1 constant in MoonApply.cpp. Runs at most once
// per session (guarded by std::atomic_flag). Cheap, frame-thread.
std::atomic_flag g_probe_fired = ATOMIC_FLAG_INIT;

// Layout offsets mirrored from MoonApply.cpp's anonymous namespace. Kept
// in sync manually for now — a shared header would be cleaner but this
// is a temporary diagnostic.
constexpr std::size_t kProbe_GameDataHolder_FileOff   = 0x20;
constexpr std::size_t kProbe_GameDataFile_HintListOff = 0x9A0;
constexpr std::size_t kProbe_HintInfo_Size            = 0x238;
constexpr std::size_t kProbe_HintInfo_StageName       = 0x000;
constexpr std::size_t kProbe_HintInfo_ObjId           = 0x098;
constexpr std::size_t kProbe_HintInfo_UniqueID        = 0x1F0;
constexpr std::size_t kProbe_FSS_BufferOff            = 0x08;
constexpr int         kProbe_ScanCount                = 0x400;

inline const char* probeReadFssBuffer(const std::uint8_t* fss_addr) {
    return *reinterpret_cast<const char* const*>(fss_addr + kProbe_FSS_BufferOff);
}

void probeHintInfoIsGetOffset(const char* picked_stage, const char* picked_obj) {
    if (!picked_stage || !picked_obj) return;
    if (g_probe_fired.test_and_set(std::memory_order_relaxed)) return;

    auto& s = smoap::ap::ApState::instance();
    void* gdh = s.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!gdh) {
        SMOAP_LOG_WARN("[hintinfo-probe] gdh cache null; skipping");
        return;
    }
    const auto* gdh_bytes = reinterpret_cast<const std::uint8_t*>(gdh);
    void* gdf = *reinterpret_cast<void* const*>(
        gdh_bytes + kProbe_GameDataHolder_FileOff);
    if (!gdf) {
        SMOAP_LOG_WARN("[hintinfo-probe] gdf null; skipping");
        return;
    }
    const auto* gdf_bytes = reinterpret_cast<const std::uint8_t*>(gdf);
    const auto* hint_base = *reinterpret_cast<const std::uint8_t* const*>(
        gdf_bytes + kProbe_GameDataFile_HintListOff);
    if (!hint_base) {
        SMOAP_LOG_WARN("[hintinfo-probe] hint_base null; skipping");
        return;
    }

    for (int i = 0; i < kProbe_ScanCount; ++i) {
        const std::uint8_t* h = hint_base + (i * kProbe_HintInfo_Size);
        const int uid = *reinterpret_cast<const int*>(h + kProbe_HintInfo_UniqueID);
        if (uid == 0) continue;
        const char* stage = probeReadFssBuffer(h + kProbe_HintInfo_StageName);
        const char* obj   = probeReadFssBuffer(h + kProbe_HintInfo_ObjId);
        if (!stage || !obj) continue;
        if (std::strcmp(stage, picked_stage) != 0) continue;
        if (std::strcmp(obj, picked_obj) != 0) continue;
        // Found the just-picked-up shine's HintInfo. Dump the bool block
        // (0x1D0-0x1D5 — mIsMoonRock, unkBool1/isGet?, mIsAchievement,
        // mIsGrand, mIsShopMoon, padding). Whichever transitions 0→1 on
        // collection is the real isGet offset.
        SMOAP_LOG_INFO("[hintinfo-probe] matched (%s, %s) at slot=%d uid=%d",
                       stage, obj, i, uid);
        SMOAP_LOG_INFO("[hintinfo-probe] bytes @0x1D0..0x1D5 = "
                       "%02x %02x %02x %02x %02x %02x "
                       "(0x1D0=mIsMoonRock, 0x1D1=isGet?, 0x1D2=mIsAchievement, "
                       "0x1D3=mIsGrand, 0x1D4=mIsShopMoon)",
                       h[0x1D0], h[0x1D1], h[0x1D2], h[0x1D3], h[0x1D4], h[0x1D5]);
        return;
    }
    SMOAP_LOG_WARN("[hintinfo-probe] no HintInfo matched (%s, %s) — "
                   "stage/obj read offsets may be wrong",
                   picked_stage, picked_obj);
}

// Quick sanity check: do the first few bytes of a string pointer look like
// ASCII? If the offset is wrong we'll get random bytes or kernel addresses;
// using strlen / %s on those is fatal. Reject anything that doesn't smell
// like a normal printable string in the first 8 bytes.
bool stringSane(const char* s) {
    if (!s) return false;
    // Reject obvious junk pointer patterns (kernel addresses, low pages).
    auto p = reinterpret_cast<std::uintptr_t>(s);
    if (p < 0x10000) return false;  // null-ish page
    for (int i = 0; i < 8; ++i) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c == 0) return i > 0;       // empty string allowed only if first byte is non-null below... actually accept c==0 if i>0
        if (c < 0x20 || c > 0x7e) return false;
    }
    return true;
}

HOOK_DEFINE_TRAMPOLINE(MoonGetHook) {
    static void Callback(GameDataFile* self, const ShineInfo* info) {
        Orig(self, info);
        SMOAP_LOG_INFO("MoonGetHook fired: info=%p", info);
        if (!info) return;
        const char* stage = smoap::game::shine_info_layout::stageName(info);
        SMOAP_LOG_INFO("MoonGetHook: stage_ptr=%p", stage);
        const char* obj = smoap::game::shine_info_layout::objectId(info);
        SMOAP_LOG_INFO("MoonGetHook: obj_ptr=%p", obj);
        const char* scen = smoap::game::shine_info_layout::scenObjId(info);
        SMOAP_LOG_INFO("MoonGetHook: scen_ptr=%p", scen);
        const int uid = smoap::game::shine_info_layout::shineId(info);
        SMOAP_LOG_INFO("MoonGetHook: uid=%d", uid);

        const bool stage_ok = stringSane(stage);
        const bool obj_ok = stringSane(obj);
        const bool scen_ok = stringSane(scen);
        SMOAP_LOG_INFO("MoonGetHook: probe stage=%s obj=%s scen=%s uid=%d",
                       stage_ok ? stage : "<bad>",
                       obj_ok ? obj : "<bad>",
                       scen_ok ? scen : "<bad>",
                       uid);
        // The canonical moon identifier SMO emits is ObjId — a placement-file
        // reference like "obj214". This was confirmed end-to-end against
        // MoonFlow's ShineInfo schema (https://github.com/Amethyst-szs/MoonFlow):
        // display names are looked up by ("ScenarioName_" + ObjId) in the
        // per-stage MSBT, but ObjId alone is the stable identity. scenObjId
        // (offset 0x130) is just "ScenarioName_objN" — redundant. Keep the
        // probe log above for diagnostics, but report ObjId.
        if (stage_ok && obj_ok) {
            SMOAP_LOG_INFO("MoonGetHook: reporting stage=%s id=%s uid=%d", stage, obj, uid);
            // One-shot probe: validate the kHintInfo_IsGet = 0x1D1 offset in
            // MoonApply.cpp by dumping the bool block of THIS shine's
            // HintInfo. Orig() has already flipped whatever flag means
            // "collected"; we should see exactly one byte = 0x01 in the
            // range, and that byte's offset is the answer.
            probeHintInfoIsGetOffset(stage, obj);
            smoap::ap::reportMoonChecked(stage, obj, uid);
        } else {
            SMOAP_LOG_WARN("MoonGetHook: insane string ptrs stage_ok=%d obj_ok=%d — "
                           "offsets in ShineInfoLayout.hpp likely wrong; dropping",
                           stage_ok ? 1 : 0, obj_ok ? 1 : 0);
        }
    }
};
}  // namespace

void installMoonGetHook() {
    SMOAP_LOG_INFO("installing MoonGetHook -> %s", smoap::sym::kGameDataFileSetGotShine);
    softInstallAtSymbol<MoonGetHook>(smoap::sym::kGameDataFileSetGotShine);
}

}  // namespace smoap::hooks
