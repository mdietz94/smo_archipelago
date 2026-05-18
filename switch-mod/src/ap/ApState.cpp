#include "ApState.hpp"

#include <cstring>

#include "nn/os.h"
#include "nn/os/os_tick.hpp"
#include "nn/time/time_timespan.hpp"

#include "../game/CaptureGate.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../game/MoonApply.hpp"
#include "../hooks/DeathHook.hpp"
#include "../ui/CappyMessenger.hpp"
#include "../util/Log.hpp"

class PlayerHitPointData;

namespace smoap::ap {

ApState& ApState::instance() {
    static ApState s;
    return s;
}

// M6 phase A: classify a moon item's grant amount.
// "X Kingdom Multi-Moon" in the AP item pool represents one in-game Multi-Moon
// (3 power moons). All other moon items count as 1. Match on the shine_id
// suffix to keep this robust across "Multi-Moon", "Cap Kingdom Multi-Moon",
// etc. — the bridge passes only the kingdom-stripped tail in shine_id.
static int moonGrantAmount(const Item& item) {
    const char* s = item.shine_id;
    // Search for "Multi-Moon" substring (case-sensitive, deliberate — the
    // apworld emits exactly this casing).
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
    // Drain pending shine-color scouts first. ShineScout is two ints —
    // no allocator path. Sentinel 0xFF would let the game keep its stage
    // default; a real palette index overrides at the next
    // setStageShineAnimFrame call (substituted in ShineAppearanceHook).
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
            // 0xFF on the wire would collide with our sentinel. Coerce to 0
            // ("no visible change") rather than silently dropping the entry.
            pal = 0;
        }
        setShinePalette(sc.shine_uid, pal);
    }

    // Pre-sample pending count for the Cappy-message bulk-suppress heuristic.
    // We can't snapshot a count by copying Items into a batch[] array
    // because Item holds std::string fields — Item assignment triggers
    // libstdc++ heap alloc for any field >15 chars, which NULL-derefs in
    // subsdk9 (project_libstdcpp_allocator_broken_in_subsdk9.md). The
    // crash from "Cascade Kingdom Power Moon" item names is exactly this.
    //
    // Solution: read items by const ref straight out of the ring buffer
    // (peekRef returns &buf_[tail]; popDiscard advances tail). No copy,
    // no allocator path on the frame thread. The string data was allocated
    // ON THE WORKER THREAD when ApClient pushed it, and the worker uses a
    // path that doesn't trip the broken allocator (per the M4/M5 memory).
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
        const std::uint64_t h = hashCheck(synth);
        (void)h;  // M6 phase A: moon arm no longer dedupes via hash.

        // Per-item bubble suppression — set by an arm when the item is a
        // bridge HELLO replay of something we already own. Source-of-truth
        // check is the game's own state (e.g. isExistInHackDictionary for
        // captures) since SaveLoadHook resets our local bitsets right
        // before each re-HELLO, so they can't dedup the replay themselves.
        bool replay_suppress = false;

        switch (item.kind) {
            case ItemKind::Moon: {
                // Per-kingdom counter is driven by OutstandingMsg from the
                // bridge (M6 phase D). The ItemMsg path is observation-only
                // for moons — mutating ap_moons_kingdom[] here would
                // double-count every grant since OutstandingMsg already
                // applies the authoritative balance for this kingdom on
                // the worker thread before this frame-thread drain runs.
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
                    // M6 phase B: actually write into SMO's hack dictionary
                    // so the capture compendium / gameplay treats it as
                    // owned. Falls back to identity (use cap as hack_name)
                    // when bridge didn't resolve — works for the 1:1 names
                    // like Goomba/Goomba.
                    const char* hack = item.hack_name[0] ? item.hack_name : item.cap;
                    // Probe the game's authoritative state BEFORE setting
                    // our local bit so we can tell a fresh grant from a
                    // HELLO-replay of something we already own. Without
                    // this gate, every initializeData fire (5-20 per save
                    // load) re-ships the full capture set and enqueues a
                    // Cappy bubble for each one. Returns false in degraded
                    // states (symbols unresolved, GDH not cached) — in those
                    // cases we fall through to the existing defer-on-fail
                    // path below so a real first-time grant isn't dropped.
                    const bool already_owned =
                        smoap::game::captureAlreadyInDictionary(hack);
                    if (bit < captures_unlocked.size()) captures_unlocked.set(bit);
                    SMOAP_LOG_INFO("[m6-capture] cap='%s' bit=%u "
                                   "hack='%s' from=%s%s",
                                   item.cap, bit, item.hack_name, item.from,
                                   already_owned ? " (replay; suppressing bubble)" : "");
                    if (already_owned) {
                        // Dict entry already present — skip the addHack call
                        // and suppress the duplicate Cappy below.
                        replay_suppress = true;
                    } else {
                        const bool granted = smoap::game::grantCapture(item.cap, hack);
                        if (!granted) {
                            // GameDataHolder not cached yet (or symbols missing,
                            // or scene not loaded). Stash the item so the
                            // per-frame reconciler tail can retry once GDH is
                            // available — and the Cappy bubble fires at the same
                            // time as the actual unlock landing, not before.
                            // Without this, the user sees "Got Goomba" with an
                            // empty compendium entry.
                            if (!pending_capture_grant.push(item)) {
                                SMOAP_LOG_WARN(
                                    "[m6-capture] pending_capture_grant FULL — "
                                    "dropping cap='%s' hack='%s' (Cappy and dict "
                                    "write both lost; raise queue cap if this fires)",
                                    item.cap, hack);
                            }
                            // Skip the unconditional Cappy enqueue below — we'll
                            // fire it from flushPendingCaptureGrants after the
                            // grant lands.
                            inbound.popDiscard();
                            ++drained;
                            continue;
                        }
                    }
                }
                break;
            case ItemKind::Other:
                SMOAP_LOG_DEBUG("[m6-other] item kind=%u name='%s' from=%s "
                                "(no in-game effect)",
                                static_cast<unsigned>(item.kind),
                                item.name, item.from);
                break;
        }

        // Cappy speech — fires after the in-game effect lands so the user
        // sees the text alongside the visible change (e.g. capture unlock +
        // "Got Frog from Alice!" same frame). Filter rules in CappyMessenger
        // drop self-grants, REPL-injected items, bulk replays, and Other
        // kinds. Actual dispatch is per-frame in DrawMainHook -> tryPump.
        smoap::ui::CappyMessenger::instance().enqueue(item, local_slot,
                                                      suppress_cappy || replay_suppress);

        // Advance tail AFTER consuming all references into item — popDiscard
        // invalidates item_ptr (its slot can be overwritten by a producer push).
        inbound.popDiscard();
        ++drained;
    }
    synthetic_grant_this_frame = false;
    maybeApplyInboundKill();
}

