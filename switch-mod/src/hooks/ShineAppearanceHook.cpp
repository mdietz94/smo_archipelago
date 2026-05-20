// Per-classification Power Moon recolor via material-parameter override.
//
// Approach: trampoline `Shine::init`, and after Orig finishes setting up
// the actor's model and materials, write the AP-classification tint
// directly into the body material's color slots. Bypasses the
// `rs::setStageShineAnimFrame` matanim path entirely — disassembly
// + runtime tests on SMO 1.0.0 (2026-05-19/20) showed that
// `Color_fcl` is an emission/highlight matanim (a "moon is glowing red"
// effect), not a body-diffuse-color driver, so frame substitution can
// only animate sparkle hue, never the actual body color.
//
// What works (confirmed by visual bisection 2026-05-20):
//   - 2D ShineDot (type=1) body color is driven by
//     `BodyMT00.uniform0_mul_color` (+ its `enable_*` gate).
//   - 3D Shine (type=0) + ShineGrand (type=2) body color is driven by a
//     COMBINATION of slots on material `BodyMT`:
//       * `base_color_mul_color` (+ gate) — uncollected body tint
//       * `uniform0_mul_color` / `uniform1_mul_color` (+ gates) — main saturation
//       * `const_color0` — collected/grey variant
//     Each shine variant's shader samples a different subset, so writing
//     the same tint into all four slots covers every state we observed
//     (uncollected, collected, scenario-locked, grand). SDK silently
//     no-ops slots the shader doesn't sample, so over-writing is cheap.
//
// Palette is keyed by AP classification (filler/progression/useful/trap)
// and looked up by BYML UniqueId, not the runtime list-index that lives
// on the Shine actor at +0x290 (that's a per-scenario position into
// `GameDataFile::mShineHintList`, NOT a BYML UniqueId — same translation
// the inline-patch path used pre-2026-05-20).

#include "lib.hpp"
#include "lib/nx/nx.h"           // Result, R_FAILED
#include "nn/ro.h"               // nn::ro::LookupSymbol
#include "../ap/ApState.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

#include <cstdint>

