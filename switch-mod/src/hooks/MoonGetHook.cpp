// Hook on GameDataFile::setGotShine(const ShineInfo*).
//
// Reads (stageName, objectId, shineId) from the ShineInfo* via the layout
// mirror in game/ShineInfoLayout.hpp and ships the raw IDs to the bridge.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../ap/shine_lookup.hpp"
#include "../game/ShineInfoLayout.hpp"
#include "../util/Log.hpp"

#include <cstdint>

class GameDataFile;
class ShineInfo;

namespace smoap::hooks {

namespace {

// Quick sanity check: do the first few bytes of a string pointer look like
// ASCII? If the offset is wrong we'll get random bytes or kernel addresses;
// using strlen / %s on those is fatal.
bool stringSane(const char* s) {
    if (!s) return false;
    auto p = reinterpret_cast<std::uintptr_t>(s);
    if (p < 0x10000) return false;
    for (int i = 0; i < 8; ++i) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c == 0) return i > 0;
        if (c < 0x20 || c > 0x7e) return false;
    }
    return true;
}

HkTrampoline<void, GameDataFile*, const ShineInfo*> moonGetHook =
    hk::hook::trampoline([](GameDataFile* self, const ShineInfo* info) -> void {
        // Phase 4 block: in talkatoo_mode, refuse to flip the shine bit for
        // moons the player hasn't been told about. The decision has to
        // happen BEFORE Orig — once setGotShine runs the moon is permanently
        // flagged in this save until a manual reset. Skipping Orig leaves
        // the bit unset; the moon respawns on save-reload, the AP check is
        // also suppressed, and Mario's get-cinematic plays cosmetically
        // (Option B / A3-fallback from the roadmap).
        const char* stage = info ? smoap::game::shine_info_layout::stageName(info) : nullptr;
        const char* obj   = info ? smoap::game::shine_info_layout::objectId(info)  : nullptr;
        const bool stage_ok = stringSane(stage);
        const bool obj_ok   = stringSane(obj);

        if (smoap::ap::ApState::instance().talkatoo_mode.load(
                std::memory_order_acquire)) {
            if (stage_ok && obj_ok) {
                const int shine_uid =
                    smoap::game::shineUidByStageObj(stage, obj);
                // Progression/Multi Moon exemption: scenario-advancing moons
                // are always collectible. Blocking one would soft-lock every
                // moon that gates on scenario_no >= N downstream. Sourced
                // from the `progression: true` flag in locations.json.
                const bool is_progression =
                    smoap::game::isProgressionShine(stage, obj);
                if (shine_uid >= 0 && !is_progression &&
                    !smoap::ap::ApState::instance().isMoonNamed(shine_uid)) {
                    SMOAP_LOG_INFO("[talkatoo-block] BLOCKED collection "
                                   "stage=%s obj=%s uid=%d (not named by "
                                   "Talkatoo)",
                                   stage, obj, shine_uid);
                    // Paint a "Blocked by Talkatoo!" label on the cutscene
                    // title pane. The get-cinematic that's about to play
                    // still fires Shine::exeDemoGet etc. (which we already
                    // hook in MoonLabelHook), and the existing label
                    // pipeline turns this into a visible message instead
                    // of leaving the pane with a half-initialized vanilla
                    // moon name. valid_for 4s matches the cutscene length.
                    const int seq = smoap::ap::ApState::instance()
                        .next_check_seq.fetch_add(1, std::memory_order_relaxed);
                    smoap::ap::ApState::instance().setPendingMoonLabel(
                        "Blocked by Talkatoo!", seq,
                        smoap::ap::ApState::nowMs() + 4000);
                    // Skip Orig + skip the outbound check. Get-cinematic
                    // still plays in SMO since Shine::get's other side
                    // effects (recoveryPlayerMax, sound, particle) already
                    // ran; the moon just won't stay flagged.
                    return;
                }
                // Moon is in shine_table.h AND named: fall through to vanilla.
                // Or moon is NOT in shine_table.h (shine_uid < 0): also fall
                // through — SMO has more moons than the apworld tracks
                // (regional/exclude moons), and we don't want to block
                // those just because they aren't in our table.
            }
            // If stage/obj read failed we fall through to vanilla too rather
            // than block-fail-closed — the existing stringSane check below
            // logs a warning, which is the actionable signal.
        }

        moonGetHook.orig(self, info);
        if (!info) return;
        const int uid = smoap::game::shine_info_layout::shineId(info);

        if (stage_ok && obj_ok) {
            SMOAP_LOG_INFO("MoonGetHook: reporting stage=%s id=%s uid=%d",
                           stage, obj, uid);
            smoap::ap::reportMoonChecked(stage, obj, uid);
        } else {
            SMOAP_LOG_WARN("MoonGetHook: insane string ptrs stage_ok=%d obj_ok=%d — "
                           "offsets in ShineInfoLayout.hpp likely wrong; dropping",
                           stage_ok ? 1 : 0, obj_ok ? 1 : 0);
        }
    });

}  // namespace

void installMoonGetHook() {
    SMOAP_LOG_INFO("installing MoonGetHook -> GameDataFile::setGotShine");
    moonGetHook.installAtSym<"_ZN12GameDataFile11setGotShineEPK9ShineInfo">();
}

}  // namespace smoap::hooks
