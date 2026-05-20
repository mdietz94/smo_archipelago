#include "KingdomUnlock.hpp"

#include <array>
#include <cstring>

#include <hk/ro/RoUtil.h>

#include "../ap/ApState.hpp"
#include "../hooks/HookSymbols.hpp"
#include "../util/Log.hpp"

namespace smoap::game {

// Order matches apworld's kingdom progression. Kept as a simple flat table so
// it's trivially diffable when extending.
static constexpr std::array<const char*, 17> kKingdoms = {
    "Cap", "Cascade", "Sand", "Wooded", "Lake", "Cloud", "Lost",
    "Metro", "Snow", "Seaside", "Luncheon", "Ruined",
    "Bowser", "Moon", "Mushroom", "Dark Side", "Darker Side",
};

std::uint8_t kingdomBitFor(const char* kingdom) {
    if (!kingdom) return 0xff;
    for (std::uint8_t i = 0; i < kKingdoms.size(); ++i) {
        if (std::strcmp(kingdom, kKingdoms[i]) == 0) return i;
    }
    return 0xff;
}

const char* kingdomForBit(std::uint8_t bit) {
    if (bit >= kKingdoms.size()) return "";
    return kKingdoms[bit];
}

void installDepositKingdomLookupSymbol() {
    const ptr addr = hk::ro::lookupSymbol(
        smoap::sym::kGameDataFunctionGetCurrentWorldIdNoDevelop);
    if (addr == 0) {
        SMOAP_LOG_ERROR("getCurrentWorldIdNoDevelop lookup FAILED — "
                        "AddPayShineHook will suppress all snapshots");
        smoap::ap::ApState::instance().get_current_world_id_fn = nullptr;
        return;
    }
    smoap::ap::ApState::instance().get_current_world_id_fn = reinterpret_cast<void*>(addr);
    SMOAP_LOG_INFO("getCurrentWorldIdNoDevelop resolved @ 0x%lx",
                   static_cast<unsigned long>(addr));
}

void installPayShineSnapshotSymbol() {
    const ptr addr = hk::ro::lookupSymbol(
        smoap::sym::kGameDataFunctionGetPayShineNumByWorld);
    if (addr == 0) {
        SMOAP_LOG_ERROR("getPayShineNum lookup FAILED — "
                        "ApState::buildPaySnapshot will return false and the "
                        "bridge will never derive outstanding (no AP credit "
                        "ever debited; deposit-then-crash protection inert)");
        smoap::ap::ApState::instance().get_pay_shine_num_fn = nullptr;
        return;
    }
    smoap::ap::ApState::instance().get_pay_shine_num_fn = reinterpret_cast<void*>(addr);
    SMOAP_LOG_INFO("getPayShineNum resolved @ 0x%lx",
                   static_cast<unsigned long>(addr));
}

std::uint8_t kingdomBitForWorldId(int world_id) {
    // 0..16 maps mostly 1:1 to kKingdoms[], with the ONE Sea/Snow swap
    // documented in KingdomUnlock.hpp.
    static constexpr std::uint8_t kWorldIdToBit[17] = {
        0, 1, 2, 3, 4, 5, 6, 7,
        9,   // 8  Sea  -> Seaside (bit 9)   <-- SWAP
        8,   // 9  Snow -> Snow    (bit 8)   <-- SWAP
        10, 11, 12, 13, 14, 15, 16,
    };
    if (world_id < 0 || world_id >= 17) return 0xff;
    return kWorldIdToBit[world_id];
}

namespace {

struct HomeStageRow {
    const char* home_stage;
    const char* kingdom_short;
};
constexpr HomeStageRow kHomeStageToKingdom[] = {
    {"CapWorldHomeStage",        "Cap"},
    {"WaterfallWorldHomeStage",  "Cascade"},
    {"SandWorldHomeStage",       "Sand"},
    {"LakeWorldHomeStage",       "Lake"},
    {"ForestWorldHomeStage",     "Wooded"},
    {"CloudWorldHomeStage",      "Cloud"},
    {"ClashWorldHomeStage",      "Lost"},
    {"CityWorldHomeStage",       "Metro"},
    {"SnowWorldHomeStage",       "Snow"},
    {"SeaWorldHomeStage",        "Seaside"},
    {"LavaWorldHomeStage",       "Luncheon"},
    {"AttackWorldHomeStage",     "Ruined"},
    {"SkyWorldHomeStage",        "Bowser"},
    {"MoonWorldHomeStage",       "Moon"},
    {"PeachWorldHomeStage",      "Mushroom"},
    {"Special1WorldHomeStage",   "Dark Side"},
    {"Special2WorldHomeStage",   "Darker Side"},
};

}  // namespace

const char* kingdomShortFromHomeStage(const char* home_stage) {
    if (!home_stage || !*home_stage) return nullptr;
    for (const auto& row : kHomeStageToKingdom) {
        if (std::strcmp(home_stage, row.home_stage) == 0) return row.kingdom_short;
    }
    return nullptr;
}

const char* kingdomShortFromWorldId(int world_id) {
    const std::uint8_t bit = kingdomBitForWorldId(world_id);
    if (bit == 0xff) return nullptr;
    const char* short_name = kingdomForBit(bit);
    return (short_name && *short_name) ? short_name : nullptr;
}

int worldIdFromKingdomShort(const char* kingdom_short) {
    const std::uint8_t bit = kingdomBitFor(kingdom_short);
    if (bit == 0xff) return -1;
    for (int wid = 0; wid < 17; ++wid) {
        if (kingdomBitForWorldId(wid) == bit) return wid;
    }
    return -1;
}

}  // namespace smoap::game
