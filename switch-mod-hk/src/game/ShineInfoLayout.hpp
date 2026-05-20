// Minimal layout mirror of SMO's ShineInfo class.
//
// We read three fields from a ShineInfo* in the moon-get hook. Rather than
// transitively pulling in lunakit-vendor's full game/Info/ShineInfo.h (which
// drags al/LiveActor/* and sead/* into every hook TU), we read at known
// offsets verified empirically against SMO 1.0.0.
//
// Layout: three FixedSafeString<0x80> packed back-to-back, then shineId.
// Each FixedSafeString<0x80> is 0x98 bytes:
//   +0x00  vtable ptr           (8 bytes)
//   +0x08  const char* mStringTop  (8 bytes) <-- cstr() returns this
//   +0x10  s32 mBufferSize      (4 bytes, = 0x80 = 128)
//   +0x14  char mBuffer[0x80]   (128 bytes; mStringTop usually points here)
//   total = 0x98 bytes.
//
// So ShineInfo layout (verified on 1.0.0 via runtime pointer-print):
//   +0x000  stageName  (FixedSafeString<0x80>, 0x98 bytes)
//   +0x098  objectId   (FixedSafeString<0x80>, 0x98 bytes)
//   +0x130  scenObjId  (FixedSafeString<0x80>, 0x98 bytes)
//   +0x1C8  shineId    (int)
//
// NOTE: lunakit-vendor/src/game/Info/ShineInfo.h has stale comments claiming
// objectId is at 0xA0 and scenObjId at 0x138. Those are wrong for SMO 1.0.0
// — see the runtime evidence in the M4 dev log (mBufferSize=0x80 lined up
// at the predicted location with the corrected offset).

#pragma once

#include <cstddef>
#include <cstdint>

namespace smoap::game {

namespace shine_info_layout {

inline constexpr std::size_t kStageNameOffset = 0x000;
inline constexpr std::size_t kObjectIdOffset  = 0x098;
inline constexpr std::size_t kScenObjIdOffset = 0x130;
inline constexpr std::size_t kShineIdOffset   = 0x1C8;

// sead::FixedSafeString's mStringTop is at +0x08 from its base.
inline constexpr std::size_t kSafeStringTopOffset = 0x08;

inline const char* readSafeString(const void* base, std::size_t field_offset) {
    if (!base) return nullptr;
    const auto* p = static_cast<const std::uint8_t*>(base) + field_offset
                  + kSafeStringTopOffset;
    return *reinterpret_cast<const char* const*>(p);
}

inline const char* stageName(const void* shine_info) {
    return readSafeString(shine_info, kStageNameOffset);
}

inline const char* objectId(const void* shine_info) {
    return readSafeString(shine_info, kObjectIdOffset);
}

inline const char* scenObjId(const void* shine_info) {
    return readSafeString(shine_info, kScenObjIdOffset);
}

inline int shineId(const void* shine_info) {
    if (!shine_info) return -1;
    return *reinterpret_cast<const int*>(
        static_cast<const std::uint8_t*>(shine_info) + kShineIdOffset);
}

}  // namespace shine_info_layout

}  // namespace smoap::game
