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
// 17 kingdoms in total, with ONE Sea/Snow swap relative to kKingdoms[]
// (verified against OdysseyDecomp's getWorldIndex* functions):
//
//   - SMO id 8  = Sea (Seaside)   → our bit 9  (Seaside)
//   - SMO id 9  = Snow            → our bit 8  (Snow)
//
// Boss (id 11, develop name "Attack") and Sky (id 12) ARE identity-mapped:
// per OdysseyDecomp + the actual SMO 1.0.0 ShineList contents,
// AttackWorldHomeStage holds the RUINED Kingdom shines (Lord of Lightning,
// Roulette Tower, etc.) and SkyWorldHomeStage holds the BOWSER'S Kingdom
// shines (Bowser's Castle, Jizo, Bowser Statue's Nose, etc.). Our
// kKingdoms[] already orders "Ruined" at bit 11 and "Bowser" at bit 12,
// matching SMO. An earlier version of this table mistakenly added a
// Boss/Sky swap and produced the Bowser↔Ruined HUD/outstanding swap users
// observed in late-game play.
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

// M6 phase D — resolve GameDataFunction::getPayShineNum(Accessor, worldId)
// the same way. Drives ApState::buildPaySnapshot, which is the input to
// the bridge's derived outstanding (outstanding = lifetime_received_AP −
// PayShineNum). Without it, the bridge never sees a snapshot and AP credit
// is never debited — deposit-then-crash protection inert. Called from
// main.cpp adjacent to installDepositKingdomLookupSymbol().
void installPayShineSnapshotSymbol();

// Map a SMO HomeStage name (e.g. "ForestWorldHomeStage") to the apworld
// kingdom short name ("Wooded"). Returns nullptr for unknown stages.
// Source of truth is the same KINGDOM_FOR_HOMESTAGE table in
// scripts/extract_shine_map.py.
const char* kingdomShortFromHomeStage(const char* home_stage);

// Map a SMO internal worldId (0..16) to the apworld kingdom short name.
// Composes kingdomBitForWorldId + kingdomForBit so the Sea↔Snow swap
// documented on kingdomBitForWorldId is honored — direct indexing into
// kKingdoms[] would mis-route Sea↔Snow for the M7 Path A Seaside/Snow
// gate. Returns nullptr for unknown ids.
const char* kingdomShortFromWorldId(int world_id);

// Inverse of kingdomShortFromWorldId. Returns -1 for unknown short names.
// Composes kingdomBitFor + scan over kingdomBitForWorldId so the inverse
// also honors the Sea/Snow swap.
int worldIdFromKingdomShort(const char* kingdom_short);

}  // namespace smoap::game