namespace smoap::hooks {

namespace {

// sead::Color4f mirror — 4 floats RGBA, std-layout. SDK setter takes
// `const sead::Color4f&`; passing the address of one of these works at
// the AArch64 ABI level since we don't link against sead headers.
struct Color4f {
    float r, g, b, a;
};

// Production palette. Indices align with the apworld's ColorsConfig
// (filler=0, progression=1, useful=2, trap=3). Tints multiply against
// the moon's base yellow texture, so saturated channels >1.0 push the
// hue past clipping for clearer reads. Filler stays at identity so
// unscouted moons look vanilla.
// Two palettes — same hues, slightly different intensities.
//
// 3D/Grand variants: their shader composes the override through several
// material slots (base_color_mul_color + uniform0/1_mul_color +
// const_color0), so the same RGBA reads less saturated than on 2D. Use
// a ~10%-softened tint to keep the look clearly per-classification
// without HDR clipping artifacts.
//
// 2D ShineDot: a single dominant slot (uniform0_mul_color on BodyMT00)
// drives the visible color, which makes the same RGBA produce a more
// intense color on the flat 2D-camera body. Use ~20%-softened tints so
// the perceived saturation matches the 3D look.
// 3D/Grand palette: softened 10% toward identity (sat sat sat → easier
// on the eye while still clearly per-classification).
constexpr Color4f kPaletteColors3D[5] = {
    {1.00f, 1.00f, 1.00f, 1.0f},  // 0 = filler      — identity (vanilla)
    {0.28f, 2.80f, 0.28f, 1.0f},  // 1 = progression — green
    {0.28f, 1.90f, 2.80f, 1.0f},  // 2 = useful      — cyan/blue
    {2.80f, 0.28f, 0.28f, 1.0f},  // 3 = trap        — red
    {0.55f, 1.00f, 0.55f, 1.0f},  // 4 = reserved
};
constexpr Color4f kPaletteColorsDot[5] = {
    {1.00f, 1.00f, 1.00f, 1.0f},  // 0 = filler      — identity (vanilla)
    {0.36f, 2.60f, 0.36f, 1.0f},  // 1 = progression — green
    {0.36f, 1.80f, 2.60f, 1.0f},  // 2 = useful      — cyan/blue
    {2.60f, 0.36f, 0.36f, 1.0f},  // 3 = trap        — red
    {0.60f, 1.00f, 0.60f, 1.0f},  // 4 = reserved
};

inline const Color4f& shinePaletteColor(int shine_type, std::size_t pal_idx) {
    return shine_type == 1
        ? kPaletteColorsDot[pal_idx]
        : kPaletteColors3D[pal_idx];
}

// Per-shine-type body material name. Runtime-probed:
//   type=0 (3D Shine)     -> "BodyMT"   (single model, single material)
//   type=1 (ShineDot, 2D) -> "BodyMT00" (joined Shine00__BodyMT00 form)
//   type=2 (ShineGrand)   -> "BodyMT"   (same as 3D)
inline constexpr const char kShineMaterialName_3D[]  = "BodyMT";
inline constexpr const char kShineMaterialName_Dot[] = "BodyMT00";

inline const char* shineMaterialNameForType(int shine_type) {
    return shine_type == 1 ? kShineMaterialName_Dot : kShineMaterialName_3D;
}

// Shine actor layout offsets (SMO 1.0.0).
//   +0x290 = mShineId       — list-INDEX into mShineHintList, NOT BYML UniqueId
//   +0x1a0 = mShineType     — 0 normal, 1 ShineDot, 2 ShineGrand
inline constexpr std::size_t kShineMShineIdxOffset = 0x290;
inline constexpr std::size_t kShineMTypeOffset     = 0x1a0;

// GameData layout offsets (same constants as MoonApply.cpp's snapshot
// enumerator; cited from lunakit-vendor headers there).
inline constexpr std::size_t kGameDataHolder_mGameDataFileOffset = 0x20;
inline constexpr std::size_t kGameDataFile_mShineHintListOffset  = 0x9A0;
inline constexpr std::size_t kHintInfo_Size                      = 0x238;
inline constexpr std::size_t kHintInfo_UniqueIdOffset            = 0x1F0;
inline constexpr int         kShineHintListMaxIndex              = 0x400;

// Resolve a runtime mShineHintList index to its BYML UniqueId by walking
// GameDataHolder → GameDataFile → mShineHintList[index]. Returns -1 if
// any pointer in the chain isn't yet populated (early scene init).
// Read-only — no allocation, safe inside a hook callback.
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

// Resolved SDK setters. Missing-symbol is non-fatal — the trampoline
// short-circuits when any of these is null.
using SetMaterialProgrammableFn      = void (*)(void* actor);
using SetModelMaterialParameterRgbaFn = void (*)(
    const void* actor, const char* mat, const char* param, const Color4f&);
using SetModelMaterialParameterF32Fn  = void (*)(
    const void* actor, const char* mat, const char* param, float v);
using IsExistMaterialFn               = bool (*)(const void* actor, const char* name);
using IsExistModelFn                  = bool (*)(const void* actor);
SetMaterialProgrammableFn       s_setMaterialProgrammable       = nullptr;
SetModelMaterialParameterRgbaFn s_setModelMaterialParameterRgba = nullptr;
SetModelMaterialParameterF32Fn  s_setModelMaterialParameterF32  = nullptr;
IsExistMaterialFn               s_isExistMaterial               = nullptr;
IsExistModelFn                  s_isExistModel                  = nullptr;

// Write the same tint into the gated + the ungated parameter slots that
// the shine shader is known to sample. Each shine variant (uncollected,
// grand, collected/grey) reads a different subset; over-writing all
// covers everything we observed. SDK silently no-ops on slots the shader
// doesn't sample, so this is cheap (~6 calls) and robust.
void writeBodyTint(void* actor, const char* mat_name, const Color4f& tint,
                   bool is_dot) {
    s_setMaterialProgrammable(actor);
    if (s_setModelMaterialParameterF32 != nullptr) {
        s_setModelMaterialParameterF32(actor, mat_name, "enable_uniform0_mul_color", 1.0f);
        if (!is_dot) {
            // 3D/Grand shines also read these slots; ShineDot's shader
            // doesn't, so the F32 writes would be no-ops anyway — skip
            // to keep the per-shine SDK-call count minimal.
            s_setModelMaterialParameterF32(actor, mat_name, "enable_base_color_mul_color", 1.0f);
            s_setModelMaterialParameterF32(actor, mat_name, "enable_uniform1_mul_color",   1.0f);
        }
    }
    s_setModelMaterialParameterRgba(actor, mat_name, "uniform0_mul_color", tint);
    if (!is_dot) {
        s_setModelMaterialParameterRgba(actor, mat_name, "base_color_mul_color", tint);
        s_setModelMaterialParameterRgba(actor, mat_name, "uniform1_mul_color",   tint);
        s_setModelMaterialParameterRgba(actor, mat_name, "const_color0",         tint);
    }
}

HOOK_DEFINE_TRAMPOLINE(ShineInitColorOverride) {
    static void Callback(void* self, const void* init_info) {
        Orig(self, init_info);
        if (!self) return;
        if (s_setMaterialProgrammable == nullptr ||
            s_setModelMaterialParameterRgba == nullptr) return;

        const auto* shine = reinterpret_cast<const std::uint8_t*>(self);
        const int shine_type = *reinterpret_cast<const int*>(
            shine + kShineMTypeOffset);
        const int index = *reinterpret_cast<const int*>(
            shine + kShineMShineIdxOffset);
        const int unique_id = resolveShineIndexToUniqueId(index);
        if (unique_id <= 0 ||
            static_cast<std::size_t>(unique_id) >=
                smoap::ap::ApState::kMaxShineUid) {
            return;
        }

        const std::uint8_t pal =
            smoap::ap::ApState::instance().getShinePalette(unique_id);
        if (pal == smoap::ap::ApState::kNoPaletteOverride) return;
        const std::size_t pal_idx = pal < 5 ? pal : 0;

        // Required model-presence guard. Some Shine::init paths complete
        // without allocating mModelKeeper — confirmed for the linked-Shine
        // inside AppearSwitchTimer when re-entering Cascade after the
        // first multi-moon (scenario reload spawns the already-collected
        // shine as a stub). isExistMaterial, setMaterialProgrammable, and
        // setModelMaterialParameter* all deref the model keeper without a
        // null check and crash. isExistModel is the canonical null-safe
        // probe (used the same way in OdysseyDecomp's AppearSwitchTimer).
        if (s_isExistModel == nullptr || !s_isExistModel(self)) return;

        const char* mat_name = shineMaterialNameForType(shine_type);

        // Probe-before-write — setModelMaterialParameter dereferences a
        // NULL material if the name doesn't exist (no SDK bounds check).
        // The runtime-probed names BodyMT / BodyMT00 work for the SMO
        // 1.0.0 ShineDot/Shine/ShineGrand archives, but the guard means
        // a future SMO build with a renamed material logs and skips
        // instead of crashing.
        if (s_isExistMaterial != nullptr &&
                !s_isExistMaterial(self, mat_name)) {
            static bool s_warned[3] = {false, false, false};
            if (shine_type >= 0 && shine_type < 3 &&
                    !s_warned[shine_type]) {
                s_warned[shine_type] = true;
                SMOAP_LOG_WARN("[shine-color] type=%d has no material '%s' — "
                               "override disabled for this type",
                               shine_type, mat_name);
            }
            return;
        }

        writeBodyTint(self, mat_name,
                      shinePaletteColor(shine_type, pal_idx),
                      /*is_dot=*/shine_type == 1);

        // Sample the first 8 overrides into the log so a session-start
        // glance confirms the path is wired. Stays quiet after that.
        static int s_logged = 0;
        if (s_logged < 8) {
            SMOAP_LOG_INFO("[shine-color] override#%d type=%d unique_id=%d palette=%u",
                           s_logged + 1, shine_type, unique_id,
                           static_cast<unsigned>(pal));
            ++s_logged;
        }
    }
};

template <typename FnPtr>
inline void resolveSymbol(const char* mangled, FnPtr& out, const char* label) {
    uintptr_t addr = 0;
    const Result rc = nn::ro::LookupSymbol(&addr, mangled);
    if (R_FAILED(rc)) {
        SMOAP_LOG_ERROR("%s lookup FAILED rc=0x%x", label, rc);
        out = nullptr;
        return;
    }
    out = reinterpret_cast<FnPtr>(addr);
    SMOAP_LOG_INFO("%s resolved @ 0x%lx", label, addr);
}

}  // namespace

void installShineAppearanceHook() {
    resolveSymbol(smoap::sym::kAlSetMaterialProgrammable,
                  s_setMaterialProgrammable, "setMaterialProgrammable");
    resolveSymbol(smoap::sym::kAlSetModelMaterialParameterRgba,
                  s_setModelMaterialParameterRgba, "setModelMaterialParameterRgba");
    resolveSymbol(smoap::sym::kAlSetModelMaterialParameterF32,
                  s_setModelMaterialParameterF32, "setModelMaterialParameterF32");
    resolveSymbol(smoap::sym::kAlIsExistMaterial,
                  s_isExistMaterial, "isExistMaterial");
    resolveSymbol(smoap::sym::kAlIsExistModel,
                  s_isExistModel, "isExistModel");

    if (s_setMaterialProgrammable != nullptr &&
        s_setModelMaterialParameterRgba != nullptr) {
        SMOAP_LOG_INFO("installing ShineInitColorOverride -> %s",
                       smoap::sym::kShineInit);
        softInstallAtSymbol<ShineInitColorOverride>(smoap::sym::kShineInit);
    }
}

}  // namespace smoap::hooks
