#include "KingdomUnlock.hpp"

#include <array>
#include <string_view>

namespace smoap::game {

// Order matches apworld's kingdom progression. Kept as a simple flat table so
// it's trivially diffable when extending.
static constexpr std::array<std::string_view, 17> kKingdoms = {
    "Cap", "Cascade", "Sand", "Wooded", "Lake", "Cloud", "Lost",
    "Metro", "Snow", "Seaside", "Luncheon", "Ruined",
    "Bowser", "Moon", "Mushroom", "Dark Side", "Darker Side",
};

std::uint8_t kingdomBitFor(const std::string& kingdom) {
    for (std::uint8_t i = 0; i < kKingdoms.size(); ++i) {
        if (kingdom == kKingdoms[i]) return i;
    }
    return 0xff;
}

const char* kingdomForBit(std::uint8_t bit) {
    if (bit >= kKingdoms.size()) return "";
    return kKingdoms[bit].data();
}

}  // namespace smoap::game
