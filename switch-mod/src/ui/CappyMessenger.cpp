#include "CappyMessenger.hpp"

#include <cstdarg>
#include <cstdio>
#include <cstring>

// Logging is a thin wrapper; in host tests it expands to no-ops (see
// SMOAP_HOST_TEST guard inside Log.hpp). We use SMOAP_LOG_INFO directly here
// rather than through a layer because the diagnostic frequency is low
// (per-item, not per-frame).
#include "../util/Log.hpp"

namespace smoap::ui {

namespace {

// Function-pointer cache for the rs:: calls — populated by
// installCappyMessengerSymbols() in src/hooks/CappyMessageHook.cpp at module
// init. We can't use HOOK_DEFINE_TRAMPOLINE on these (we're not hooking, just
// calling); nn::ro::LookupSymbol gives us the raw entry-point address.
//
// The host-test build never installs these, so the function pointers stay
// null and tryPump's null-guard short-circuits with no-op behavior.

using TryShowCapMessagePriorityLowFn =
    bool (*)(const void* /*IUseSceneObjHolder*/,
             const char* /*label*/,
             int /*delay*/,
             int /*wait*/);
using IsActiveCapMessageFn = bool (*)(const void* /*IUseSceneObjHolder*/);

TryShowCapMessagePriorityLowFn s_tryShow = nullptr;
IsActiveCapMessageFn           s_isActive = nullptr;

}  // namespace

void setCappyMessengerRsCalls(TryShowCapMessagePriorityLowFn tryShow,
                              IsActiveCapMessageFn isActive);

void setCappyMessengerRsCalls(TryShowCapMessagePriorityLowFn tryShow,
                              IsActiveCapMessageFn isActive) {
    s_tryShow = tryShow;
    s_isActive = isActive;
}

// ----------------------------------------------------------------------------
// CappyMessenger
// ----------------------------------------------------------------------------

CappyMessenger& CappyMessenger::instance() {
    static CappyMessenger s;
    return s;
}

void CappyMessenger::enqueue(const smoap::ap::Item& item,
                             const char* local_slot,
                             bool suppress) {
    if (!shouldShowCappyMsg(item.kind, item.from, local_slot, suppress)) {
        return;
    }
    if (live_count_ >= kQueueCap) {
        // Drop newest rather than evicting — the user picked "items from
        // other players" + "suppress on bulk replay" as their filters; the
        // remaining case where the queue overflows is a rare burst from a
        // single sender and dropping the tail is safer than displacing
        // already-queued items the player has been waiting to see.
        SMOAP_LOG_WARN("[cappy] queue full (cap=%u) — dropping item name='%s' from=%s",
                       static_cast<unsigned>(kQueueCap),
                       item.name, item.from);
        return;
    }
    Entry& e = queue_[tail_];
    formatCappyMsg(e.text, sizeof(e.text), item);
    e.live = true;
    tail_ = (tail_ + 1) % kQueueCap;
    ++live_count_;
    SMOAP_LOG_INFO("[cappy] enqueued text='%s' (queue=%u)",
                   e.text, static_cast<unsigned>(live_count_));
}

void CappyMessenger::enqueueSystem(const char* text) {
    if (!text || text[0] == '\0') return;
    if (live_count_ >= kQueueCap) {
        SMOAP_LOG_WARN("[cappy] queue full (cap=%u) — dropping system msg='%s'",
                       static_cast<unsigned>(kQueueCap), text);
        return;
    }
    Entry& e = queue_[tail_];
    // Verbatim copy — no "Got X from Y!" wrapping for system messages. Same
    // fixed-buffer pattern as Entry::text initialization; no allocator path.
    std::size_t i = 0;
    while (i + 1 < sizeof(e.text) && text[i] != '\0') {
        e.text[i] = text[i];
        ++i;
    }
    e.text[i] = '\0';
    e.live = true;
    tail_ = (tail_ + 1) % kQueueCap;
    ++live_count_;
    SMOAP_LOG_INFO("[cappy] enqueued system text='%s' (queue=%u)",
                   e.text, static_cast<unsigned>(live_count_));
}

void CappyMessenger::tryPump(const void* scene) {
    // Scene-stability bookkeeping. We tick this even when there's nothing
    // queued so the settle counter is warm by the time an item arrives.
    if (scene != last_scene_) {
        if (last_scene_ != nullptr || scene != nullptr) {
            SMOAP_LOG_INFO("[cappy] scene changed last=%p new=%p — resetting "
                           "settle counter",
                           last_scene_, scene);
        }
        last_scene_ = scene;
        settle_frames_ = 0;
        // If a balloon was active on the previous scene, force-release the
        // buffer. Two reasons: (1) CapMessage state is per-scene, so SMO has
        // already discarded the balloon — keeping buffer_in_use_=true would
        // strand the next dispatch waiting for an isActive that will never
        // flip; (2) the buffer-in-use probe below would otherwise call
        // s_isActive(new_scene) before the new scene's CapMessageDirector
        // finishes registering with its SceneObjHolder, which NULL-derefs.
        // Releasing here is safe — SMO can no longer read the old buffer.
        if (buffer_in_use_) {
            SMOAP_LOG_INFO("[cappy] scene change while buffer in-use — "
                           "force-releasing");
            buffer_in_use_ = false;
        }
    } else if (scene != nullptr) {
        ++settle_frames_;
    }

    if (live_count_ == 0) return;
    if (!scene) return;            // boot scene / transition — try next frame
    if (!s_tryShow) return;        // host test build, or symbol resolve failed
    if (!s_isActive) return;

    // Settle gate: don't poke the CapMessage director until the scene has
    // been stable for kSceneSettleFrames consecutive frames. New StageScene
    // construction races with SceneObjHolder child registration; calling
    // rs::isActiveCapMessage before the director registers NULL-derefs.
    if (settle_frames_ < kSceneSettleFrames) {
        // Throttle the log so we don't spam every frame — once at the start
        // is plenty for diagnostics.
        if (settle_frames_ == 0) {
            SMOAP_LOG_INFO("[cappy] waiting for scene settle (%u/%u frames) "
                           "before first dispatch; queue=%u scene=%p",
                           static_cast<unsigned>(settle_frames_),
                           static_cast<unsigned>(kSceneSettleFrames),
                           static_cast<unsigned>(live_count_),
                           scene);
        }
        return;
    }

    // Don't rotate the buffer while a balloon is on screen — SMO is reading
    // from it. Re-check every frame; once isActive flips false the balloon
    // is fully gone and we can release the buffer.
    if (buffer_in_use_) {
        SMOAP_LOG_DEBUG("[cappy] probe isActive scene=%p (buffer-in-use path)", scene);
        const bool still_active = s_isActive(scene);
        SMOAP_LOG_DEBUG("[cappy] probe isActive returned %d", still_active);
        if (still_active) {
            return;
        }
        // Balloon finished; release and let head advance.
        buffer_in_use_ = false;
        SMOAP_LOG_INFO("[cappy] balloon released; buffer free");
    }

    // Idle pre-flight isActive check: this is the FIRST call into rs::
    // territory for a brand-new dispatch. Bracket with DEBUG logs — this
    // runs every frame while items are queued and CapMessage is busy, so
    // INFO would flood the sink and the wire-forward path. The meaningful
    // transitions (`dispatched`, `dropping head after N frames`, `balloon
    // released`) are logged at INFO separately.
    SMOAP_LOG_DEBUG("[cappy] >> isActive(scene=%p) [pre-flight]", scene);
    const bool nintendo_active = s_isActive(scene);
    SMOAP_LOG_DEBUG("[cappy] << isActive returned %d", nintendo_active);

    // If isActive is true for any *other* reason (Nintendo CapMessage in
    // flight), back off to next frame. Also bump retry counter so a stuck
    // state eventually drops the head item with a log line rather than
    // queueing indefinitely.
    if (nintendo_active) {
        ++retry_frames_;
        if (retry_frames_ >= kMaxRetryFrames) {
            SMOAP_LOG_WARN("[cappy] dropping head after %u frames waiting "
                           "for CapMessage clear (text='%s')",
                           static_cast<unsigned>(retry_frames_),
                           queue_[head_].text);
            queue_[head_].live = false;
            head_ = (head_ + 1) % kQueueCap;
            --live_count_;
            retry_frames_ = 0;
        }
        return;
    }

    // Prepare the substitution buffer with the head item's text. Conversion
    // failure (e.g. malformed UTF-8) writes nothing past the NUL — Nintendo
    // will render an empty balloon. We log loudly in that case.
    Entry& e = queue_[head_];
    const std::size_t n = utf8ToUtf16(e.text, buffer_, kBufferUtf16Words);
    if (n == 0 && e.text[0] != '\0') {
        SMOAP_LOG_WARN("[cappy] utf8->utf16 produced empty buffer for text='%s' "
                       "— dropping head", e.text);
        queue_[head_].live = false;
        head_ = (head_ + 1) % kQueueCap;
        --live_count_;
        retry_frames_ = 0;
        return;
    }
    buffer_in_use_ = true;

    // CAVEAT: rs::tryShowCapMessagePriorityLow's positional arg order is
    // (holder, label, waitTime, delayTime). Confirmed by disassembly of
    // both the function (0x23a910) and the CapMessageShowInfo ctor
    // (0x23a540): the function passes its 3rd arg to mWaitTime and its
    // 4th arg to mDelayTime — opposite of the natural reading. Wait FIRST,
    // delay SECOND. (Spent a playtest pass thinking otherwise — the bubble
    // was getting a 5-second delay-before-appearing and a 0-tick on-screen
    // hold.)
    SMOAP_LOG_INFO("[cappy] >> tryShow(scene=%p label='%s' wait=%d delay=0) "
                   "text='%s' utf16_words=%u",
                   scene, kArchipelagoLabel, kWaitTicks, e.text,
                   static_cast<unsigned>(n));
    const bool ok = s_tryShow(scene, kArchipelagoLabel,
                              /*waitTime=*/kWaitTicks,
                              /*delayTime=*/0);
    SMOAP_LOG_INFO("[cappy] << tryShow returned %d", ok);

    if (!ok) {
        // Priority-low queue declined us (probably a rare race between our
        // isActive check and the system actually scheduling). Release the
        // buffer so the next pump can retry — the text in queue_[head_] is
        // unchanged.
        buffer_in_use_ = false;
        ++retry_frames_;
        return;
    }

    SMOAP_LOG_INFO("[cappy] dispatched text='%s' (queue %u -> %u)",
                   e.text,
                   static_cast<unsigned>(live_count_),
                   static_cast<unsigned>(live_count_ - 1));
    markDispatched();
}

void CappyMessenger::markDispatched() {
    if (live_count_ == 0) return;
    queue_[head_].live = false;
    head_ = (head_ + 1) % kQueueCap;
    --live_count_;
    retry_frames_ = 0;
    // buffer_in_use_ stays true until isActive flips false in tryPump —
    // SMO needs the buffer pointer to outlive the show() call.
}

const char16_t* CappyMessenger::lookupSubstitution(const char* label) const {
    if (!label) return nullptr;
    // Cheap-first: most lookups are NOT ours, so the first char check filters
    // 99.9% of calls before the full strcmp. kArchipelagoLabel starts with 'A'.
    if (label[0] != 'A') return nullptr;
    if (std::strcmp(label, kArchipelagoLabel) != 0) return nullptr;
    // Even when matched: if our buffer hasn't been filled yet (e.g. someone
    // called tryShowCapMessage with our label before pump() filled the
    // buffer — shouldn't happen, defense in depth), return nullptr so
    // tryGetText behaves like a normal "no such label" lookup.
    if (!buffer_in_use_) return nullptr;
    return buffer_;
}

void CappyMessenger::resetForTest() {
    for (auto& e : queue_) {
        e.text[0] = '\0';
        e.live = false;
    }
    head_ = 0;
    tail_ = 0;
    live_count_ = 0;
    retry_frames_ = 0;
    for (auto& c : buffer_) c = 0;
    buffer_in_use_ = false;
    last_scene_ = nullptr;
    settle_frames_ = 0;
}

// ----------------------------------------------------------------------------
// Free helpers — host-testable
// ----------------------------------------------------------------------------

bool shouldShowCappyMsg(smoap::ap::ItemKind kind,
                        const char* from,
                        const char* local_slot,
                        bool suppress) {
    if (suppress) return false;
    if (!from || from[0] == '\0') return false;
    using K = smoap::ap::ItemKind;
    if (kind == K::Other) return false;
    if (local_slot && local_slot[0] != '\0'
        && std::strcmp(from, local_slot) == 0) {
        return false;
    }
    return true;
}

// Rewrite table — one row per suffix we shorten. Every rule drops just
// " Kingdom" so the displayed item type stays full-text ("Power Moon" /
// "Multi-Moon" / "Sticker"). The bridge-side `format_moon_label` uses the
// same shape ("Got Cascade Power Moon!"), so a moon offline-collected and
// surfaced via the inbound-ItemMsg path now reads the same as one
// surfaced via the live cutscene label. Order doesn't matter — suffixes
// are disjoint. Kept const so the table sits in rodata.
namespace {
struct ShortenRule {
    const char* suffix;
    const char* replacement;
};
constexpr ShortenRule kShortenRules[] = {
    {" Kingdom Power Moon",  " Power Moon"},
    {" Kingdom Multi-Moon",  " Multi-Moon"},
    {" Kingdom Sticker",     " Sticker"},
};
}  // namespace

std::size_t shortenItemNameForBubble(const char* src,
                                     char* dst,
                                     std::size_t dst_cap) {
    if (!dst || dst_cap == 0) return 0;
    dst[0] = '\0';
    if (!src) return 0;

    const std::size_t src_len = std::strlen(src);

    // Try each rule; first matching suffix wins (they're disjoint by design).
    for (const auto& rule : kShortenRules) {
        const std::size_t suf_len = std::strlen(rule.suffix);
        if (src_len <= suf_len) continue;  // need a non-empty prefix
        if (std::strcmp(src + src_len - suf_len, rule.suffix) != 0) continue;

        const std::size_t prefix_len = src_len - suf_len;
        const std::size_t rep_len = std::strlen(rule.replacement);
        const std::size_t out_len = prefix_len + rep_len;
        if (out_len + 1 > dst_cap) {
            // Rewrite wouldn't fit — fall through to verbatim copy below.
            break;
        }
        std::memcpy(dst, src, prefix_len);
        std::memcpy(dst + prefix_len, rule.replacement, rep_len);
        dst[out_len] = '\0';
        return out_len;
    }

    // Verbatim copy (truncate to dst_cap-1 if oversize).
    const std::size_t copy_len = (src_len < dst_cap) ? src_len : dst_cap - 1;
    std::memcpy(dst, src, copy_len);
    dst[copy_len] = '\0';
    return copy_len;
}

int formatCappyMsg(char* buf, std::size_t cap, const smoap::ap::Item& item) {
    if (!buf || cap == 0) return 0;
    // Apply the cosmetic shortener BEFORE formatting so the fit-check and
    // sender-truncation logic both operate on the displayed name length.
    // Sized for the longest apworld item name (27 chars) plus headroom.
    char short_name[64];
    shortenItemNameForBubble(
        item.name[0] == '\0' ? "?" : item.name,
        short_name, sizeof(short_name));
    const char* name = short_name;
    const char* sender = item.from[0] == '\0' ? "?" : item.from;

    // Synthetic-sender sentinels — when the bridge tags the from-field with
    // one of these, drop the "from <sender>" suffix entirely: the message
    // reads "Got X!" instead of "Got X from (offline)!" / "Got X from
    // (self)!". Neither sentinel can collide with a real player name
    // (parens aren't legal in slot names) so the strcmp is unambiguous and
    // short-circuits the whole truncation branch below.
    //   (offline) — M6 phase C reconcile of a bridge-offline collection
    //   (self)    — AP echo of a user-issued /send_location
    if (std::strcmp(sender, kReconcileFromSentinel) == 0
        || std::strcmp(sender, kManualGrantSentinel) == 0) {
        int n = std::snprintf(buf, cap, "Got %s!", name);
        if (n < 0) {
            buf[0] = '\0';
            return 0;
        }
        return n;
    }

    // Preferred form: full name + full sender. Fit-test against kSoftMaxChars
    // before settling — the buffer cap is much larger but the speech bubble
    // wraps awkwardly past ~60 visible chars.
    int full = std::snprintf(buf, cap, "Got %s from %s!", name, sender);
    if (full < 0) {
        buf[0] = '\0';
        return 0;
    }
    if (static_cast<std::size_t>(full) <= CappyMessenger::kSoftMaxChars) {
        return full;
    }

    // Doesn't fit. Per user preference: keep the item name in full,
    // truncate the sender with "..." before the trailing "!".
    //
    // Layout: "Got " + name + " from " + sender_trunc + "...!"
    // Fixed overhead: 4 + 6 + 4 = 14 chars (assuming "...!" ends it)
    constexpr std::size_t kOverhead = 4 + 6 + 4;
    const std::size_t name_len = std::strlen(name);

    if (name_len + kOverhead > CappyMessenger::kSoftMaxChars) {
        // Even just "Got NAME from ...!" can't fit — drop the "from"
        // clause entirely. If the name ALONE is over budget, snprintf
        // will still respect cap and truncate; we accept that as a soft
        // fallback (better to show a truncated item name than nothing).
        int n = std::snprintf(buf, cap, "Got %s!", name);
        if (n < 0) {
            buf[0] = '\0';
            return 0;
        }
        return n;
    }

    const std::size_t sender_budget =
        CappyMessenger::kSoftMaxChars - name_len - kOverhead;
    // .*s precision is byte count, fine for the ASCII apworld+slot names
    // we deal with. For non-ASCII senders we may cut mid-codepoint, but
    // utf8ToUtf16 already silently skips malformed leading bytes so the
    // rendered string will be slightly shorter rather than corrupt.
    int n = std::snprintf(buf, cap, "Got %s from %.*s...!",
                          name,
                          static_cast<int>(sender_budget),
                          sender);
    if (n < 0) {
        buf[0] = '\0';
        return 0;
    }
    return n;
}

std::size_t utf8ToUtf16(const char* src,
                        char16_t* dst,
                        std::size_t dst_cap_words) {
    if (!dst || dst_cap_words == 0) return 0;
    dst[0] = 0;
    if (!src) return 0;

    std::size_t out = 0;
    const unsigned char* p = reinterpret_cast<const unsigned char*>(src);

    while (*p && out + 1 < dst_cap_words) {  // reserve 1 word for terminator
        char32_t cp = 0;
        int extra = 0;
        if (*p < 0x80) {
            cp = *p++;
        } else if ((*p & 0xE0) == 0xC0) {
            cp = *p++ & 0x1F;
            extra = 1;
        } else if ((*p & 0xF0) == 0xE0) {
            cp = *p++ & 0x0F;
            extra = 2;
        } else if ((*p & 0xF8) == 0xF0) {
            cp = *p++ & 0x07;
            extra = 3;
        } else {
            // Invalid lead byte — skip and resync.
            ++p;
            continue;
        }
        bool bad = false;
        for (int i = 0; i < extra; ++i) {
            if ((*p & 0xC0) != 0x80) { bad = true; break; }
            cp = (cp << 6) | (*p++ & 0x3F);
        }
        if (bad) continue;
        if (cp < 0x10000) {
            dst[out++] = static_cast<char16_t>(cp);
        } else if (out + 2 < dst_cap_words) {
            cp -= 0x10000;
            dst[out++] = static_cast<char16_t>(0xD800 | (cp >> 10));
            dst[out++] = static_cast<char16_t>(0xDC00 | (cp & 0x3FF));
        } else {
            break;
        }
    }
    dst[out] = 0;
    return out;
}

}  // namespace smoap::ui
