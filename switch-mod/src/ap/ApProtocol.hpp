// Wire format mirror for the Switch <-> Bridge channel.
// Authoritative spec lives in docs/wire-protocol.md and bridge/smo_ap_bridge/protocol.py.
//
// Single persistent TCP connection. Each message is one '\n'-terminated line
// of UTF-8 JSON. Field "t" is the message type. Max line: 8 KiB.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "../util/Json.hpp"

namespace smoap::ap {

inline constexpr std::size_t kMaxLineBytes = 8 * 1024;

enum class ItemKind : std::uint8_t {
    Moon = 0,
    Capture = 1,
    Other = 4,
};

const char* toWire(ItemKind k);          // "moon" / "capture" / ...
ItemKind fromWire(const char* s);        // returns Other for unknown
ItemKind fromWire(const std::string& s); // legacy overload — forwards to char*

// Switch -> Bridge ----------------------------------------------------------

struct Hello {
    std::string mod_ver;
    std::string smo_ver;
    std::string cap_table_hash;
};

// Fixed-size char buffer used for Check string fields. libstdc++'s
// std::string allocator path NULL-derefs in our subsdk9 context for any
// string that exceeds SSO (~15 bytes), same root cause as the std::set
// crash. Keeping checks allocation-free here means the frame thread can
// produce them without touching the broken allocator. 64 bytes covers every
// stage name, moon objectId, capture, and kingdom string SMO emits.
inline constexpr std::size_t kCheckFieldCap = 64;

// Inbound-side caps. DecodedMsg fields use these — see comment on the
// structs below for the empirical justification (the worker-thread
// std::string allocator NULL-derefs once heap state has drifted, observed
// 2026-05-16 in parseCheckedReplay → readIntoString on a 20-char shine_id).
inline constexpr std::size_t kMediumFieldCap = 128;  // shine_id, name, ctx
inline constexpr std::size_t kLongFieldCap   = 256;  // err msgs, kill cause
inline constexpr std::size_t kPrintFieldCap  = 512;  // bridge print.text

// Copy a C-string into a fixed buffer, null-terminating. Null src -> empty.
inline void copyCheckField(char (&dst)[kCheckFieldCap], const char* src) {
    if (!src) { dst[0] = '\0'; return; }
    std::size_t i = 0;
    while (i + 1 < kCheckFieldCap && src[i] != '\0') {
        dst[i] = src[i];
        ++i;
    }
    dst[i] = '\0';
}

// Generic fixed-buffer copy (C-string source). Same shape as copyCheckField
// but works for any compile-time buffer size — used by inbound DecodedMsg
// fields which use varying sizes (64/128/256/512).
template <std::size_t N>
inline void copyFixedField(char (&dst)[N], const char* src) {
    if (!src) { dst[0] = '\0'; return; }
    std::size_t i = 0;
    while (i + 1 < N && src[i] != '\0') {
        dst[i] = src[i];
        ++i;
    }
    dst[i] = '\0';
}

// Length-bounded variant — used when the source is a string_view from the
// inbound JSON Reader (which does NOT null-terminate). Truncates and always
// null-terminates the destination.
template <std::size_t N>
inline void copyFixedFieldN(char (&dst)[N], const char* src, std::size_t n) {
    const std::size_t take = (n < N - 1) ? n : (N - 1);
    for (std::size_t i = 0; i < take; ++i) dst[i] = src[i];
    dst[take] = '\0';
}

struct Check {
    ItemKind kind = ItemKind::Moon;
    // legacy resolved fields (still used by inbound items / kingdom)
    char kingdom[kCheckFieldCap] = {};
    char shine_id[kCheckFieldCap] = {};
    char cap[kCheckFieldCap] = {};
    // M4 raw identifiers — bridge resolves these via shine_map.json / capture_map.json
    char stage_name[kCheckFieldCap] = {};  // moons: ShineInfo::stageName
    char object_id[kCheckFieldCap] = {};   // moons: ShineInfo::objectId
    int shine_uid = -1;                    // moons: ShineInfo::shineId
    char hack_name[kCheckFieldCap] = {};   // captures: PlayerHackKeeper::getCurrentHackName
    // M6 phase A.5: per-session monotonic sequence id. Bridge echoes it
    // back in MoonLabelMsg.seq so the cutscene-label hook can tell which
    // pending label belongs to which moon. 0 = absent (legacy path); the
    // bridge skips Channel A when seq == 0. The Switch fills this from a
    // simple counter in MoonGetHook before sending the Check.
    int seq = 0;
};

struct Status {
    std::string kingdom;
    int scenario = -1;
    int moons_collected = -1;
    std::string stage_name;  // M4: raw stage at the time of the scenario flip
};

struct Goal {};

struct Death {
    std::int64_t ts_ms = 0;
};

struct Ping {
    std::int64_t ts_ms = 0;
};

// Forwarded log line — every smoap::util::log() call above the configured
// threshold is mirrored into the bridge's "Switch" tab. Fixed buffers
// because the producer can be ANY thread (frame, worker, hook callbacks)
// and libstdc++'s std::string allocator NULL-derefs on the worker once heap
// state has drifted — same M6.1 rationale as Check.
inline constexpr std::size_t kLogLevelCap = 8;   // "debug", "info", "warn", "error"
inline constexpr std::size_t kLogMsgCap   = 256; // truncates longer messages

struct Log {
    char level[kLogLevelCap] = {};
    char msg[kLogMsgCap] = {};
};

// State snapshot. Sent by the Switch on every (re)connect right after HELLO,
// and (transitively) on save load via SaveLoadHook -> requestRehello. Three
// kinds of message in sequence: one StateBegin, N StateChunk (per-stage shines
// + a trailing "_meta" chunk for cross-stage data), one StateEnd.
//
// Carries RAW SMO identifiers (stage_name, object_id, shine_uid, hack_name)
// matching M4's Check semantics; the bridge resolves via shine_map.json /
// capture_map.json. The bridge is the source of truth for what AP knows; the
// snapshot lets AP learn about anything collected while disconnected.
//
// M6 phase C — fixed-buffer storage. The encoder runs on the worker thread,
// and the M6.1 allocator hazard (libstdc++'s allocator NULL-derefs in
// nn::os::GetTlsValue on any std::string growth past SSO ~15 chars and any
// std::vector growth) makes every wide stage_name (e.g. "WaterfallWorldHomeStage"
// = 23 chars) plus every per-stage shines accumulation a latent crash. Prior
// to enumerateOwnedShines emitting real data, this stayed dormant because the
// stub emitted nothing; landing M6 phase C without converting these would
// trip the deref the first time a stage with > ~3 owned shines was traversed.
// All snapshot string fields are now char[kCheckFieldCap]; arrays use
// kSnapshotMax* caps with log-and-drop on overflow.

inline constexpr std::size_t kSnapshotMaxShinesPerStage = 64;
    // Worst observed real per-stage moon count is ~30 (Mushroom split across
    // many stages keeps per-stage low); 64 is 2× headroom. Overflow logs+drops
    // in SnapshotBuilder::addShine — next reconnect retries idempotently.
inline constexpr std::size_t kSnapshotMaxCaptures = 64;
    // 43 caps in capture_table.h today; 64 is comfortable headroom.

struct StateBegin {
    std::string mod_ver;
    int save_slot = -1;  // -1 means absent; bridge does NOT fence on this
};

struct ShineEntry {
    char object_id[kCheckFieldCap] = {};
    int shine_uid = -1;
};

struct StateChunk {
    // Per-stage chunk: stage_name = SMO stage key (e.g. "CapWorldHomeStage"),
    //   shines[0..shine_count] = list of {object_id, shine_uid}.
    // Cross-stage "_meta" chunk: stage_name = "_meta",
    //   captures[0..capture_count] = list of raw hack_names,
    //   include_goal_reached/goal_reached for the goal flag.
    char stage_name[kCheckFieldCap] = {};
    ShineEntry shines[kSnapshotMaxShinesPerStage] = {};
    int shine_count = 0;
    char captures[kSnapshotMaxCaptures][kCheckFieldCap] = {};
    int capture_count = 0;
    bool include_goal_reached = false;
    bool goal_reached = false;
};

struct StateEnd {};

// M6 phase D — Switch -> Bridge deposit notification.
//
// Each call to GameDataFunction::addPayShine (intercepted by AddPayShineHook)
// emits one Deposit with the per-toss `amount`, the kingdom Mario was in at
// the time, and a monotonic-per-session `seq`. The bridge applies the debit
// to its outstanding cache, writes to the AP data store, and replies with a
// DepositAck carrying the same seq so the Switch can drop the entry from
// its pending-deposit ring. Unacked entries are replayed on reconnect.
//
// Fixed-size kingdom buffer because this is encoded on the worker thread
// (allocator-NULL-deref discipline). 32 is generous — the longest internal
// kingdom string is "Darker Side" (11 chars) plus NUL.
struct Deposit {
    std::uint64_t seq = 0;
    char kingdom[kCheckFieldCap] = {};
    int amount = 0;
};

void encodeDeposit(smoap::util::json::LineBuffer&, const Deposit&);

// Bridge -> Switch ----------------------------------------------------------
//
// Fixed-size char buffers throughout. Originally these were std::string
// fields under the assumption "std::string is safe on the worker." That
// assumption broke 2026-05-16: parseCheckedReplay's first ItemRef.shine_id
// assignment (a 20-char "Our First Power Moon") NULL-deref'd inside
// libstdc++'s allocator. The encoder path was fixed by going through a
// LineBuffer; this is the matching inbound-side fix. Sizes are budgeted from
// observed traffic + 2x headroom.

struct HelloAck {
    bool ok = false;
    char seed[kCheckFieldCap] = {};
    char slot[kCheckFieldCap] = {};
    char cap_table_hash[kCheckFieldCap] = {};
    // Bridge-owned DeathLink toggle. Mod ships the inbound apply path
    // unconditionally; this flag gates whether we act on inbound kill messages
    // so the user enables DeathLink in bridge config without rebuilding.
    bool deathlink_enabled = false;
    char err[kLongFieldCap] = {};
};

struct ItemRef {
    ItemKind kind = ItemKind::Other;
    char kingdom[kCheckFieldCap] = {};
    char shine_id[kMediumFieldCap] = {};
    char cap[kCheckFieldCap] = {};
    char name[kMediumFieldCap] = {};
    // AP classification (progression/useful/trap/filler), empty if absent.
    // Carried on full ItemMsgs but NOT on checked_replay (bridge strips).
    // Fixed buffer per the M6.1 allocator-safety contract.
    char classification[kCheckFieldCap] = {};
};

struct CheckedReplay {
    // Fixed-size array — `std::vector::push_back` triggered the libstdc++
    // allocator NULL-deref on a re-HELLO 2026-05-16, same root cause as the
    // other inbound fields. 128 entries covers typical session replay (the
    // bridge only emits checks observed since the last connect). Overflow
    // truncates with a log line.
    static constexpr std::size_t kMaxIds = 128;
    ItemRef ids[kMaxIds]{};
    std::size_t id_count = 0;
    bool truncated = false;
};

struct Item {
    ItemKind kind = ItemKind::Other;
    char kingdom[kCheckFieldCap] = {};
    char shine_id[kMediumFieldCap] = {};
    char cap[kCheckFieldCap] = {};
    char name[kMediumFieldCap] = {};
    char from[kCheckFieldCap] = {};
    // M6 phase B: populated by the bridge for capture items via the reverse
    // CaptureMap (cap_name -> hack_name). Mod feeds straight to
    // GameDataFunction::addHackDictionary. Empty when the bridge had no map
    // entry — mod logs and drops in that case.
    char hack_name[kCheckFieldCap] = {};
    // M-color: AP item classification (wire form: "progression"/"useful"/
    // "trap"/"filler", empty when the bridge didn't send one — older bridge
    // against newer Switch). Used for log lines + future post-collection
    // effects; pre-collection moon color comes from ShineScouts, not here.
    // Fixed buffer per the same M6.1 allocator-safety contract every other
    // inbound field uses.
    char classification[kCheckFieldCap] = {};
};

struct Print {
    char text[kPrintFieldCap] = {};
};

struct ApStateMsg {
    // Renamed from ApState to avoid collision with class smoap::ap::ApState
    // (the in-process singleton). Carries the bridge's view of the AP-server
    // connection state.
    char conn[kCheckFieldCap] = {};  // "disconnected" | "connecting" | "ready"
};

struct Pong {
    std::int64_t ts_ms = 0;
};

struct Err {
    char code[kCheckFieldCap] = {};
    char ctx[kMediumFieldCap] = {};
};

struct Kill {
    // DeathLink forwarded from another slot. M4 logs this; killing Mario
    // belongs to M6 where we also have the player-state-write machinery.
    char source[kCheckFieldCap] = {};
    char cause[kLongFieldCap] = {};
};

struct MoonLabel {
    // M6 phase A.5 — Channel A. Bridge ships this in the same TCP push as
    // the handshake reply to a Check, so the text is in our hands before
    // the moon-get cutscene starts. ApState stows it into pending_moon_label;
    // the MoonLabelHook trampolines read it during the cutscene and call
    // al::setPaneStringFormat on the "TxtScenario" pane.
    //
    // `text` is pre-truncated by the bridge (≤30 bytes UTF-8). Switch
    // re-validates length on copy into ApState::pending_moon_label.
    // Fixed char[] (not std::string) per the M6.1 inbound-allocator-safety
    // contract — every DecodedMsg field uses fixed buffers because the
    // libstdc++ allocator NULL-derefs on the worker thread once heap state
    // drifts. kCheckFieldCap (64) is comfortable headroom over the bridge's
    // 30-byte truncation.
    //
    // `seq` echoes Check.seq so the hook knows whether the pending label
    // is for the moon it's about to display vs. a stale leftover.
    //
    // `valid_for_ms` is a Switch-relative TTL starting at receipt — avoids
    // PC/Switch clock skew. Expired labels are silently discarded.
    char text[kCheckFieldCap] = {};
    int seq = 0;
    int valid_for_ms = 4000;
};

// One entry of the AP-classification palette table. Sent by the bridge in
// chunked ShineScouts after AP `LocationInfo` lands (i.e. once at AP-connect
// time, plus a full replay on every Switch reconnect via HELLO). The Switch
// merges chunks into ApState::shine_palette by shine_uid overwrite, then
// the ShineAppearanceHook substitutes the palette index in rs::set
// StageShineAnimFrame.
struct ShineScout {
    int shine_uid = -1;
    int palette = 0;  // 0 means "no override; keep stage default frame"
};

struct ShineScouts {
    // Fixed-size array (same M6.1 allocator-safety contract as
    // CheckedReplay.ids). Bridge chunks at 200 entries per message;
    // kMaxEntries holds one full chunk with headroom for protocol drift.
    // Overflow truncates with a log line on the consumer side.
    static constexpr std::size_t kMaxEntries = 256;
    ShineScout entries[kMaxEntries]{};
    std::size_t entry_count = 0;
    bool truncated = false;
};

// M6 phase D — Bridge -> Switch deposit ack.
//
// One ack per inbound Deposit (idempotent: the bridge re-acks already-seen
// seqs unconditionally so reconnect-replay always self-heals). Switch keeps
// last_acked_deposit_seq as the high-water mark and drops everything at or
// below it from the pending-deposit ring.
struct DepositAck {
    std::uint64_t seq = 0;
};

// M6 phase D — Bridge -> Switch authoritative per-kingdom balance.
//
// Sent (1) immediately after HelloAckMsg on every Switch reconnect and (2)
// every time the bridge's outstanding_by_kingdom mutates (grant arrival or
// deposit applied). The Switch overwrites each `ap_moons_kingdom[bit]` so
// the AP data store remains the single source of truth across reboots.
//
// Up to 17 entries (one per kingdom). Unused entries pad with zero `count`
// + empty `kingdom` — the consumer skips empty kingdoms (treats as "no
// update for this slot"); for full-reset behavior, send all 17 explicitly
// even if some are 0.
struct OutstandingEntry {
    char kingdom[kCheckFieldCap] = {};
    int count = 0;
};

struct Outstanding {
    static constexpr std::size_t kMaxEntries = 17;
    OutstandingEntry entries[kMaxEntries]{};
    std::size_t entry_count = 0;
};

// (de)serialization --------------------------------------------------------
// Implementations in ApProtocol.cpp use util/Json.hpp (no STL exceptions).
//
// Encoders write into a caller-owned LineBuffer. The trailing '\n' is
// included. Use `line.data()` / `line.size()` to send on the socket.
// Caller-owned buffers keep the encode path off the libstdc++ allocator,
// which NULL-derefs in our subsdk9 link once heap state drifts (see project
// memory `libstdcpp_allocator_broken_in_subsdk9`).

void encodeHello(smoap::util::json::LineBuffer&, const Hello&);
void encodeCheck(smoap::util::json::LineBuffer&, const Check&);
void encodeStatus(smoap::util::json::LineBuffer&, const Status&);
void encodeGoal(smoap::util::json::LineBuffer&);
void encodeDeath(smoap::util::json::LineBuffer&, const Death&);
void encodePing(smoap::util::json::LineBuffer&, const Ping&);
void encodeLog(smoap::util::json::LineBuffer&, const Log&);
void encodeStateBegin(smoap::util::json::LineBuffer&, const StateBegin&);
void encodeStateChunk(smoap::util::json::LineBuffer&, const StateChunk&);
void encodeStateEnd(smoap::util::json::LineBuffer&);
// encodeDeposit is declared above (next to the Deposit struct) so the
// Switch->Bridge encoders all live with their associated structs.

// Returns true on parse success and fills the discriminated union outputs.
struct DecodedMsg {
    char t[kCheckFieldCap] = {};  // type discriminator: "hello_ack" etc.
    HelloAck hello_ack{};
    CheckedReplay checked_replay{};
    Item item{};
    Print print{};
    ApStateMsg ap_state{};
    Pong pong{};
    Err err{};
    Kill kill{};
    MoonLabel moon_label{};
    ShineScouts shine_scouts{};
    DepositAck deposit_ack{};
    Outstanding outstanding{};
};
bool decode(const char* data, std::size_t len, DecodedMsg& out);

}  // namespace smoap::ap
