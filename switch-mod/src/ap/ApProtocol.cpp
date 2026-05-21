// Wire format (de)serializers for the Switch <-> Bridge channel.
//
// All output ends in '\n' (one message per line). Encoders skip optional
// fields when their values are sentinel (empty string, slot < 0). Decoder
// requires the "t" field to be first (matches our encoders + the bridge's).

#include "ApProtocol.hpp"

#include <cstring>

namespace smoap::ap {

namespace {

using smoap::util::json::Encoder;
using smoap::util::json::LineBuffer;
using smoap::util::json::Reader;

// Inbound path: read a JSON string value into a fixed char buffer. Bounded,
// no heap touch. Used by all the DecodedMsg parse* functions below.
template <std::size_t N>
bool readIntoField(Reader& r, char (&dst)[N]) {
    std::string_view v;
    if (!r.nextString(v)) return false;
    copyFixedFieldN(dst, v.data(), v.size());
    return true;
}

bool readIntoInt(Reader& r, int& out) {
    std::int64_t v;
    if (!r.nextInt(v)) return false;
    out = static_cast<int>(v);
    return true;
}

}  // namespace

// ---------------------------------------------------------------------------
// ItemKind <-> wire string
// ---------------------------------------------------------------------------

const char* toWire(ItemKind k) {
    switch (k) {
        case ItemKind::Moon:    return "moon";
        case ItemKind::Capture: return "capture";
        case ItemKind::Other:   return "other";
    }
    return "other";
}

ItemKind fromWire(const char* s) {
    if (!s) return ItemKind::Other;
    if (std::strcmp(s, "moon")    == 0) return ItemKind::Moon;
    if (std::strcmp(s, "capture") == 0) return ItemKind::Capture;
    return ItemKind::Other;
}

ItemKind fromWire(const std::string& s) { return fromWire(s.c_str()); }

// ---------------------------------------------------------------------------
// Encoders (Switch -> Bridge)
// ---------------------------------------------------------------------------

void encodeHello(LineBuffer& line, const Hello& h) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("hello")
        .key("mod_ver").value(h.mod_ver)
        .key("smo_ver").value(h.smo_ver)
        .key("cap_table_hash").value(h.cap_table_hash)
     .endObject();
    line.append('\n');
}

void encodeCheck(LineBuffer& line, const Check& c) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("check")
        .key("kind").value(toWire(c.kind));
    if (c.kingdom[0])    e.key("kingdom").value(c.kingdom);
    if (c.shine_id[0])   e.key("shine_id").value(c.shine_id);
    if (c.cap[0])        e.key("cap").value(c.cap);
    if (c.stage_name[0]) e.key("stage_name").value(c.stage_name);
    if (c.object_id[0])  e.key("object_id").value(c.object_id);
    if (c.shine_uid >= 0) e.key("shine_uid").value(c.shine_uid);
    if (c.hack_name[0])  e.key("hack_name").value(c.hack_name);
    if (c.seq > 0)       e.key("seq").value(c.seq);
    e.endObject();
    line.append('\n');
}

void encodeStatus(LineBuffer& line, const Status& s) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("status");
    if (!s.kingdom.empty())     e.key("kingdom").value(s.kingdom);
    if (s.scenario >= 0)        e.key("scenario").value(s.scenario);
    if (s.moons_collected >= 0) e.key("moons_collected").value(s.moons_collected);
    if (!s.stage_name.empty())  e.key("stage_name").value(s.stage_name);
    e.endObject();
    line.append('\n');
}

void encodeGoal(LineBuffer& line) {
    line.clear();
    Encoder e{line};
    e.beginObject().key("t").value("goal").endObject();
    line.append('\n');
}

void encodeDeath(LineBuffer& line, const Death& d) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("death")
        .key("ts_ms").value(d.ts_ms)
     .endObject();
    line.append('\n');
}

void encodePing(LineBuffer& line, const Ping& p) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("ping")
        .key("ts_ms").value(p.ts_ms)
     .endObject();
    line.append('\n');
}

void encodeLog(LineBuffer& line, const Log& lg) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("log")
        .key("level").value(lg.level)
        .key("msg").value(lg.msg)
     .endObject();
    line.append('\n');
}

