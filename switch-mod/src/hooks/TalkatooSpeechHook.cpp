// Talkatoo% mode — Talkatoo speech-bubble substitution.
//
// When Talkatoo% mode is on and the player consults Talkatoo, his speech bubble
// names AP-pool moons from the current kingdom instead of vanilla picks.
//
// Mechanism: trampoline GameDataFunction::tryFindShineMessage, the runtime
// moon-name-message resolver. Talkatoo's Poetter::exeWait picks a shine_index
// from rs::calcShineIndexTableNameAvailable then calls tryFindShineMessage to
// resolve it to a UTF-16 display string. We let vanilla run, then if the
// caller is a Poetter (vtable check against the resolved _ZTV7Poetter address)
// AND Talkatoo% mode is on, override the returned char16_t* with a pointer
// into a small rotation of static buffers holding an AP-pool moon name.
//
// Why message-layer substitution rather than painting a layout pane:
//   - Talkatoo's speech bubble isn't drawn by Poetter::exeWait directly —
//     exeWait composes the message, stashes the char16_t* at self+0x130,
//     and transitions to the TalkShow nerve. A downstream EventFlow paints
//     the pane via the standard SMO message-bubble pipeline.
//   - Hooking that pipeline at message-resolve time means we don't need to
//     find the pane name, the LayoutActor pointer, or replicate the EventFlow
//     bubble lifecycle. SMO renders our text the same as vanilla.
//
// Why vtable filter rather than per-callsite hook: tryFindShineMessage is
// also called from cutscenes, the pause menu, and AchievementHint. The
// vtable comparison scopes substitution to Talkatoo only — non-Talkatoo
// callers fall through to Orig untouched.
//
// Discovery provenance: see HookSymbols.hpp's Talkatoo% block (memory file
// memory/project_talkatoo_internal_names.md kept the search context that
// led here — Talkatoo's actor class is `Poetter`, found via OdysseyDecomp's
// src/Scene/ProjectActorFactory.cpp entry `{"Poetter", nullptr}` and
// confirmed by MrKatzenGaming/SMO-SeededTalkatoo, then the dynsym scan
// of SMO 1.0.0 main.nso pinned `Poetter::exeWait` to the function
// containing SeededTalkatoo's `TableHookSym` BL offset 0x3afb08).

#include "hk/hook/Trampoline.h"
#include "hk/ro/RoUtil.h"
#include "hk/types.h"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "../ap/ApProtocol.hpp"
#include "../ap/ApState.hpp"
#include "../ap/shine_lookup.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"

