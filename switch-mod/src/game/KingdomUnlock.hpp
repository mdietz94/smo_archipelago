// Kingdom name ↔ bit-index ↔ SMO worldId mapping table.
//
// 17 kingdoms (Cap..Darker Side); the canonical ordering used by the apworld
// (kKingdoms[] in the cpp). Consumers: M6 phase D deposit kingdom resolution
// (ap_moons_kingdom[bit], kingdomBitForWorldId), M7 Path A kingdom-order gate
// (kingdomShortFromHomeStage, kingdomShortFromWorldId, worldIdFromKingdomShort),
// and the per-kingdom shine-counter hooks. Despite the legacy filename, this
// file is NOT about AP-driven kingdom unlocks — that plumbing was removed.

#pragma once

#include <cstdint>

namespace smoap::game {

std::uint8_t kingdomBitFor(const char* kingdom);
const char* kingdomForBit(std::uint8_t bit);

// M6 phase D — map SMO's internal world id (returned by
// GameDataFunction::getCurrentWorldIdNoDevelop) to our kKingdoms[] bit index.
// 17 kingdoms in total, but the SMO ordering DIFFERS from our kKingdoms[] in
// two places (verified against OdysseyDecomp's getWorldIndex* functions):
//
//   - SMO id 8  = Sea (Seaside)   → our bit 9  (Seaside)
//   - SMO id 9  = Snow            → our bit 8  (Snow)
//   - SMO id 11 = Boss (Bowser's) → our bit 12 (Bowser)
//   - SMO id 12 = Sky (Ruined)    → our bit 11 (Ruined)
//
// Other 13 ids are identity-mapped. Returns 0xff for out-of-range / unknown
// (caller treats as "kingdom-less" and suppresses any debit). Don't reorder
// kKingdoms[] — it's the canonical ordering used by the apworld and
// captureBitFor / kingdomBitFor for AP names.
std::uint8_t kingdomBitForWorldId(int world_id);

// M6 phase D — resolve GameDataFunction::getCurrentWorldIdNoDevelop via
// nn::ro::LookupSymbol once and store the function pointer on ApState. Same
// pattern as M6-B's addHackDictionary symbol bind. Called from main.cpp at
// module load; the AddPayShineHook callback reads the function pointer to
// resolve "which kingdom is Mario in" inside its hot path.
void installDepositKingdomLookupSymbol();

// Map a SMO HomeStage name (e.g. "ForestWorldHomeStage") to the apworld
// kingdom short name ("Wooded"). Returns nullptr for unknown stages.
// Source of truth is the same KINGDOM_FOR_HOMESTAGE table in
// scripts/extract_shine_map.py.
const char* kingdomShortFromHomeStage(const char* home_stage);

// Map a SMO internal worldId (0..16) to the apworld kingdom short name.
// Composes kingdomBitForWorldId + kingdomForBit so the 4 SMO/apworld order
// mismatches documented on kingdomBitForWorldId are honored — direct
// indexing into kKingdoms[] would mis-route Sea↔Snow and Boss↔Sky for the
// M7 Path A Seaside/Snow gate. Returns nullptr for unknown ids.
const char* kingdomShortFromWorldId(int world_id);

// Inverse of kingdomShortFromWorldId. Returns -1 for unknown short names.
// Composes kingdomBitFor + scan over kingdomBitForWorldId so the inverse
// also honors the 4 SMO/apworld swaps.
int worldIdFromKingdomShort(const char* kingdom_short);

}  // namespace smoap::game