void encodeStateBegin(LineBuffer& line, const StateBegin& s) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("state_begin")
        .key("mod_ver").value(s.mod_ver);
    if (s.save_slot >= 0) e.key("save_slot").value(s.save_slot);
    e.endObject();
    line.append('\n');
}

void encodeStateChunk(LineBuffer& line, const StateChunk& s) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("state_chunk")
        .key("stage_name").value(s.stage_name);
    if (s.shine_count > 0) {
        e.key("shines").beginArray();
        for (int i = 0; i < s.shine_count; ++i) {
            const auto& sh = s.shines[i];
            e.beginObject();
            if (sh.object_id[0])   e.key("object_id").value(sh.object_id);
            if (sh.shine_uid >= 0) e.key("shine_uid").value(sh.shine_uid);
            e.endObject();
        }
        e.endArray();
    }
    if (s.capture_count > 0) {
        e.key("captures").beginArray();
        for (int i = 0; i < s.capture_count; ++i) e.value(s.captures[i]);
        e.endArray();
    }
    if (s.include_goal_reached) e.key("goal_reached").value(s.goal_reached);
    e.endObject();
    line.append('\n');
}

void encodeStateEnd(LineBuffer& line) {
    line.clear();
    Encoder e{line};
    e.beginObject().key("t").value("state_end").endObject();
    line.append('\n');
}

void encodePaySnapshot(LineBuffer& line, const PaySnapshot& s) {
    line.clear();
    Encoder e{line};
    e.beginObject()
        .key("t").value("pay_snapshot");
    if (s.save_slot >= 0) e.key("save_slot").value(s.save_slot);
    e.key("complete").value(s.complete);
    e.key("entries").beginArray();
    for (std::size_t i = 0; i < s.entry_count; ++i) {
        const auto& entry = s.entries[i];
        e.beginObject()
            .key("kingdom").value(entry.kingdom)
            .key("pay").value(entry.pay)
         .endObject();
    }
    e.endArray();
    e.endObject();
    line.append('\n');
}

// ---------------------------------------------------------------------------
// Decoder (Bridge -> Switch)
// ---------------------------------------------------------------------------

namespace {

// Local fixed-buffer helper for the one place we read a kind discriminator
// that we immediately convert to ItemKind and discard. 32 bytes is way
// more than any wire value ("capture" = 7 chars is the longest).
bool readKindField(Reader& r, ItemKind& out) {
    char tmp[32] = {};
    std::string_view v;
    if (!r.nextString(v)) return false;
    copyFixedFieldN(tmp, v.data(), v.size());
    out = fromWire(tmp);  // const char* overload — zero heap touches
    return true;
}

bool parseHelloAck(Reader& r, HelloAck& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "ok")                { if (!r.nextBool(out.ok)) return false; }
        else if (key == "seed")              { if (!readIntoField(r, out.seed)) return false; }
        else if (key == "slot")              { if (!readIntoField(r, out.slot)) return false; }
        else if (key == "cap_table_hash")    { if (!readIntoField(r, out.cap_table_hash)) return false; }
        else if (key == "deathlink_enabled") { if (!r.nextBool(out.deathlink_enabled)) return false; }
        else if (key == "client_ver")        { if (!readIntoField(r, out.client_ver)) return false; }
        else if (key == "err")               { if (!readIntoField(r, out.err)) return false; }
        else                                 { return false; }  // unknown field
    }
    return true;
}

bool parseItemRefBody(Reader& r, ItemRef& out) {
    // Reads object fields for an ItemRef. Caller has already consumed '{'.
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "kind")           { if (!readKindField(r, out.kind)) return false; }
        else if (key == "kingdom")        { if (!readIntoField(r, out.kingdom)) return false; }
        else if (key == "shine_id")       { if (!readIntoField(r, out.shine_id)) return false; }
        else if (key == "cap")            { if (!readIntoField(r, out.cap)) return false; }
        else if (key == "name")           { if (!readIntoField(r, out.name)) return false; }
        else if (key == "classification") { if (!readIntoField(r, out.classification)) return false; }
        else                              { return false; }
    }
    return true;
}

