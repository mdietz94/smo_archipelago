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

// Always-unlocked captures whose dict entries we pre-populate at scene load,
// independent of AP grants. These are captures the apworld deliberately
// EXCLUDES from the AP item pool (so they're free / always-available) but
// the in-game compendium would otherwise stay empty until the player
// organically encountered them. Pre-populating makes the compendium honest:
// if AP isn't gating them, they should show up from the start.
//
// Raw SMO hack names (cross-checked against capture_map.json):
//   "Frog"        — cap_name "Frog" (Cap Kingdom)
//   "ElectricWire"— cap_name "Spark pylon" (Wooded Kingdom)
//   "Koopa"       — cap_name "Bowser" (Moon Kingdom escape sequence). Excluded
//                   from the AP pool because the capture has no gameplay role
//                   outside the forced post-boss escape — keeping it AP-locked
//                   would softlock any player who reached the escape without
//                   the item (you can't back out of the throne-room cutscene).
//
// captureBitFor() returns 0xff for these (they're not in capture_table.h),
// so AddHackDictionaryHook::captureBlocked() returns false and our writes
// pass through Orig naturally. If the user organically captures one before
// the reconciler ever runs, the same hook lets the organic write through —
// idempotent either way.
inline constexpr std::array<std::string_view, 3> kBaselineHacks = {
    "Frog",
    "ElectricWire",
    "Koopa",
};

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
    // M6 phase C: walk kCaptureHackNames (the auto-generated authoritative
    // list of every hack we care about) and probe isExistInHackDictionary for
    // each. Reuses the M6-phase-B-resolved s_isExistInHackDictionary fn ptr.
    // No allocation, no new symbols, no thread issues.
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
        // kCaptureHackNames entries are constructed from string literals,
        // which are NUL-terminated, so .data() is a safe const char*.
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
    // Scene-loaded gate. GameDataHolder.mGameDataFile (offset 0x20) is a
    // file-slot pointer that flips when the user navigates from title /
    // file-select into actual gameplay. Writes that land while we're still
    // on title go into the transient file slot active there; once the user
    // loads a save, that slot is swapped for the loaded-save's file and our
    // earlier writes are gone. Symptom in the 2026-05-18 Ryujinx log:
    //   0:15.417 isExistInHackDictionary('Killer') -> TRUE (some title slot)
    //   0:35.196 isExistInHackDictionary('Killer') -> FALSE (post-swap)
    //   0:35.199 addHackDictionary OK (but lands in current-slot at title)
    //   0:52.273 [cappy] scene changed last=0 new=0x...  (game scene appears)
    //   0:53.644 in Cap Kingdom — compendium has no Bullet Bill
    // Fix: defer all grants until the scene cache is populated. The pending_
    // capture_grant queue + reconciler tail already handle the retry — bits
    // stay set in captures_unlocked across the deferral, so when the
    // reconciler fires post-scene-load it observes the loaded-save's file
    // (via mGameDataFile-after-swap) and writes there.
    if (!st.scene_cache.load(std::memory_order_relaxed)) {
        SMOAP_LOG_WARN("[m6-capture] dropped: scene not loaded yet "
                       "(cap='%s' hack='%s') — reconciler will retry once "
                       "user enters game",
                       cap_name ? cap_name : "", hack_name);
        return false;
    }
    void* gdh = st.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!gdh) {
        SMOAP_LOG_WARN("[m6-capture] dropped: GameDataHolder not cached yet "
                       "(cap='%s' hack='%s') — reconciler will retry next frame",
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
    // Log before the call so the trampoline's `[m7-dict] FIRE` line
    // appears immediately after; standalone FIRE lines (no preceding
    // grantCapture firing line) are SMO's organic capture path.
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
    // Same scene-loaded gate as grantCapture above — we must not write while
    // the user is at title/file-select because mGameDataFile flips on save
    // load and our writes get stranded. See the timeline in grantCapture's
    // comment for the 2026-05-18 repro.
    if (!s.scene_cache.load(std::memory_order_relaxed)) return;
    void* gdh = s.game_data_holder_cache.load(std::memory_order_relaxed);
    if (!gdh) return;

    GameDataHolderAccessor acc{gdh};
    GameDataHolderWriter   w{gdh};

    // Baseline pre-populate: captures the apworld leaves out of the AP pool
    // (Frog, Spark Pylon as of 2026-05-18). Idempotent — isExist short-
    // circuits after the first frame these write.
    for (const auto& sv : kBaselineHacks) {
        if (sv.empty()) continue;
        const char* hack = sv.data();
        if (s_isExistInHackDictionary(acc, hack)) continue;
        SMOAP_LOG_INFO("[m6-capture] baseline pre-populate hack='%s'", hack);
        s_addHackDictionary(w, hack);
        SMOAP_LOG_INFO("[m6-capture] baseline addHackDictionary OK hack='%s'", hack);
    }

    // AP-granted captures: skip the bitset walk entirely when nothing's set.
    if (s.captures_unlocked.none()) return;

    // captures_unlocked is sized 128 but only ~42 caps exist. Walk the
    // hack-name table directly so unused trailing bits are skipped for free.
    for (std::uint8_t i = 0; i < kCaptureHackNames.size(); ++i) {
        if (!s.captures_unlocked.test(i)) continue;
        const auto& sv = kCaptureHackNames[i];
        if (sv.empty()) continue;
        // string_view from capture_table.h is backed by a literal string and
        // is NUL-terminated; data() is safe to pass to the C-string API.
        const char* hack = sv.data();
        if (s_isExistInHackDictionary(acc, hack)) continue;
        SMOAP_LOG_INFO("[m6-capture] reconcile firing for bit=%u hack='%s'",
                       static_cast<unsigned>(i), hack);
        s_addHackDictionary(w, hack);
        SMOAP_LOG_INFO("[m6-capture] reconcile addHackDictionary OK bit=%u hack='%s'",
                       static_cast<unsigned>(i), hack);
    }
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