namespace smoap::hooks {

namespace {

// xorshift32 — tiny, no allocations, OK for "shuffle three slots from N".
// Kept for the >3-pool Fisher-Yates path inside pickThreeUncollectedFromKingdom.
std::uint32_t cheapRand(std::uint32_t& state) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    if (state == 0) state = 0xC0FFEE;
    return state;
}

// Vtable address of the Poetter (Talkatoo) class. Read once at install time
// via hk::ro::lookupSymbol("_ZTV7Poetter"). The symbol address points to the
// start of the vtable; the vptr value SMO stores in each Poetter object is
// `vtable_start + 0x18` per the Itanium C++ ABI (observed at runtime — 2026-
// 05-20 user log: vptr=0xa265a70, vtable=0xa265a58, delta=0x18). The 0x18 is
// vbase_offset(8) + offset_to_top(8) + RTTI_ptr(8) — three slots ahead of
// the first virtual function pointer.
//
// kPoetterVTableSpan: 0x400 covers _ZTV7Poetter (0x1f8) + the immediately
// following Poetter-only auxiliary symbols (_ZTT7Poetter, _ZTC7Poetter…,
// _ZTI7Poetter) which span ~0x3e0 from vtable_start. Object vptrs only ever
// point inside the primary vtable's 0x1f8 bytes during normal operation, OR
// inside a construction vtable during the brief construction window — both
// are Poetter-owned regions, so the wider window has no false-positive risk.
//
// Hooks under Talkatoo% mode are no-ops if g_poetter_vtable_addr stays zero
// (symbol unresolved) so a future SMO version that renames the vtable
// degrades to vanilla speech rather than crashing.
std::uintptr_t g_poetter_vtable_addr = 0;
constexpr std::uintptr_t kPoetterVTableSpan = 0x400;

// UTF-16 buffer rotation. The hook returns a `char16_t*` that SMO stores at
// Poetter+0x130 and reads back later via an EventFlow. The buffer must outlive
// the speech-bubble lifetime (~3–5 s in vanilla). Single-Talkatoo invariant
// holds in normal play (one Poetter per scene); the 4-slot rotation makes it
// safe even if two speech bubbles overlap, the picker re-rolls within the
// same frame, or our hook fires in re-entrant edge cases.
constexpr std::size_t kUtfBufCount = 4;
char16_t g_utf_buffers[kUtfBufCount][smoap::ap::kCheckFieldCap + 1] = {};
std::atomic<std::size_t> g_utf_buf_cursor{0};

// Promote an ASCII-printable string into one of the static UTF-16 buffers.
// SMO's display names are USen ASCII (no multibyte characters), so byte
// widening is correct. Truncates at kCheckFieldCap-1 to leave a null cell.
const char16_t* asciiToUtf16BufStatic(const char* src) {
    const auto slot = g_utf_buf_cursor.fetch_add(1, std::memory_order_relaxed)
                      % kUtfBufCount;
    char16_t* dst = g_utf_buffers[slot];
    std::size_t i = 0;
    while (src[i] != '\0' && i < smoap::ap::kCheckFieldCap) {
        dst[i] = static_cast<char16_t>(static_cast<unsigned char>(src[i]));
        ++i;
    }
    dst[i] = 0;
    return dst;
}

// One-shot diagnostic gates, demoted from INFO → DEBUG once Phase 3 was
// verified end-to-end (2026-05-21). Kept (rather than removed) because they
// localize which stage of the trampoline fails if a future SMO version
// renames Poetter or shifts vtable layout — turn on with
// `-DSMOAP_LOG_FORWARD_MIN_LEVEL=0` at build time. The substitute-call log
// remains at INFO since it fires only on player input.
std::atomic<bool> g_logged_first_call{false};
std::atomic<bool> g_logged_first_poetter{false};

// Hardcoded probe strings for the bring-up check. When `talkatoo_mode` is on
// AND we fail to find any AP-pool moons for the current kingdom (or whenever
// the bring-up env flag is asserted), Talkatoo speaks these instead of any
// vanilla moon name. They're intentionally non-vanilla so a single
// substitution is unambiguously visible in the speech bubble.
constexpr const char* kHardcodedProbe[3] = {
    "BK Moon #1",
    "BK Moon #2",
    "BK Moon #3",
};

// Per-kingdom Talkatoo visit counter. The substitute hook in the AP-pool
// path advances slot[bit] on each visit and uses (counter % 3) for
// pick_idx, producing a deterministic 0->1->2->0 cycle through the
// kingdom's window.
std::atomic<std::size_t> g_talkatoo_visit_counters[
    smoap::ap::ApState::kTalkatooKingdomCount] = {};

}  // namespace

