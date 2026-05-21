// Capture lock + cap-name → bit-index mapping.
//
// Hakkun port: nn::ro::LookupSymbol → hk::ro::lookupSymbol; all other logic
// retained verbatim from production switch-mod.

#include "CaptureGate.hpp"

#include <cstring>

#include <hk/ro/RoUtil.h>

#include "../ap/ApState.hpp"
#include "../ap/capture_table.h"  // kCaptureNames, kCaptureHackNames
#include "../hooks/HookSymbols.hpp"
#include "../util/Log.hpp"

struct GameDataHolderWriter   { void* mData; };
struct GameDataHolderAccessor { void* mData; };

namespace smoap::game {

namespace {

using AddHackDictionaryFn      = void (*)(GameDataHolderWriter, const char*);
using IsExistInHackDictionaryFn = bool (*)(GameDataHolderAccessor, const char*);

AddHackDictionaryFn       s_addHackDictionary       = nullptr;
IsExistInHackDictionaryFn s_isExistInHackDictionary = nullptr;

// Always-unlocked captures whose dict entries we pre-populate at scene load,
// independent of AP grants. The apworld excludes these from the AP item pool
// (free / always-available) so the compendium needs them written here.
inline constexpr std::array<std::string_view, 3> kBaselineHacks = {
    "Frog",
    "ElectricWire",
    "Koopa",
};

}  // namespace

std::uint8_t captureBitFor(const char* cap_name) {
    if (!cap_name) return 0xff;
    const std::size_t n = std::strlen(cap_name);
    for (std::uint8_t i = 0; i < kCaptureHackNames.size(); ++i) {
        const auto& sv = kCaptureHackNames[i];
        if (sv.size() == n && std::memcmp(cap_name, sv.data(), n) == 0) return i;
    }
    for (std::uint8_t i = 0; i < kCaptureNames.size(); ++i) {
        const auto& sv = kCaptureNames[i];
        if (sv.size() == n && std::memcmp(cap_name, sv.data(), n) == 0) return i;
    }
    return 0xff;
}

bool captureBlocked(const char* cap_name) {
    const std::uint8_t bit = captureBitFor(cap_name);
    if (bit == 0xff) return false;
    return !smoap::ap::ApState::instance().captures_unlocked.test(bit);
}

std::string nameForHackData(/* const PlayerHackData* data */) {
    return {};
}

void playSE_NG() {
    SMOAP_LOG_INFO("playSE_NG (stub)");
}

void enumerateOwnedCaptures(CaptureEnumerationCallback cb, void* ctx) {
    if (!cb || !s_isExistInHackDictionary) {
        SMOAP_LOG_WARN("[snapshot] enumerateOwnedCaptures skipped: cb=%p sym=%p",
                       reinterpret_cast<void*>(cb),
                       reinterpret_cast<void*>(s_isExistInHackDictionary));
        return;
    }
    void* gdh = smoap::ap::ApState::instance().game_data_holder_cache.load(
        std::memory_order_relaxed);
    if (!gdh) {
        SMOAP_LOG_WARN("[snapshot] enumerateOwnedCaptures: GameDataHolder not cached yet");
        return;
    }
    GameDataHolderAccessor acc{gdh};
    int emitted = 0;
    for (const auto& sv : kCaptureHackNames) {
        if (sv.empty()) continue;
        const char* name = sv.data();
        if (s_isExistInHackDictionary(acc, name)) {
            cb(ctx, name);
            ++emitted;
        }
    }
    SMOAP_LOG_INFO("[snapshot] enumerateOwnedCaptures emitted=%d", emitted);
}

bool captureAlreadyInDictionary(const char* hack_name) {
    if (!hack_name || !*hack_name) return false;
    if (!s_isExistInHackDictionary) return false;
    void* gdh = smoap::ap::ApState::instance().game_data_holder_cache.load(
        std::memory_order_relaxed);
    if (!gdh) return false;
    GameDataHolderAccessor acc{gdh};
    return s_isExistInHackDictionary(acc, hack_name);
}

bool grantCapture(const char* cap_name, const char* hack_name) {
    if (!hack_name || !*hack_name) {
        SMOAP_LOG_WARN("[m6-capture] dropped: empty hack_name (cap='%s')",
                       cap_name ? cap_name : "");
        return false;
    }
    if (!s_addHackDictionary || !s_isExistInHackDictionary) {
        SMOAP_LOG_WARN("[m6-capture] dropped: symbols unresolved "
                       "(cap='%s' hack='%s')",
                       cap_name ? cap_name : "", hack_name);
        return false;
    }
    auto& st = smoap::ap::ApState::instance();
    if (!st.scene_cache.load(std::memory_order_relaxed)) {
        SMOAP_LOG_WARN("[m6-capture] dropped: scene not loaded yet "
                       "(cap='%s' hack='%s') — reconciler will retry",
                       cap_name ? cap_name : "", hack_name);
        return false;
    }
    void* gdh = st.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!gdh) {
        SMOAP_LOG_WARN("[m6-capture] dropped: GameDataHolder not cached yet "
                       "(cap='%s' hack='%s')",
                       cap_name ? cap_name : "", hack_name);
        return false;
    }
    GameDataHolderAccessor acc{gdh};
    if (s_isExistInHackDictionary(acc, hack_name)) {
        SMOAP_LOG_INFO("[m6-capture] already in dictionary cap='%s' hack='%s'",
                       cap_name ? cap_name : "", hack_name);
        return true;
    }
    GameDataHolderWriter w{gdh};
    SMOAP_LOG_INFO("[m6-capture] grantCapture firing cap='%s' hack='%s'",
                   cap_name ? cap_name : "", hack_name);
    s_addHackDictionary(w, hack_name);
    SMOAP_LOG_INFO("[m6-capture] addHackDictionary OK cap='%s' hack='%s'",
                   cap_name ? cap_name : "", hack_name);
    return true;
}

