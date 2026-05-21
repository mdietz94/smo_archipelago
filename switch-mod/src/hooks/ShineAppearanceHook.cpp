// Per-classification Power Moon recolor via material-parameter override.
//
// Trampolines Shine::init and writes AP-classification tint directly into
// the body material's color slots. See production switch-mod's
// ShineAppearanceHook.cpp for the full design narrative.

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <cstdint>

#include "../ap/ApState.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"

namespace smoap::hooks {

namespace {

struct Color4f {
    float r, g, b, a;
};

constexpr Color4f kPaletteColors3D[5] = {
    {1.00f, 1.00f, 1.00f, 1.0f},
    {0.28f, 2.80f, 0.28f, 1.0f},
    {0.28f, 1.90f, 2.80f, 1.0f},
    {2.80f, 0.28f, 0.28f, 1.0f},
    {0.55f, 1.00f, 0.55f, 1.0f},
};
constexpr Color4f kPaletteColorsDot[5] = {
    {1.00f, 1.00f, 1.00f, 1.0f},
    {0.36f, 2.60f, 0.36f, 1.0f},
    {0.36f, 1.80f, 2.60f, 1.0f},
    {2.60f, 0.36f, 0.36f, 1.0f},
    {0.60f, 1.00f, 0.60f, 1.0f},
};

inline const Color4f& shinePaletteColor(int shine_type, std::size_t pal_idx) {
    return shine_type == 1 ? kPaletteColorsDot[pal_idx] : kPaletteColors3D[pal_idx];
}

inline constexpr const char kShineMaterialName_3D[]  = "BodyMT";
inline constexpr const char kShineMaterialName_Dot[] = "BodyMT00";

inline const char* shineMaterialNameForType(int shine_type) {
    return shine_type == 1 ? kShineMaterialName_Dot : kShineMaterialName_3D;
}

inline constexpr std::size_t kShineMShineIdxOffset = 0x290;
inline constexpr std::size_t kShineMTypeOffset     = 0x1a0;

inline constexpr std::size_t kGameDataHolder_mGameDataFileOffset = 0x20;
inline constexpr std::size_t kGameDataFile_mShineHintListOffset  = 0x9A0;
inline constexpr std::size_t kHintInfo_Size                      = 0x238;
inline constexpr std::size_t kHintInfo_UniqueIdOffset            = 0x1F0;
inline constexpr int         kShineHintListMaxIndex              = 0x400;

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

using SetMaterialProgrammableFn      = void (*)(void* actor);
using SetModelMaterialParameterRgbaFn = void (*)(
    const void* actor, const char* mat, const char* param, const Color4f&);
using SetModelMaterialParameterF32Fn  = void (*)(
    const void* actor, const char* mat, const char* param, float v);
using IsExistMaterialFn               = bool (*)(const void* actor, const char* name);

SetMaterialProgrammableFn       s_setMaterialProgrammable       = nullptr;
SetModelMaterialParameterRgbaFn s_setModelMaterialParameterRgba = nullptr;
SetModelMaterialParameterF32Fn  s_setModelMaterialParameterF32  = nullptr;
IsExistMaterialFn               s_isExistMaterial               = nullptr;

void writeBodyTint(void* actor, const char* mat_name, const Color4f& tint,
                   bool is_dot) {
    s_setMaterialProgrammable(actor);
    if (s_setModelMaterialParameterF32 != nullptr) {
        s_setModelMaterialParameterF32(actor, mat_name, "enable_uniform0_mul_color", 1.0f);
        if (!is_dot) {
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

HkTrampoline<void, void*, const void*> shineInitColorOverride =
    hk::hook::trampoline([](void* self, const void* init_info) -> void {
        shineInitColorOverride.orig(self, init_info);
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

        const char* mat_name = shineMaterialNameForType(shine_type);

        if (s_isExistMaterial != nullptr && !s_isExistMaterial(self, mat_name)) {
            static bool s_warned[3] = {false, false, false};
            if (shine_type >= 0 && shine_type < 3 && !s_warned[shine_type]) {
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

        static int s_logged = 0;
        if (s_logged < 8) {
            SMOAP_LOG_INFO("[shine-color] override#%d type=%d unique_id=%d palette=%u",
                           s_logged + 1, shine_type, unique_id,
                           static_cast<unsigned>(pal));
            ++s_logged;
        }
    });

template <typename FnPtr>
inline void resolveSymbol(const char* mangled, FnPtr& out, const char* label) {
    const ptr addr = hk::ro::lookupSymbol(mangled);
    if (addr == 0) {
        SMOAP_LOG_ERROR("%s lookup FAILED", label);
        out = nullptr;
        return;
    }
    out = reinterpret_cast<FnPtr>(addr);
    SMOAP_LOG_INFO("%s resolved @ 0x%lx", label, static_cast<unsigned long>(addr));
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

    if (s_setMaterialProgrammable != nullptr &&
        s_setModelMaterialParameterRgba != nullptr) {
        SMOAP_LOG_INFO("installing ShineInitColorOverride -> Shine::init");
        // Sail-resolved at link time so we don't need to bake the string here
        // — pass the catalog constant to the templated installAtSym via
        // util::TemplateString conversion. NOTE: HkTrampoline.installAtSym is
        // a consteval template, requires a literal — so we duplicate the
        // string here for now. Keep in sync with HookSymbols.hpp's kShineInit.
        shineInitColorOverride.installAtSym<
            "_ZN5Shine4initERKN2al13ActorInitInfoE">();
    }
}

}  // namespace smoap::hooks