// Snapshot the AP-pool for `kingdom_bit` and return up to 3 uncollected moon
// display names into `out[0..3]`. `out_count` (must be non-null) receives the
// number of names actually written (0..3).
//
// `is_collected` is a caller-supplied predicate that returns true if the
// shine_id is already collected. Currently the hook callback passes nullptr
// (so all AP-pool moons in the kingdom are eligible) — the predicate hook
// is retained for unit testing and for a future GameDataFile::isGotShine
// integration if we want to skip moons already collected this session.
//
// Returns true if at least one moon was picked. Empty pool, mode-off, or
// all-collected return false (caller falls back to vanilla speech).
//
// NOTE: caller-owned output buffers per the M6.1 allocator-safety contract.
bool pickThreeUncollectedFromKingdom(
    int kingdom_bit,
    bool (*is_collected)(const char* shine_id, void* ctx),
    void* is_collected_ctx,
    char out[3][smoap::ap::kCheckFieldCap],
    std::size_t* out_count)
{
    if (out_count == nullptr) return false;
    *out_count = 0;
    out[0][0] = out[1][0] = out[2][0] = '\0';

    using Pool = smoap::ap::ApState::TalkatooKingdomPool;
    char snapshot[Pool::kMaxMoons][smoap::ap::kCheckFieldCap];
    const std::size_t n = smoap::ap::ApState::instance().snapshotTalkatooKingdom(
        kingdom_bit, snapshot, Pool::kMaxMoons);
    if (n == 0) return false;

    // Two-pass filter: build a list of uncollected indices, then pick up to
    // 3 random ones via Fisher-Yates partial shuffle. Indices fit in u8 (96 max).
    std::uint8_t idx_buf[Pool::kMaxMoons];
    std::size_t idx_count = 0;
    for (std::size_t i = 0; i < n; ++i) {
        const bool taken = is_collected
            ? is_collected(snapshot[i], is_collected_ctx)
            : false;
        if (!taken) {
            idx_buf[idx_count++] = static_cast<std::uint8_t>(i);
        }
    }
    if (idx_count == 0) return false;

    // Phase 5 (Gap #3 follow-up, 2026-05-21): when idx_count <= 3 (which
    // is the Phase 5 cursor-window case — bridge ships exactly 3 ordered
    // moons per kingdom), preserve input order so the per-kingdom visit
    // counter in the substitute hook produces a stable A->B->C->A cycle.
    // Fisher-Yates re-shuffling each visit broke that: the counter would
    // cycle pick_idx 0/1/2, but slot 0 was a different moon each visit,
    // so the player saw repeats. For idx_count > 3 (Phase 4 fallback path,
    // bridge ships a larger pool), keep Fisher-Yates so the Switch picks
    // a different 3-subset per visit — preserves variety in the absence
    // of a bridge-side cursor.
    if (idx_count <= 3) {
        const std::size_t pick = idx_count;
        for (std::size_t k = 0; k < pick; ++k) {
            std::memcpy(out[k], snapshot[idx_buf[k]], smoap::ap::kCheckFieldCap);
            out[k][smoap::ap::kCheckFieldCap - 1] = '\0';
        }
        *out_count = pick;
        return true;
    }

    auto seed = static_cast<std::uint32_t>(
        smoap::ap::ApState::nowMs() & 0xFFFFFFFFu);
    if (seed == 0) seed = 0xC0FFEE;

    constexpr std::size_t kPick = 3;
    for (std::size_t k = 0; k < kPick; ++k) {
        const std::size_t remain = idx_count - k;
        const std::size_t r = cheapRand(seed) % remain;
        const std::uint8_t winner = idx_buf[r];
        idx_buf[r] = idx_buf[remain - 1];  // Fisher-Yates swap
        std::memcpy(out[k], snapshot[winner], smoap::ap::kCheckFieldCap);
        out[k][smoap::ap::kCheckFieldCap - 1] = '\0';
    }
    *out_count = kPick;
    return true;
}

