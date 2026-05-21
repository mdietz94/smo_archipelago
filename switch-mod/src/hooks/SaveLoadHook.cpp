// Hook on GameDataFile::initializeData(). Clears session dedupe state and
// requests a fresh HELLO replay from the bridge. Debounces a burst of
// initializeData calls that SMO emits for a single save-load event.

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <atomic>
#include <cstdint>
#include <cstring>

#include "../ap/ApClient.hpp"
#include "../ap/ApProtocol.hpp"  // kCheckFieldCap
#include "../ap/ApState.hpp"
#include "../ap/shine_table.h"
#include "../game/KingdomUnlock.hpp"
#include "../ui/CappyMessenger.hpp"
#include "../util/Log.hpp"

class GameDataFile;

namespace smoap::hooks {

namespace {

std::atomic<std::uint64_t> g_fire_counter{0};
std::atomic<std::int64_t>  g_last_fire_ms{0};
std::atomic<std::int64_t>  g_last_side_effect_ms{0};
constexpr std::int64_t kSaveLoadDebounceMs = 500;

// Talkatoo% Phase 2 — pre-mark non-AP moons so the world only contains
// AP-pool locations. SKELETON, not wired yet — runs the apworld×shine_map
// intersection (kShineTable) against the per-kingdom AP pool and counts
// how many moons WOULD be pre-marked. The actual setGotShine() call is
// TODO: GameDataFile::setGotShine takes `const ShineInfo*`, not a uid —
// we'd need to either (a) discover a setGotShineByUid overload or
// (b) construct a ShineInfo on the stack from the HintInfo at
// mShineHintList[i] and pass it. Both are next-session work.
//
// Logging-only today means: enabling Talkatoo% on the user side doesn't
// hide non-AP moons yet. The world still contains every moon. Phase 3's
// speech-hook gives the AP feel; Phase 2's pre-marking is the polish.
void premarkNonApMoonsIfTalkatooMode() {
    auto& st = smoap::ap::ApState::instance();
    if (!st.talkatoo_mode.load(std::memory_order_acquire)) return;

    // Snapshot every kingdom's AP-pool once so we can do membership checks
    // by linear search. Stack-allocated to keep allocators out — fixed
    // upper bound from the wire-cap constants in ApProtocol.hpp.
    using Pool = smoap::ap::ApState::TalkatooKingdomPool;
    constexpr std::size_t kK = smoap::ap::ApState::kTalkatooKingdomCount;
    static char pool[kK][Pool::kMaxMoons][smoap::ap::kCheckFieldCap];
    static std::size_t pool_count[kK];
    for (std::size_t b = 0; b < kK; ++b) {
        pool_count[b] = st.snapshotTalkatooKingdom(
            static_cast<int>(b), pool[b], Pool::kMaxMoons);
    }

    // Walk the static shine_table built from apworld locations × shine_map.
    // For each moon NOT in its kingdom's pool, count it (real setGotShine
    // call is TODO — see file header comment).
    std::size_t would_premark = 0;
    std::size_t hits = 0;
    for (const auto& row : smoap::game::kShineTable) {
        // AP-form kingdom → bit. Translation is identity for everything
        // except "Bowser's" → bit 12 (see KingdomUnlock.cpp).
        const std::uint8_t bit = smoap::game::kingdomBitFor(row.kingdom.data());
        if (bit >= kK) continue;  // unknown kingdom — leave it alone
        bool in_pool = false;
        for (std::size_t i = 0; i < pool_count[bit]; ++i) {
            if (std::strcmp(pool[bit][i], row.shine_id.data()) == 0) {
                in_pool = true; break;
            }
        }
        if (in_pool) {
            ++hits;
        } else {
            ++would_premark;
        }
    }
    SMOAP_LOG_INFO("[talkatoo-premark] pool-hit=%zu would-premark=%zu (NOT WIRED — see file header)",
                   hits, would_premark);
}

HkTrampoline<void, GameDataFile*> saveLoadHook =
    hk::hook::trampoline([](GameDataFile* self) -> void {
        const std::uint64_t fire_n =
            g_fire_counter.fetch_add(1, std::memory_order_relaxed) + 1;
        const std::int64_t now_ms = smoap::ap::ApState::nowMs();
        const std::int64_t prev_ms =
            g_last_fire_ms.exchange(now_ms, std::memory_order_relaxed);
        const std::int64_t delta_ms = prev_ms ? (now_ms - prev_ms) : -1;

        SMOAP_LOG_INFO("[saveload-diag] fire#%llu dt=%lldms self=%p",
                       static_cast<unsigned long long>(fire_n),
                       static_cast<long long>(delta_ms),
                       self);

        auto& st = smoap::ap::ApState::instance();
        st.save_load_passthrough.store(true, std::memory_order_release);
        saveLoadHook.orig(self);
        st.save_load_passthrough.store(false, std::memory_order_release);

        const std::int64_t prev_side_effect =
            g_last_side_effect_ms.load(std::memory_order_relaxed);
        if (prev_side_effect != 0 && (now_ms - prev_side_effect) < kSaveLoadDebounceMs) {
            SMOAP_LOG_INFO("[saveload-diag] fire#%llu debounced "
                           "(last side-effect %lldms ago, window=%lldms)",
                           static_cast<unsigned long long>(fire_n),
                           static_cast<long long>(now_ms - prev_side_effect),
                           static_cast<long long>(kSaveLoadDebounceMs));
            return;
        }
        g_last_side_effect_ms.store(now_ms, std::memory_order_relaxed);

        SMOAP_LOG_INFO("SaveLoadHook: clearing session state + requesting re-HELLO");
        st.locations_checked.reset();
        st.captures_unlocked.reset();
        st.goal_sent = false;
        st.death_pending_send.store(false, std::memory_order_release);
        smoap::ui::CappyMessenger::instance().clearDispatchLatch();
        std::size_t drained = 0;
        while (st.pending_capture_grant.peekRef() != nullptr) {
            st.pending_capture_grant.popDiscard();
            ++drained;
        }
        if (drained > 0) {
            SMOAP_LOG_INFO("SaveLoadHook: dropped %zu pending capture grant(s)",
                           drained);
        }
        st.save_was_loaded.store(true, std::memory_order_release);
        smoap::ap::ApClient::instance().requestRehello();

        // Talkatoo% pre-mark pass. No-op when mode is off; today even when
        // mode is on this just logs the count of moons that would be pre-
        // marked — see the function header for the missing write primitive.
        premarkNonApMoonsIfTalkatooMode();

        smoap::ap::ApClient::instance().deferSaveLoadStatusBubble();
    });

}  // namespace

void installSaveLoadHook() {
    SMOAP_LOG_INFO("installing SaveLoadHook -> GameDataFile::initializeData");
    saveLoadHook.installAtSym<"_ZN12GameDataFile14initializeDataEv">();
}

}  // namespace smoap::hooks