bool parseCheckedReplay(Reader& r, CheckedReplay& out) {
    out.id_count = 0;
    out.truncated = false;
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "ids") {
            if (!r.enterArray()) return false;
            while (r.hasMoreInArray()) {
                if (!r.enterObject()) return false;
                if (out.id_count < CheckedReplay::kMaxIds) {
                    if (!parseItemRefBody(r, out.ids[out.id_count])) return false;
                    ++out.id_count;
                } else {
                    // Buffer full — parse and discard so the JSON stays
                    // well-formed; flag for caller logging.
                    ItemRef discard;
                    if (!parseItemRefBody(r, discard)) return false;
                    out.truncated = true;
                }
                if (!r.exitObject()) return false;
            }
            if (!r.exitArray()) return false;
        } else {
            return false;
        }
    }
    return true;
}

bool parseItem(Reader& r, Item& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "kind")           { if (!readKindField(r, out.kind)) return false; }
        else if (key == "kingdom")        { if (!readIntoField(r, out.kingdom)) return false; }
        else if (key == "shine_id")       { if (!readIntoField(r, out.shine_id)) return false; }
        else if (key == "cap")            { if (!readIntoField(r, out.cap)) return false; }
        else if (key == "name")           { if (!readIntoField(r, out.name)) return false; }
        else if (key == "from")           { if (!readIntoField(r, out.from)) return false; }
        // M6 phase B: bridge populates hack_name for capture items (cap → hack
        // reverse lookup via CaptureMap). Mod-side passes hack_name straight
        // to GameDataFunction::addHackDictionary.
        else if (key == "hack_name")      { if (!readIntoField(r, out.hack_name)) return false; }
        // M-color: AP classification, see ApProtocol.hpp Item.classification.
        else if (key == "classification") { if (!readIntoField(r, out.classification)) return false; }
        else                              { return false; }
    }
    return true;
}

bool parseShineScouts(Reader& r, ShineScouts& out) {
    out.entry_count = 0;
    out.truncated = false;
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "entries") {
            if (!r.enterArray()) return false;
            while (r.hasMoreInArray()) {
                if (!r.enterObject()) return false;
                ShineScout sc;
                std::string_view k2;
                while (r.nextField(k2)) {
                    if      (k2 == "shine_uid") { if (!readIntoInt(r, sc.shine_uid)) return false; }
                    else if (k2 == "palette")   { if (!readIntoInt(r, sc.palette)) return false; }
                    else                        { return false; }
                }
                if (!r.exitObject()) return false;
                if (out.entry_count < ShineScouts::kMaxEntries) {
                    out.entries[out.entry_count++] = sc;
                } else {
                    // Parsed-and-discarded so the JSON stays well-formed;
                    // the consumer logs truncation.
                    out.truncated = true;
                }
            }
            if (!r.exitArray()) return false;
        } else {
            return false;
        }
    }
    return true;
}

bool parsePrint(Reader& r, Print& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "text") { if (!readIntoField(r, out.text)) return false; }
        else               { return false; }
    }
    return true;
}

bool parseApStateMsg(Reader& r, ApStateMsg& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "conn") { if (!readIntoField(r, out.conn)) return false; }
        else               { return false; }
    }
    return true;
}

bool parsePong(Reader& r, Pong& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "ts_ms") { if (!r.nextInt(out.ts_ms)) return false; }
        else                { return false; }
    }
    return true;
}

bool parseErr(Reader& r, Err& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "code") { if (!readIntoField(r, out.code)) return false; }
        else if (key == "ctx")  { if (!readIntoField(r, out.ctx)) return false; }
        else                    { return false; }
    }
    return true;
}

bool parseKill(Reader& r, Kill& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "source") { if (!readIntoField(r, out.source)) return false; }
        else if (key == "cause")  { if (!readIntoField(r, out.cause)) return false; }
        else                      { return false; }
    }
    return true;
}

// Compare a char buffer's null-terminated contents to a string literal.
// Replaces former `out.t == "hello_ack"` style comparisons (out.t is now
// char[]). Same shape as strcmp but with a literal RHS for ergonomics.
inline bool eqStr(const char* a, const char* b) {
    while (*a && *b && *a == *b) { ++a; ++b; }
    return *a == '\0' && *b == '\0';
}