void ApState::flushPendingCaptureGrants() {
    // Drain the pending-grant queue head-first. Order matters: SpscRing
    // preserves FIFO and the user perceives Cappy messages in arrival
    // order. If the head item's grant still fails (GDH still not cached),
    // stop — every queued item would fail for the same reason this frame,
    // and we'll try again next frame. The reconciler running just before
    // this would have populated the dict entry for any cap whose bit is
    // already set, so grantCapture's idempotent isExist check almost
    // always returns true in this loop in practice.
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
        // suppress=false for the retry path: by the time we get here the
        // original bulk-replay burst (if any) is N frames behind us.
        smoap::ui::CappyMessenger::instance().enqueue(item, local_slot,
                                                      /*suppress=*/false);
        pending_capture_grant.popDiscard();
    }
}

std::int64_t ApState::nowMs() {
    // nn::os::GetSystemTick returns a u64 tick at a fixed ~19.2 MHz. Convert
    // via the SDK helper so we don't bake the rate in here.
    const auto ts = nn::os::ConvertToTimeSpan(nn::os::GetSystemTick());
    return static_cast<std::int64_t>(ts.GetMilliSeconds());
}

void ApState::setPendingMoonLabel(const char* text, int seq, std::int64_t deadline_ms) {
    if (seq <= 0) return;  // 0 is the "empty" sentinel; bridge bug if it sends one
    // Order matters: write text + deadline BEFORE bumping published_seq with
    // release semantics. The frame thread's acquire-load synchronizes-with this
    // store, guaranteeing it sees a fully-written buffer.
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
    if (seq == 0) return false;                       // never set
    if (seq == label_last_consumed_seq) return false; // already shown for this cutscene
    if (pending_moon_label.deadline_ms != 0 &&
        nowMs() > pending_moon_label.deadline_ms) {
        // Expired (e.g. label arrived but the cutscene never fired within
        // valid_for_ms — round-trip too slow, or moon get aborted). Mark
        // consumed so we don't keep checking it.
        label_last_consumed_seq = seq;
        return false;
    }
    std::memcpy(text_out, pending_moon_label.text, kPendingMoonLabelCap);
    text_out[kPendingMoonLabelCap - 1] = '\0';
    label_last_consumed_seq = seq;
    return true;
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
        // Chicken-and-egg: PlayerHitPointData::kill is the only callsite that
        // caches the pointer, so before Mario's first organic death we have
        // nothing to call. Drop with a log; subsequent inbound kills after his
        // first death will land.
        SMOAP_LOG_INFO("[deathlink in] dropped (no cached PlayerHitPointData yet)");
        return;
    }
    SMOAP_LOG_INFO("[deathlink in] applying synthetic kill");
    synthetic_death_this_frame = true;
    smoap::hooks::synthKillMario(hp);
    synthetic_death_this_frame = false;
    last_observed_death_ms.store(now, std::memory_order_relaxed);
}

std::uint64_t ApState::hashCheck(const Check& c) {
    // FNV-1a over a canonical fixed-order serialization. Cheap, no allocations.
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
    // M4: fold the new raw fields so {stage_name, object_id} hashes uniquely.
    mix(c.stage_name);
    mix(c.object_id);
    h ^= static_cast<std::uint64_t>(c.shine_uid + 1);  // -1 -> 0
    h *= 0x100000001b3ULL;
    mix(c.hack_name);
    return h;
}

}  // namespace smoap::ap