namespace {

// Cheap "is this actor a Poetter?" — read the vptr at actor[0], range-check
// against the Poetter vtable address. Returns false when the vtable hasn't
// been resolved (degraded mode: Talkatoo% acts as no-op, vanilla speech runs).
bool actorIsPoetter(const void* actor) {
    if (actor == nullptr || g_poetter_vtable_addr == 0) return false;
    const auto vptr = *reinterpret_cast<const std::uintptr_t*>(actor);
    return vptr >= g_poetter_vtable_addr
        && vptr <  g_poetter_vtable_addr + kPoetterVTableSpan;
}

// Forward-declared opaque types — we never deref these, we just pass `actor`
// through to orig() and read its vptr for the Poetter check. Pulling in the
// full al::LiveActor / al::IUseMessageSystem headers here would drag in the
// OdysseyHeaders mostly for nothing; the trampoline signature matches the
// symbol's mangled shape (PKN2al9LiveActorE, PKN2al17IUseMessageSystemE) so
// we declare them at the right address-space level for ABI compatibility.
struct PoetterOpaqueLiveActor;
struct PoetterOpaqueMessageSystem;

HkTrampoline<const char16_t*,
             const PoetterOpaqueLiveActor*,
             const PoetterOpaqueMessageSystem*,
             int, int> tryFindShineMessageHook =
    hk::hook::trampoline([](const PoetterOpaqueLiveActor* actor,
                            const PoetterOpaqueMessageSystem* sys,
                            int world_id, int index) -> const char16_t* {
        const char16_t* vanilla = tryFindShineMessageHook.orig(actor, sys, world_id, index);

        // Probe 1: confirm the trampoline is wired at all. Logs on the first
        // call only — non-Talkatoo callers (cutscene cards, pause menu) all
        // route through here too, so this fires shortly after a save loads.
        bool expected = false;
        if (g_logged_first_call.compare_exchange_strong(
                expected, true, std::memory_order_relaxed)) {
            const auto vptr_val = (actor != nullptr)
                ? *reinterpret_cast<const std::uintptr_t*>(actor)
                : 0;
            SMOAP_LOG_DEBUG("[talkatoo] tryFindShineMessage FIRST CALL "
                            "actor=%p vptr=0x%lx Poetter_vtable=0x%lx "
                            "world_id=%d index=%d talkatoo_mode=%d",
                            static_cast<const void*>(actor),
                            vptr_val,
                            g_poetter_vtable_addr,
                            world_id, index,
                            static_cast<int>(smoap::ap::ApState::instance()
                                .talkatoo_mode.load(std::memory_order_acquire)));
        }

        if (!actorIsPoetter(actor)) {
            return vanilla;
        }

        // Probe 2: first time we see a Poetter caller.
        expected = false;
        if (g_logged_first_poetter.compare_exchange_strong(
                expected, true, std::memory_order_relaxed)) {
            const auto vptr_val = *reinterpret_cast<const std::uintptr_t*>(actor);
            SMOAP_LOG_DEBUG("[talkatoo] FIRST POETTER CALL "
                            "actor=%p vptr=0x%lx (offset within vtable=0x%lx) "
                            "world_id=%d index=%d",
                            static_cast<const void*>(actor),
                            vptr_val,
                            vptr_val - g_poetter_vtable_addr,
                            world_id, index);
        }

        // Gate substitution on talkatoo_mode_on. Without this gate, non-
        // Talkatoo% players see "BK Moon #N" instead of vanilla Talkatoo speech.
        const bool talkatoo_mode_on =
            smoap::ap::ApState::instance().talkatoo_mode.load(
                std::memory_order_acquire);
        if (!talkatoo_mode_on) {
            return vanilla;
        }

        const std::uint8_t bit = smoap::game::kingdomBitForWorldId(world_id);

        // Try AP-pool first (still preferred when both mode and pool exist).
        char picks[3][smoap::ap::kCheckFieldCap];
        std::size_t n = 0;
        const bool have_ap_pool =
            talkatoo_mode_on &&
            bit != 0xff &&
            pickThreeUncollectedFromKingdom(static_cast<int>(bit),
                                            nullptr, nullptr,
                                            picks, &n) &&
            n > 0;

        // Hardcoded probe fallback. Two purposes:
        //   1. Bring-up check — if the AP-pool wire path is broken,
        //      substitution still produces a visibly distinctive bubble so
        //      we know the SMO-side hook works.
        //   2. Graceful fallback — when the player hasn't received the pool
        //      for this kingdom yet, we'd rather show "BK Moon #N" than
        //      vanilla names that the player can't actually find under
        //      Talkatoo% rules.
        const char* chosen_ascii;
        std::size_t pool_size;
        std::size_t pick_idx;
        bool chose_padding = false;
        if (have_ap_pool) {
            const std::size_t real_n = n;
            // Pad picks[] with probe strings if the AP pool returned <3
            // real entries — keeps Talkatoo's rotation at a consistent 3
            // slots so the player always sees a stable A/B/C cycle.
            for (std::size_t k = real_n; k < 3; ++k) {
                std::memcpy(picks[k], kHardcodedProbe[k],
                            std::strlen(kHardcodedProbe[k]) + 1);
            }
            // Per-kingdom counter for strict 0/1/2 cycling.
            auto& counter = g_talkatoo_visit_counters[bit];
            pick_idx = counter.fetch_add(1, std::memory_order_relaxed) % 3;
            pool_size = 3;
            chosen_ascii = picks[pick_idx];
            chose_padding = (pick_idx >= real_n);
        } else {
            // Probe fallback. Cycle deterministically through #1/#2/#3
            // across consecutive Poetter visits via an atomic counter.
            static std::atomic<std::size_t> probe_cursor{0};
            pick_idx = probe_cursor.fetch_add(1, std::memory_order_relaxed) % 3;
            pool_size = 3;
            chosen_ascii = kHardcodedProbe[pick_idx];
        }
        const char16_t* substitute = asciiToUtf16BufStatic(chosen_ascii);

        // Phase 4: publish the named moon to ApState. Block path in
        // MoonGetHook consults isMoonNamed before letting setGotShine run.
        // Skipped for the PROBE fallback and for the AP-path's padded probe
        // slots (chose_padding flags a pick that landed on one of those).
        if (have_ap_pool && !chose_padding) {
            const int shine_uid =
                smoap::game::shineUidByDisplayName(chosen_ascii);
            if (shine_uid >= 0) {
                smoap::ap::ApState::instance().markMoonNamed(shine_uid);
            } else {
                SMOAP_LOG_WARN("[talkatoo] markMoonNamed: '%s' not found "
                               "in shine_table — collection block won't see "
                               "this moon as named",
                               chosen_ascii);
            }
        }

        // Log every substitute call — Talkatoo fires the hook once per visit.
        SMOAP_LOG_INFO("[talkatoo] substituting: world_id=%d kingdom_bit=%u "
                       "shine_index=%d mode=%d -> %s pick %u/%zu '%s'",
                       world_id, static_cast<unsigned>(bit), index,
                       static_cast<int>(talkatoo_mode_on),
                       have_ap_pool ? "AP" : "PROBE",
                       static_cast<unsigned int>(pick_idx),
                       pool_size, chosen_ascii);
        return substitute;
    });

}  // namespace