bool parseOutstanding(Reader& r, Outstanding& out) {
    out.entry_count = 0;
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "entries") {
            if (!r.enterArray()) return false;
            while (r.hasMoreInArray()) {
                if (!r.enterObject()) return false;
                OutstandingEntry entry;
                std::string_view k2;
                while (r.nextField(k2)) {
                    if      (k2 == "kingdom") { if (!readIntoField(r, entry.kingdom)) return false; }
                    else if (k2 == "count")   { if (!readIntoInt(r, entry.count)) return false; }
                    else                      { return false; }
                }
                if (!r.exitObject()) return false;
                if (out.entry_count < Outstanding::kMaxEntries) {
                    out.entries[out.entry_count++] = entry;
                }
                // Overflow: silently drop — the bridge only sends one entry
                // per kingdom, and the consumer treats kMaxEntries (17) as the
                // hard cap. A second entry for the same kingdom would be a
                // bridge bug, not a wire-protocol concern.
            }
            if (!r.exitArray()) return false;
        } else if (key == "lake_received_total" || key == "snow_received_total") {
            // Legacy M7 Path A fields — bridge no longer ships these but
            // older bridges still in flight might. Consume the int and drop
            // it; the gate is now stateless (KingdomOrderGate.hpp).
            int discard = 0;
            if (!readIntoInt(r, discard)) return false;
        } else {
            return false;
        }
    }
    return true;
}

bool parseCappy(Reader& r, Cappy& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "text") { if (!readIntoField(r, out.text)) return false; }
        else               { return false; }
    }
    return true;
}

bool parseMoonLabel(Reader& r, MoonLabel& out) {
    std::int64_t tmp = 0;
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "text")         { if (!readIntoField(r, out.text)) return false; }
        else if (key == "seq")          { if (!r.nextInt(tmp)) return false;
                                          out.seq = static_cast<int>(tmp); }
        else if (key == "valid_for_ms") { if (!r.nextInt(tmp)) return false;
                                          out.valid_for_ms = static_cast<int>(tmp); }
        else                            { return false; }
    }
    return true;
}

}  // namespace

bool decode(const char* data, std::size_t len, DecodedMsg& out) {
    if (len == 0) return false;
    Reader r(data, len);
    if (!r.enterObject()) return false;

    // First field MUST be "t". Both encoders (this file + bridge's protocol.py)
    // emit it first; we make it a hard requirement so the parser can dispatch
    // immediately.
    std::string_view key;
    if (!r.nextField(key) || key != "t") return false;
    if (!readIntoField(r, out.t)) return false;

    bool ok = true;
    if      (eqStr(out.t, "hello_ack"))      ok = parseHelloAck(r, out.hello_ack);
    else if (eqStr(out.t, "checked_replay")) ok = parseCheckedReplay(r, out.checked_replay);
    else if (eqStr(out.t, "item"))           ok = parseItem(r, out.item);
    else if (eqStr(out.t, "print"))          ok = parsePrint(r, out.print);
    else if (eqStr(out.t, "ap_state"))       ok = parseApStateMsg(r, out.ap_state);
    else if (eqStr(out.t, "pong"))           ok = parsePong(r, out.pong);
    else if (eqStr(out.t, "err"))            ok = parseErr(r, out.err);
    else if (eqStr(out.t, "kill"))           ok = parseKill(r, out.kill);
    else if (eqStr(out.t, "moon_label"))     ok = parseMoonLabel(r, out.moon_label);
    else if (eqStr(out.t, "cappy"))          ok = parseCappy(r, out.cappy);
    else if (eqStr(out.t, "shine_scouts"))   ok = parseShineScouts(r, out.shine_scouts);
    else if (eqStr(out.t, "outstanding"))    ok = parseOutstanding(r, out.outstanding);
    else {
        // Unknown type: leave out.t set so handleLine can warn. Don't bother
        // draining the rest of the object — caller treats unknown as ignored.
        return true;
    }
    if (!ok) return false;
    return r.exitObject();
}

}  // namespace smoap::ap
