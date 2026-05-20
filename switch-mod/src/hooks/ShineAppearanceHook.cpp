// Per-shine palette override via inline patches at 4 BL call sites inside
// Shine::init. Matches Kgamer77/SuperMarioOdysseyArchipelago's technique
// (MIT, codehook.slpatch) on SMO 1.0.0 — they redirect each BL to a wrapper;
// we use exlaunch's HOOK_DEFINE_INLINE to intercept before the BL fires and
// modify the color arg register in place.
//
// Why inline patches, not a symbol hook on rs::setStageShineAnimFrame?
// That function is called from MULTIPLE actor types (Shine AND
// ShineTowerRocket, observed live). Reading Shine-class fields off a
// non-Shine actor crashed in StageScene init. Patching inside Shine::init's
// body guarantees the parent IS a Shine.
//
// 1.0.0 offsets (verified against real main.nso disassembly 2026-05-19):
//   0x1cdce4 -> BL rs::setStageShineAnimFrame   (Mtp anim, X0 = Shine self)
//   0x1cdd3c -> BL rs::setStageShineAnimFrame   (Mtp anim, X0 = child actor)
//   0x1cddcc -> BL rs::setStageShineAnimFrame   (Mcl anim, X0 = Shine self)
//   0x1cde24 -> BL rs::setStageShineAnimFrame   (Mcl anim, X0 = child actor)
//
// Each shine fires exactly 2 of 4 sites: one Mtp + one Mcl. Which member of
// each pair runs depends on a per-shine branch inside Shine::init that picks
// between "apply on the Shine's own model" and "apply on a child LiveActor
// stored at [Shine + 0x2e8]" (the latter is the 2D ShineDot 'Dot' model
// holder). The X0 of the BL therefore varies between Shine and child.
//
// At each site, the AArch64 ABI has:
//   X0 = LiveActor* (Shine OR child — depends on site, see above)
//   X1 = const char* stageName
//   W2 = int color           <-- substitute this
//   W3 = bool isMatAnim
//
// Reading mShineId off X0 worked at 0x1cdce4 / 0x1cddcc (X0 = Shine) but was
// a buffer over-read past the child's 264-byte size at 0x1cdd3c / 0x1cde24.
// Fix in one direction: read the parent Shine* out of X19 instead.
// Shine::init's prologue does `mov x19, x0` at 0x1cd50c and x19 is
// callee-saved on the stack, so X19 holds the original Shine* across the
// whole function body — including every one of the 4 BL sites.
//
// SEPARATE issue (the keyspace bug discovered 2026-05-19): the value at
// [Shine + 0x290] is NOT the BYML's UniqueId. Disassembly at 0x1cd628 shows
// it is the return value of `GameDataFunction::tryFindShineIndex(actor,
// initInfo)` — a per-scene LIST INDEX into GameDataFile::mShineHintList.
// The bridge populates `shine_palette[]` keyed by UniqueId (the BYML field
// our scripts/extract_shine_map.py pulls), so the two identifiers don't
// share a keyspace and every lookup missed except by coincidence.
//
// Real fix: at hook time, treat [Shine + 0x290] as an index, walk
// GameDataHolder → GameDataFile → mShineHintList[index] → UniqueId, then
// use that UniqueId for the palette lookup. The walk is read-only (no
// allocations) so it's safe inside the inline callback. Layout constants
// (GDH+0x20 = GameDataFile*, GDF+0x9A0 = mShineHintList, HintInfo size
// 0x238, UniqueId at +0x1F0) are the same ones MoonApply.cpp's snapshot
// enumerator uses — see comments there for the lunakit-header citations.

#include "lib.hpp"  // HOOK_DEFINE_INLINE, exl::hook::InlineCtx
#include "../ap/ApState.hpp"
#include "../util/Log.hpp"
#include "SoftInstall.hpp"

#include <cstdint>

