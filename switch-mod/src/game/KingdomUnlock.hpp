// Kingdom-unlock bit assignment.
//
// 17 kingdoms (Cap..Darker Side); we use the first 17 bits of
// ApState::received_kingdom_mask.

#pragma once

#include <cstdint>
#include <string>

namespace smoap::game {

std::uint8_t kingdomBitFor(const std::string& kingdom);
const char* kingdomForBit(std::uint8_t bit);

}  // namespace smoap::game
