// ApState singleton + utility methods.
//
// Phase 3b in progress. This file is being populated incrementally as the
// hooks that reference it land. Methods that aren't yet defined here will
// link if-and-only-if no current code path references them (LTO + gc-sections
// drop the unreferenced declarations).

#include "ApState.hpp"

#include <hk/svc/api.h>
#include <hk/svc/cpu.h>

#include "../game/CaptureGate.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../hooks/DeathHook.hpp"
#include "../ui/CappyMessenger.hpp"
#include "../util/Log.hpp"

class PlayerHitPointData;

namespace smoap::ap {

ApState& ApState::instance() {
    static ApState s;
    return s;
}

// Monotonic milliseconds. nn::os::GetSystemTick (used by the exlaunch build)
// returns u64 ticks at the system tick rate; Hakkun's hk::svc::getSystemTick
// is the equivalent. Switch's system tick rate is fixed at 19.2 MHz (1 ms ≈
// 19200 ticks); the conversion is ticks * 1000 / 19200000.
std::int64_t ApState::nowMs() {
    const u64 ticks = hk::svc::getSystemTick();
    return static_cast<std::int64_t>(ticks / 19200ULL);
}

// FNV-1a over a canonical fixed-order serialization. Cheap, no allocations.
// Used for session dedupe of outbound Check messages.
std::uint64_t ApState::hashCheck(const Check& c) {
    std::uint64_t h = 0xcbf29ce484222325ULL;
    auto mix = [&](const char* s) {
        for (; *s; ++s) {
            h ^= static_cast<std::uint8_t>(*s);
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
    mix(c.stage_name);
    mix(c.object_id);
    h ^= static_cast<std::uint64_t>(c.shine_uid + 1);
    h *= 0x100000001b3ULL;
    mix(c.hack_name);
    return h;
}

namespace {

// Minimal layout mirror — same shape as AddPayShineHook's local. Keeps the
// game-side header bleed contained to one .cpp.
struct GameDataHolderAccessor { void* mData; };
using GetPayShineNumFn = int (*)(GameDataHolderAccessor, int);

}  // namespace

// Classify a moon item's grant amount. "X Kingdom Multi-Moon" represents
// one in-game Multi-Moon (3 power moons). All other moon items count as 1.
static int moonGrantAmount(const Item& item) {
    const char* s = item.shine_id;
    const char* needle = "Multi-Moon";
    while (*s) {
        const char* a = s;
        const char* b = needle;
        while (*a && *b && *a == *b) { ++a; ++b; }
        if (*b == '\0') return 3;
        ++s;
    }
    return 1;
}

void ApState::applyOnFrame() {
    // Drain pending shine-color scouts first.
    ShineScout sc;
    while (inbound_scouts.pop(sc)) {
        if (sc.shine_uid < 0 ||
            static_cast<std::size_t>(sc.shine_uid) >= kMaxShineUid) {
            SMOAP_LOG_WARN("[shine-color] dropping uid=%d (out of range; bump kMaxShineUid?)",
                           sc.shine_uid);
            continue;
        }
        std::uint8_t pal = static_cast<std::uint8_t>(sc.palette & 0xFF);
        if (pal == kNoPaletteOverride) {
            pal = 0;
        }
        setShinePalette(sc.shine_uid, pal);
    }

    const bool suppress_cappy = (inbound.pendingApprox() > 3);

    constexpr std::size_t kDrainCap = 16;
    std::size_t drained = 0;
    while (drained < kDrainCap) {
        const Item* item_ptr = inbound.peekRef();
        if (!item_ptr) break;
        const Item& item = *item_ptr;
        Check synth{};
        synth.kind = item.kind;
        copyCheckField(synth.kingdom, item.kingdom);
        copyCheckField(synth.shine_id, item.shine_id);
        copyCheckField(synth.cap, item.cap);
        (void)hashCheck(synth);

        bool replay_suppress = false;

        switch (item.kind) {
            case ItemKind::Moon: {
                const int amount = moonGrantAmount(item);
                const std::uint8_t bit = item.kingdom[0]
                    ? smoap::game::kingdomBitFor(item.kingdom)
                    : 0xFFu;
                if (bit < 17) {
                    SMOAP_LOG_INFO(
                        "[m6-moon] grant observed kingdom=%s(bit=%u) +%d "
                        "shine_id='%s' from=%s (counter driven by OutstandingMsg)",
                        item.kingdom, bit, amount, item.shine_id, item.from);
                } else {
                    SMOAP_LOG_WARN(
                        "[m6-moon] DROPPED moon item: kingdom='%s' (bit=%u) "
                        "not a known kingdom — shine_id='%s' from=%s",
                        item.kingdom, bit, item.shine_id, item.from);
                }
                break;
            }
            case ItemKind::Capture:
                if (item.cap[0] != '\0') {
                    const std::uint8_t bit = smoap::game::captureBitFor(item.cap);
                    const char* hack = item.hack_name[0] ? item.hack_name : item.cap;
                    const bool already_owned =
                        smoap::game::captureAlreadyInDictionary(hack);
                    if (bit < captures_unlocked.size()) captures_unlocked.set(bit);
                    SMOAP_LOG_INFO("[m6-capture] cap='%s' bit=%u "
                                   "hack='%s' from=%s%s",
                                   item.cap, bit, item.hack_name, item.from,
                                   already_owned ? " (replay; suppressing bubble)" : "");
                    if (already_owned) {
                        replay_suppress = true;
                    } else {
                        const bool granted = smoap::game::grantCapture(item.cap, hack);
                        if (!granted) {
                            if (!pending_capture_grant.push(item)) {
                                SMOAP_LOG_WARN(
                                    "[m6-capture] pending_capture_grant FULL — "
                                    "dropping cap='%s' hack='%s'",
                                    item.cap, hack);
                            }
                            inbound.popDiscard();
                            ++drained;
                            continue;
                        }
                    }
                }
                break;
            case ItemKind::Other:
                SMOAP_LOG_DEBUG("[m6-other] item kind=%u name='%s' from=%s",
                                static_cast<unsigned>(item.kind),
                                item.name, item.from);
                break;
        }

        smoap::ui::CappyMessenger::instance().enqueue(item, local_slot,
                                                      suppress_cappy || replay_suppress);

        inbound.popDiscard();
        ++drained;
    }
    synthetic_grant_this_frame = false;
    maybeApplyInboundKill();
}

void ApState::flushPendingCaptureGrants() {
    while (true) {
        const Item* item_ptr = pending_capture_grant.peekRef();
        if (!item_ptr) break;
        const Item& item = *item_ptr;
        const char* hack = item.hack_name[0] ? item.hack_name : item.cap;
        const bool granted = smoap::game::grantCapture(item.cap, hack);
        if (!granted) break;
        SMOAP_LOG_INFO("[m6-capture] deferred Cappy firing now cap='%s' "
                       "hack='%s' from=%s",
                       item.cap, hack, item.from);
        smoap::ui::CappyMessenger::instance().enqueue(item, local_slot,
                                                      /*suppress=*/false);
        pending_capture_grant.popDiscard();
    }
}

void ApState::maybeApplyInboundKill() {
    if (!inbound_kill_pending.exchange(false, std::memory_order_acq_rel)) return;
    if (!deathlink_enabled.load(std::memory_order_relaxed)) {
        SMOAP_LOG_INFO("[deathlink in] dropped (deathlink disabled in hello_ack)");
        return;
    }
    const auto now = nowMs();
    const auto last = last_observed_death_ms.load(std::memory_order_relaxed);
    if (last != 0 && now - last < kInboundKillDebounceMs) {
        SMOAP_LOG_INFO("[deathlink in] swallowed (last death %lldms ago < %lldms window)",
                       static_cast<long long>(now - last),
                       static_cast<long long>(kInboundKillDebounceMs));
        return;
    }
    auto* hp = static_cast<PlayerHitPointData*>(player_hp_cache.load(std::memory_order_relaxed));
    if (!hp) {
        SMOAP_LOG_INFO("[deathlink in] dropped (no cached PlayerHitPointData yet)");
        return;
    }
    SMOAP_LOG_INFO("[deathlink in] applying synthetic kill");
    synthetic_death_this_frame = true;
    smoap::hooks::synthKillMario(hp);
    synthetic_death_this_frame = false;
    last_observed_death_ms.store(now, std::memory_order_relaxed);
}

void ApState::setPendingMoonLabel(const char* text, int seq, std::int64_t deadline_ms) {
    if (seq <= 0) return;  // 0 is the "empty" sentinel
    std::size_t i = 0;
    if (text != nullptr) {
        while (i + 1 < kPendingMoonLabelCap && text[i] != '\0') {
            pending_moon_label.text[i] = text[i];
            ++i;
        }
    }
    pending_moon_label.text[i] = '\0';
    pending_moon_label.deadline_ms = deadline_ms;
    pending_moon_label.published_seq.store(seq, std::memory_order_release);
}

bool ApState::tryTakePendingMoonLabel(char (&text_out)[kPendingMoonLabelCap]) {
    const int seq = pending_moon_label.published_seq.load(std::memory_order_acquire);
    if (seq == 0) return false;
    if (seq == label_last_consumed_seq) return false;
    if (pending_moon_label.deadline_ms != 0 &&
        nowMs() > pending_moon_label.deadline_ms) {
        label_last_consumed_seq = seq;
        return false;
    }
    for (std::size_t i = 0; i < kPendingMoonLabelCap; ++i) {
        text_out[i] = pending_moon_label.text[i];
    }
    text_out[kPendingMoonLabelCap - 1] = '\0';
    label_last_consumed_seq = seq;
    return true;
}

bool ApState::buildPaySnapshot(PendingPaySnapshot& out) const {
    void* holder = game_data_holder_cache.load(std::memory_order_relaxed);
    if (!holder || !get_pay_shine_num_fn) return false;
    auto fn = reinterpret_cast<GetPayShineNumFn>(get_pay_shine_num_fn);
    GameDataHolderAccessor acc{holder};
    // Iterate by kingdom BIT and resolve the matching worldId. Composition
    // (bit → short name → worldId) honors the Sea↔Snow swap documented on
    // kingdomBitForWorldId.
    for (int bit = 0; bit < 17; ++bit) {
        const char* name = smoap::game::kingdomForBit(static_cast<std::uint8_t>(bit));
        if (!name || !*name) {
            out.totals[bit] = 0;
            continue;
        }
        const int world_id = smoap::game::worldIdFromKingdomShort(name);
        if (world_id < 0) {
            out.totals[bit] = 0;
            continue;
        }
        const int n = fn(acc, world_id);
        out.totals[bit] = (n < 0) ? 0 : n;
    }
    return true;
}

}  // namespace smoap::ap