namespace smoap::hooks {

namespace {

// Field at Shine + 0x290 — NOT the BYML UniqueId despite Kgamer77's repo
// comment claiming so. Disassembly of Shine::init (1.0.0) shows the value
// stored here comes from `GameDataFunction::tryFindShineIndex(actor,
// initInfo)` at 0x1cd628, which returns a position in
// GameDataFile::mShineHintList. Translate to a real UniqueId via that list
// before using it as a palette key — see the file-header comment.
inline constexpr std::size_t kShineMShineIdxOffset = 0x290;

// GameDataHolder layout — first non-vtable field is GameDataFile*.
// Source: lunakit-vendor/src/game/GameData/GameDataHolder.h:94.
inline constexpr std::size_t kGameDataHolder_mGameDataFileOffset = 0x20;

// GameDataFile::mShineHintList — HintInfo* (array, sparse — unused slots
// have UniqueId == 0). Source: lunakit GameDataFile.h:463.
inline constexpr std::size_t kGameDataFile_mShineHintListOffset = 0x9A0;

// HintInfo: 0x238 bytes, UniqueId at +0x1F0. Source: lunakit
// GameDataFile.h:57-83 + static_assert at line 86.
inline constexpr std::size_t kHintInfo_Size = 0x238;
inline constexpr std::size_t kHintInfo_UniqueIdOffset = 0x1F0;

// Defensive upper bound for the index → UniqueId walk: lunakit's own
// `findShine(int)` scans 0x400 entries unconditionally (GameDataFile.h:
// 362-369), so mShineHintList is sized for ≥ 0x400. Indices above that
// are almost certainly junk reads.
inline constexpr int kShineHintListMaxIndex = 0x400;

// Returns the BYML UniqueId for an mShineHintList position, or -1 if any
// pointer in the chain (GDH cache, GDF, list base) isn't yet populated.
// Read-only — no allocation, safe inside an inline hook callback.
inline int resolveShineIndexToUniqueId(int index) {
    if (index < 0 || index >= kShineHintListMaxIndex) return -1;
    void* gdh = smoap::ap::ApState::instance().game_data_holder_cache.load(
        std::memory_order_relaxed);
    if (!gdh) return -1;
    const auto* gdf = *reinterpret_cast<const void* const*>(
        reinterpret_cast<const std::uint8_t*>(gdh)
            + kGameDataHolder_mGameDataFileOffset);
    if (!gdf) return -1;
    const auto* hint_base = *reinterpret_cast<const std::uint8_t* const*>(
        reinterpret_cast<const std::uint8_t*>(gdf)
            + kGameDataFile_mShineHintListOffset);
    if (!hint_base) return -1;
    return *reinterpret_cast<const int*>(
        hint_base + index * kHintInfo_Size + kHintInfo_UniqueIdOffset);
}

// 1.0.0 BL call sites Kgamer77 patches in Shine::init. Same 4 offsets
// applied to our exlaunch InlineHook give us the same effect: substitute
// the color arg right before the BL fires.
inline constexpr ptrdiff_t kShineColorPatchOffsets[] = {
    0x1cdce4, 0x1cdd3c, 0x1cddcc, 0x1cde24,
};

HOOK_DEFINE_INLINE(ShineInitColorPatch) {
    static void Callback(exl::hook::InlineCtx* ctx) {
        // X19 holds the parent Shine* across all 4 patch sites — Shine::init's
        // prologue stashes the first arg there. X0 at the BL may be either
        // the Shine itself OR a child LiveActor at [Shine + 0x2e8] depending
        // on the site / per-shine branch (see the file-header comment).
        const auto* parent = reinterpret_cast<const std::uint8_t*>(ctx->X[19]);
        if (!parent) return;

        // Two-step lookup: read the list-INDEX from [Shine + 0x290], then
        // resolve through mShineHintList[index].UniqueId to get the key the
        // bridge actually populated `shine_palette[]` with.
        const int index = *reinterpret_cast<const int*>(
            parent + kShineMShineIdxOffset);
        const int unique_id = resolveShineIndexToUniqueId(index);
        if (unique_id <= 0 ||
            static_cast<std::size_t>(unique_id) >= smoap::ap::ApState::kMaxShineUid) {
            return;
        }
        const std::uint8_t pal = smoap::ap::ApState::instance().getShinePalette(unique_id);
        if (pal == smoap::ap::ApState::kNoPaletteOverride) return;

        // Per-shine, each Shine::init fires 2 of the 4 patches (one Mtp +
        // one Mcl), so 2 fires per moon is the natural rate — 16 covers ~8
        // shines.
        static int s_subst_count = 0;
        if (s_subst_count < 16) {
            SMOAP_LOG_INFO("[shine-color] subst#%d shine=%p unique_id=%d palette=%u",
                           s_subst_count + 1, ctx->X[19], unique_id,
                           static_cast<unsigned>(pal));
        }
        ++s_subst_count;
        ctx->W[2] = pal;  // substitute the color arg (zero-extends X2)
    }
};

}  // namespace

void installShineAppearanceHook() {
    for (ptrdiff_t off : kShineColorPatchOffsets) {
        SMOAP_LOG_INFO("installing ShineInitColorPatch @ +0x%lx",
                       static_cast<unsigned long>(off));
        ShineInitColorPatch::InstallAtOffset(off);
    }
}

}  // namespace smoap::hooks
