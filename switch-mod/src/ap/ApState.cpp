#include "ApState.hpp"

#include <cstring>

#include "../game/CaptureGate.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../game/MoonApply.hpp"

namespace smoap::ap {

ApState& ApState::instance() {
    static ApState s;
    return s;
}

void ApState::applyOnFrame() {
    Item item;
    while (inbound.pop(item)) {
        const std::uint64_t h = hashCheck(Check{
            .kind = item.kind,
            .kingdom = item.kingdom,
            .shine_id = item.shine_id,
            .cap = item.cap,
            .slot = item.slot,
        });

        switch (item.kind) {
            case ItemKind::Moon:
                if (!item.kingdom.empty() && !item.shine_id.empty()) {
                    synthetic_grant_this_frame = true;
                    smoap::game::grantShine(item.kingdom, item.shine_id);
                    synthetic_grant_this_frame = false;
                    locations_checked.insert(h);  // suppress matching outbound check
                }
                break;
            case ItemKind::Capture:
                if (!item.cap.empty()) {
                    const std::uint8_t bit = smoap::game::captureBitFor(item.cap);
                    if (bit < captures_unlocked.size()) captures_unlocked.set(bit);
                }
                break;
            case ItemKind::Kingdom:
                if (!item.kingdom.empty()) {
                    const std::uint8_t bit = smoap::game::kingdomBitFor(item.kingdom);
                    if (bit < 32) received_kingdom_mask |= (1u << bit);
                }
                break;
            case ItemKind::Shop:
            case ItemKind::Other:
                // M4 / M8: shop items don't grant in-game state directly; UI-only.
                break;
        }
    }
    synthetic_grant_this_frame = false;
}

std::uint64_t ApState::hashCheck(const Check& c) {
    // FNV-1a over a canonical fixed-order serialization. Cheap, no allocations.
    std::uint64_t h = 0xcbf29ce484222325ULL;
    auto mix = [&](const std::string& s) {
        for (char ch : s) {
            h ^= static_cast<std::uint8_t>(ch);
            h *= 0x100000001b3ULL;
        }
        h ^= '\x1f';
        h *= 0x100000001b3ULL;
    };
    h ^= static_cast<std::uint8_t>(c.kind);
    h *= 0x100000001b3ULL;
    mix(c.kingdom);
    mix(c.shine_id);
    mix(c.cap);
    h ^= static_cast<std::uint64_t>(c.slot + 1);  // -1 -> 0
    h *= 0x100000001b3ULL;
    return h;
}

}  // namespace smoap::ap