void reconcileCaptureDictionary() {
    auto& s = smoap::ap::ApState::instance();
    if (!s_addHackDictionary || !s_isExistInHackDictionary) return;
    if (!s.scene_cache.load(std::memory_order_relaxed)) return;
    void* gdh = s.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!gdh) return;

    GameDataHolderAccessor acc{gdh};
    GameDataHolderWriter   w{gdh};

    for (const auto& sv : kBaselineHacks) {
        if (sv.empty()) continue;
        const char* hack = sv.data();
        if (s_isExistInHackDictionary(acc, hack)) continue;
        SMOAP_LOG_INFO("[m6-capture] baseline pre-populate hack='%s'", hack);
        s_addHackDictionary(w, hack);
    }

    if (s.captures_unlocked.none()) return;

    for (std::uint8_t i = 0; i < kCaptureHackNames.size(); ++i) {
        if (!s.captures_unlocked.test(i)) continue;
        const auto& sv = kCaptureHackNames[i];
        if (sv.empty()) continue;
        const char* hack = sv.data();
        if (s_isExistInHackDictionary(acc, hack)) continue;
        SMOAP_LOG_INFO("[m6-capture] reconcile firing for bit=%u hack='%s'",
                       static_cast<unsigned>(i), hack);
        s_addHackDictionary(w, hack);
    }
}

void installCaptureGrantSymbols() {
    ptr addr = hk::ro::lookupSymbol(smoap::sym::kGameDataFunctionAddHackDictionary);
    if (addr == 0) {
        SMOAP_LOG_ERROR("addHackDictionary lookup FAILED");
    } else {
        s_addHackDictionary = reinterpret_cast<AddHackDictionaryFn>(addr);
        SMOAP_LOG_INFO("addHackDictionary resolved @ 0x%lx",
                       static_cast<unsigned long>(addr));
    }
    addr = hk::ro::lookupSymbol(smoap::sym::kGameDataFunctionIsExistInHackDictionary);
    if (addr == 0) {
        SMOAP_LOG_ERROR("isExistInHackDictionary lookup FAILED");
    } else {
        s_isExistInHackDictionary = reinterpret_cast<IsExistInHackDictionaryFn>(addr);
        SMOAP_LOG_INFO("isExistInHackDictionary resolved @ 0x%lx",
                       static_cast<unsigned long>(addr));
    }
}

}  // namespace smoap::game
