// Crazy Cap shop moon-label substitution.
//
// PATTERN: same primitive as CreditsStartHook — `hk::hook::writeBranchLink
// AtMainOffset` overwrites a single BL with a BL to our callback. Patches two
// BL sites inside `ShopLayoutInfo::updateItemPartsData(...)` that vanilla
// SMO uses to fetch the localized name for each shop slot:
//
//   0x2089C4  BL al::getSystemMessageString
//   0x208A44  BL al::getSystemMessageString
//
// Offsets are verbatim from Kgamer77/SuperMarioOdysseyArchipelago's
// `Mod/patches/codehook.slpatch`. Both target SMO 1.0.0 main.nso (build-id
// `3ca12dfaaf9c82da064d1698df79cda1`, the same we pin against). We can't
// re-verify by static disassembly here (main.nso is gitignored / extracted
// per-machine), so installer asserts the instruction shape is a BL before
// patching — refusing + logging if not, so a future ABI drift can't silently
// corrupt random code.
//
// CALLBACK shape: `al::getSystemMessageString(messageSystem, fileName, key)`
// returns `const char16_t*` (a pointer into SMO's MSBT-loaded localization
// arena — null-terminated UTF-16). Substituting just means returning a
// pointer to OUR UTF-16 buffer instead. Misses fall through to the original
// SDK lookup so non-moon shop slots (costumes, stickers, souvenirs, the
// "Welcome" greeting) render unchanged.
//
// FONT: the shop UI uses a different layout/font than the Cappy speech
// bubble path, but in absence of separate font coverage data we assume
// MessageFont38 coverage as the conservative floor. writeShopLabels runs
// inputs through smoap::util::sanitizeForMsgFont before stowing.
//
// DISCOVERY: the actual (fileName, key) the shop UI uses are observed
// empirically — the callback logs each unique pair once via SMOAP_LOG_INFO
// (de-duped by a 256-slot FlatHashSet so a long shop walk doesn't flood).
// First Ryujinx Crazy Cap visit reveals the keys; the bridge's hard-coded
// {kingdom → (fileName, key)} dict is populated from those logs.
//
// CHANNEL-A PARITY: format/sanitize convention matches MoonLabelHook so
// the cutscene text and shop slot show the same AP-aware string.

#include "hk/hook/InstrUtil.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <atomic>
#include <cstdint>
#include <cstring>

#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::hooks {