void installTalkatooSpeechHook() {
    // Resolve Poetter's vtable address. Without it the trampoline degrades to
    // a no-op (Talkatoo% mode flag honored, but every call passes through to
    // Orig because actorIsPoetter() returns false). That keeps the module
    // running on a hypothetical future SMO patch that renames the vtable —
    // vanilla speech survives, only Talkatoo% silently disables.
    const ptr vt_addr = hk::ro::lookupSymbol(smoap::sym::kPoetterVTable);
    if (vt_addr == 0) {
        SMOAP_LOG_ERROR("[talkatoo] lookupSymbol FAILED sym=%s — "
                        "Talkatoo%% mode will be inert (vanilla speech)",
                        smoap::sym::kPoetterVTable);
    } else {
        SMOAP_LOG_INFO("[talkatoo] Poetter vtable @ 0x%lx",
                       static_cast<unsigned long>(vt_addr));
        g_poetter_vtable_addr = static_cast<std::uintptr_t>(vt_addr);
    }

    SMOAP_LOG_INFO("installing TryFindShineMessageHook -> %s",
                   smoap::sym::kGameDataFunctionTryFindShineMessage);
    tryFindShineMessageHook.installAtSym<
        "_ZN16GameDataFunction19tryFindShineMessageEPKN2al9LiveActorEPKNS0_17IUseMessageSystemEii">();
}

}  // namespace smoap::hooks
