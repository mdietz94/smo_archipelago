// M4: cap-name lookup via the generated capture_table.h. The bit index for a
// given cap matches its position in apworld/data/items.json (Capture category)
// so the Switch and bridge cannot drift on assignment.
//
// M6 phase B: grantCapture wires the inbound-item path to GameDataFunction::
// addHackDictionary. Symbols are resolved once at module init via
// nn::ro::LookupSymbol and stored as function pointers (same pattern as
// CaptureStartHook's getCurrentHackName).

#include "CaptureGate.hpp"

#include <cstring>

#include "lib/nx/nx.h"          // Result, R_FAILED
#include "nn/ro.h"              // nn::ro::LookupSymbol
#include "../ap/ApState.hpp"
#include "../ap/capture_table.h"  // kCaptureNames, kCaptureHackNames
#include "../hooks/HookSymbols.hpp"
#include "../util/Log.hpp"

// Minimal layout mirrors. Both wrappers are 1-pointer non-trivial classes
// (GameDataHolderAccessor inherits from GameDataHolderWriter; Writer has one
// GameDataHolder* member). Itanium ABI passes them in x0 by value. Brace-
// init the struct from a GameDataHolder* and call.
struct GameDataHolderWriter   { void* mData; };
struct GameDataHolderAccessor { void* mData; };

namespace smoap::game {

namespace {

using AddHackDictionaryFn      = void (*)(GameDataHolderWriter, const char*);
using IsExistInHackDictionaryFn = bool (*)(GameDataHolderAccessor, const char*);

AddHackDictionaryFn       s_addHackDictionary       = nullptr;
IsExistInHackDictionaryFn s_isExistInHackDictionary = nullptr;

}  // namespace

std::uint8_t captureBitFor(const char* cap_name) {
    if (!cap_name) return 0xff;
    // Called from two paths with two name spaces:
    //   - ApState applyOnFrame passes item.cap (apworld English name from items.json)
    //   - CaptureStartHook passes getCurrentHackName() (SMO-internal hack_name)
    // Search kCaptureHackNames first because that's the hot path (every
    // capture attempt). Identity entries make this redundant for the ~36
    // 1:1 caps; for the ~6 diverged caps (TRex/T-Rex, Wanwan/Chain Chomp,
    // ElectricWire/Spark Pylon, KuriboWing/Paragoomba, ...) only this
    // table matches. Then fall back to kCaptureNames for the apworld path.
    //
    // string_views back literal strings (NUL-terminated) but we rely on
    // length+memcmp for correctness regardless. const char* signature is
    // M6.1 allocator-hardening — std::string in this TU would NULL-deref
    // libstdc++ on the worker recv path.
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
    if (bit == 0xff) return false;  // unknown -> don't block (fail open)
    return !smoap::ap::ApState::instance().captures_unlocked.test(bit);
}

std::string nameForHackData(/* const PlayerHackData* data */) {
    return {};  // M5
}

void playSE_NG() {
    // al::startSe(/* SE_NG */, /* ... */);
    SMOAP_LOG_INFO("playSE_NG (stub)");
}

void enumerateOwnedCaptures(CaptureEnumerationCallback cb, void* ctx) {
    // M5/M6 will iterate the player's used-capture record from GameDataHolder
    // and invoke cb with each raw hack_name. Stub for M4.5 — empty snapshot
    // is harmless.
    (void)cb;
    (void)ctx;
}

void grantCapture(const char* cap_name, const char* hack_name) {
    if (!hack_name || !*hack_name) {
        SMOAP_LOG_WARN("[m6-capture] dropped: empty hack_name (cap='%s')",
                       cap_name ? cap_name : "");
        return;
    }
    if (!s_addHackDictionary || !s_isExistInHackDictionary) {
        SMOAP_LOG_WARN("[m6-capture] dropped: symbols unresolved "
                       "(cap='%s' hack='%s')",
                       cap_name ? cap_name : "", hack_name);
        return;
    }
    void* gdh = smoap::ap::ApState::instance().game_data_holder_cache.load(
        std::memory_order_relaxed);
    if (!gdh) {
        SMOAP_LOG_WARN("[m6-capture] dropped: GameDataHolder not cached yet "
                       "(cap='%s' hack='%s')",
                       cap_name ? cap_name : "", hack_name);
        return;
    }
    GameDataHolderAccessor acc{gdh};
    if (s_isExistInHackDictionary(acc, hack_name)) {
        SMOAP_LOG_INFO("[m6-capture] already in dictionary cap='%s' hack='%s'",
                       cap_name ? cap_name : "", hack_name);
        return;
    }
    GameDataHolderWriter w{gdh};
    // Log before the call so the trampoline's `[m7-dict] FIRE` line
    // appears immediately after; standalone FIRE lines (no preceding
    // grantCapture firing line) are SMO's organic capture path.
    SMOAP_LOG_INFO("[m6-capture] grantCapture firing cap='%s' hack='%s'",
                   cap_name ? cap_name : "", hack_name);
    s_addHackDictionary(w, hack_name);
    SMOAP_LOG_INFO("[m6-capture] addHackDictionary OK cap='%s' hack='%s'",
                   cap_name ? cap_name : "", hack_name);
}

void installCaptureGrantSymbols() {
    uintptr_t addr = 0;
    Result rc = nn::ro::LookupSymbol(&addr,
        smoap::sym::kGameDataFunctionAddHackDictionary);
    if (R_FAILED(rc)) {
        SMOAP_LOG_ERROR("addHackDictionary lookup FAILED rc=0x%x", rc);
    } else {
        s_addHackDictionary = reinterpret_cast<AddHackDictionaryFn>(addr);
        SMOAP_LOG_INFO("addHackDictionary resolved @ 0x%lx", addr);
    }
    addr = 0;
    rc = nn::ro::LookupSymbol(&addr,
        smoap::sym::kGameDataFunctionIsExistInHackDictionary);
    if (R_FAILED(rc)) {
        SMOAP_LOG_ERROR("isExistInHackDictionary lookup FAILED rc=0x%x", rc);
    } else {
        s_isExistInHackDictionary = reinterpret_cast<IsExistInHackDictionaryFn>(addr);
        SMOAP_LOG_INFO("isExistInHackDictionary resolved @ 0x%lx", addr);
    }
}

}  // namespace smoap::game