namespace {

// BL offsets inside SMO 1.0.0 main.nso (ShopLayoutInfo::updateItemPartsData).
inline constexpr ptrdiff_t kShopMsgPatchA = 0x2089C4;
inline constexpr ptrdiff_t kShopMsgPatchB = 0x208A44;

// AArch64 unconditional branch-link opcode: top 6 bits = 0b100101 (37).
// Reuses the same shape Hakkun's InstrUtil.h emits for `writeBranchLink*`.
inline bool isBranchLinkInstr(std::uint32_t instr) {
    return ((instr >> 26) & 0b111111u) == 0b100101u;
}

// One-shot per-(file_name, key) log dedupe so a long shop session doesn't
// flood the Switch tab. 256 slots is comfortable headroom — a single shop's
// updateItemPartsData fires once per visible slot, so the user would have
// to enter dozens of distinct shops to fill it. Power-of-two for
// FlatHashSet's open-addressing.
smoap::ap::FlatHashSet<256> g_logged_keys;

std::uint64_t hashStrings(const char* a, const char* b) {
    std::uint64_t h = 0xcbf29ce484222325ULL;
    for (; *a; ++a) { h ^= static_cast<std::uint8_t>(*a); h *= 0x100000001b3ULL; }
    h ^= '\x1f'; h *= 0x100000001b3ULL;
    for (; *b; ++b) { h ^= static_cast<std::uint8_t>(*b); h *= 0x100000001b3ULL; }
    return h;
}

// Resolved at install time — the BL we replace points to
// `al::getSystemMessageString`. We grab the target by computing it from the
// instruction's signed imm26 displacement before overwriting, so the
// fallback path doesn't depend on the SDK symbol being in our .sym file.
using GetSystemMessageStringFn = const char16_t* (*)(const void*, const char*, const char*);
std::atomic<GetSystemMessageStringFn> g_orig_get_system_message_string{nullptr};

// Decode the BL at `addr` and return its target address (the function the
// branch-link calls). Returns 0 if `addr` isn't a BL.
std::uintptr_t resolveBranchLinkTarget(std::uintptr_t addr) {
    const std::uint32_t instr = *reinterpret_cast<std::uint32_t*>(addr);
    if (!isBranchLinkInstr(instr)) return 0;
    // BL imm26 is sign-extended, ×4, relative to the BL instruction PC.
    std::int32_t imm26 = static_cast<std::int32_t>(instr & 0x03FFFFFFu);
    if (imm26 & 0x02000000) imm26 |= 0xFC000000;  // sign-extend bit 25
    return addr + static_cast<std::int64_t>(imm26) * 4;
}

const char16_t* apGetShopItemMessage(const void* messageSystem,
                                      const char* fileName,
                                      const char* key) {
    // Defensive — the SDK wouldn't normally pass null but a stray BL
    // intercept could; never crash from the hook.
    if (!fileName) fileName = "";
    if (!key) key = "";

    // First sighting? Log it so the bridge can populate the substitution
    // table. De-dupe globally so we don't spam.
    const std::uint64_t h = hashStrings(fileName, key);
    if (g_logged_keys.tryInsert(h)) {
        SMOAP_LOG_INFO("[shop-discovery] file='%s' key='%s'", fileName, key);
    }

    if (const char16_t* override_label =
            smoap::ap::ApState::instance().lookupShopLabel(fileName, key)) {
        return override_label;
    }

    auto orig = g_orig_get_system_message_string.load(std::memory_order_acquire);
    if (!orig) {
        // Installer should always set this before patching; falling back to
        // a static empty string keeps the shop UI alive rather than crashing.
        static const char16_t kFallback[] = u"";
        SMOAP_LOG_WARN("[shop] orig al::getSystemMessageString unresolved — "
                       "returning empty fallback for file='%s' key='%s'",
                       fileName, key);
        return kFallback;
    }
    return orig(messageSystem, fileName, key);
}

bool installAtOffset(ptrdiff_t offset, const char* tag) {
    auto* main = hk::ro::getMainModule();
    if (!main) {
        SMOAP_LOG_WARN("[shop] %s @+0x%lx skipped — main module unresolved",
                       tag, static_cast<unsigned long>(offset));
        return false;
    }
    const std::uintptr_t pc = main->range().start() + offset;
    const std::uint32_t instr = *reinterpret_cast<std::uint32_t*>(pc);
    if (!isBranchLinkInstr(instr)) {
        SMOAP_LOG_WARN("[shop] %s @+0x%lx is NOT a BL (instr=0x%08x) — refusing "
                       "to patch (would corrupt random code); offset drift "
                       "vs SMO 1.0.0?",
                       tag, static_cast<unsigned long>(offset), instr);
        return false;
    }

    // Capture the original target so the fallback can call through. First
    // resolve wins — both patch sites should land on the same SDK function.
    GetSystemMessageStringFn current = g_orig_get_system_message_string.load(
        std::memory_order_relaxed);
    if (!current) {
        const std::uintptr_t target = resolveBranchLinkTarget(pc);
        if (target) {
            auto fn = reinterpret_cast<GetSystemMessageStringFn>(target);
            g_orig_get_system_message_string.store(fn,
                std::memory_order_release);
            SMOAP_LOG_INFO("[shop] resolved orig al::getSystemMessageString "
                           "via %s @+0x%lx -> %p",
                           tag, static_cast<unsigned long>(offset),
                           reinterpret_cast<void*>(target));
        }
    }

    auto rc = hk::hook::writeBranchLinkAtMainOffset(offset,
        &apGetShopItemMessage);
    if (rc.failed()) {
        SMOAP_LOG_WARN("[shop] %s @+0x%lx writeBranchLinkAtMainOffset FAILED",
                       tag, static_cast<unsigned long>(offset));
        return false;
    }
    SMOAP_LOG_INFO("[shop] %s @+0x%lx BL re-pointed to apGetShopItemMessage",
                   tag, static_cast<unsigned long>(offset));
    return true;
}

}  // namespace

void installShopItemMessageHook() {
    const bool a = installAtOffset(kShopMsgPatchA, "patchA");
    const bool b = installAtOffset(kShopMsgPatchB, "patchB");
    if (!a || !b) {
        SMOAP_LOG_WARN("[shop] partial install — labels won't substitute "
                       "consistently (a=%d b=%d). Shop UI still works "
                       "(fallback path returns vanilla text).",
                       static_cast<int>(a), static_cast<int>(b));
    } else {
        SMOAP_LOG_INFO("[shop] ShopItemMessageHook installed at both BL sites");
    }
}

}  // namespace smoap::hooks
