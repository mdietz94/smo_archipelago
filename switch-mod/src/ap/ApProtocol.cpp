// Wire format (de)serializers for the Switch <-> Bridge channel.
//
// All output ends in '\n' (one message per line). Encoders skip optional
// fields when their values are sentinel (empty string, slot < 0). Decoder
// requires the "t" field to be first (matches our encoders + the bridge's).

#include "ApProtocol.hpp"

#include <cstring>

#include "../util/Json.hpp"

namespace smoap::ap {

namespace {

using smoap::util::json::Encoder;
using smoap::util::json::Reader;

std::string finishLine(Encoder& e) {
    auto s = std::move(e).take();
    s.push_back('\n');
    return s;
}

bool readIntoString(Reader& r, std::string& out) {
    std::string_view v;
    if (!r.nextString(v)) return false;
    out.assign(v);
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
        case ItemKind::Kingdom: return "kingdom";
        case ItemKind::Shop:    return "shop";
        case ItemKind::Other:   return "other";
    }
    return "other";
}

ItemKind fromWire(const std::string& s) {
    if (s == "moon")    return ItemKind::Moon;
    if (s == "capture") return ItemKind::Capture;
    if (s == "kingdom") return ItemKind::Kingdom;
    if (s == "shop")    return ItemKind::Shop;
    return ItemKind::Other;
}

// ---------------------------------------------------------------------------
// Encoders (Switch -> Bridge)
// ---------------------------------------------------------------------------

std::string encodeHello(const Hello& h) {
    Encoder e;
    e.beginObject()
        .key("t").value("hello")
        .key("mod_ver").value(h.mod_ver)
        .key("smo_ver").value(h.smo_ver)
        .key("cap_table_hash").value(h.cap_table_hash)
     .endObject();
    return finishLine(e);
}

std::string encodeCheck(const Check& c) {
    Encoder e;
    e.beginObject()
        .key("t").value("check")
        .key("kind").value(toWire(c.kind));
    if (!c.kingdom.empty())  e.key("kingdom").value(c.kingdom);
    if (!c.shine_id.empty()) e.key("shine_id").value(c.shine_id);
    if (!c.cap.empty())      e.key("cap").value(c.cap);
    if (c.slot >= 0)         e.key("slot").value(c.slot);
    e.endObject();
    return finishLine(e);
}

std::string encodeStatus(const Status& s) {
    Encoder e;
    e.beginObject()
        .key("t").value("status");
    if (!s.kingdom.empty())     e.key("kingdom").value(s.kingdom);
    if (s.scenario >= 0)        e.key("scenario").value(s.scenario);
    if (s.moons_collected >= 0) e.key("moons_collected").value(s.moons_collected);
    e.endObject();
    return finishLine(e);
}

std::string encodeGoal() {
    Encoder e;
    e.beginObject().key("t").value("goal").endObject();
    return finishLine(e);
}

std::string encodePing(const Ping& p) {
    Encoder e;
    e.beginObject()
        .key("t").value("ping")
        .key("ts_ms").value(p.ts_ms)
     .endObject();
    return finishLine(e);
}

std::string encodeLog(const Log& lg) {
    Encoder e;
    e.beginObject()
        .key("t").value("log")
        .key("level").value(lg.level)
        .key("msg").value(lg.msg)
     .endObject();
    return finishLine(e);
}

// ---------------------------------------------------------------------------
// Decoder (Bridge -> Switch)
// ---------------------------------------------------------------------------

namespace {

bool parseHelloAck(Reader& r, HelloAck& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "ok")             { if (!r.nextBool(out.ok)) return false; }
        else if (key == "seed")           { if (!readIntoString(r, out.seed)) return false; }
        else if (key == "slot")           { if (!readIntoString(r, out.slot)) return false; }
        else if (key == "cap_table_hash") { if (!readIntoString(r, out.cap_table_hash)) return false; }
        else if (key == "err")            { if (!readIntoString(r, out.err)) return false; }
        else                              { return false; }  // unknown field
    }
    return true;
}

bool parseItemRefBody(Reader& r, ItemRef& out, bool& want_comma_handled) {
    // Reads object fields for an ItemRef. Caller has already consumed '{'.
    want_comma_handled = false;  // Reader handles array commas via prepareValue
    std::string_view key;
    while (r.nextField(key)) {
        if      (key == "kind")     { std::string s; if (!readIntoString(r, s)) return false; out.kind = fromWire(s); }
        else if (key == "kingdom")  { if (!readIntoString(r, out.kingdom)) return false; }
        else if (key == "shine_id") { if (!readIntoString(r, out.shine_id)) return false; }
        else if (key == "cap")      { if (!readIntoString(r, out.cap)) return false; }
        else if (key == "name")     { if (!readIntoString(r, out.name)) return false; }
        else if (key == "slot")     { if (!readIntoInt(r, out.slot)) return false; }
        else                        { return false; }
    }
    return true;
}

bool parseCheckedReplay(Reader& r, CheckedReplay& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "ids") {
            if (!r.enterArray()) return false;
            while (r.hasMoreInArray()) {
                if (!r.enterObject()) return false;
                ItemRef ref;
                bool dummy;
                if (!parseItemRefBody(r, ref, dummy)) return false;
                if (!r.exitObject()) return false;
                out.ids.push_back(std::move(ref));
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
        if      (key == "kind")     { std::string s; if (!readIntoString(r, s)) return false; out.kind = fromWire(s); }
        else if (key == "kingdom")  { if (!readIntoString(r, out.kingdom)) return false; }
        else if (key == "shine_id") { if (!readIntoString(r, out.shine_id)) return false; }
        else if (key == "cap")      { if (!readIntoString(r, out.cap)) return false; }
        else if (key == "name")     { if (!readIntoString(r, out.name)) return false; }
        else if (key == "slot")     { if (!readIntoInt(r, out.slot)) return false; }
        else if (key == "from")     { if (!readIntoString(r, out.from)) return false; }
        else                        { return false; }
    }
    return true;
}

bool parsePrint(Reader& r, Print& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "text") { if (!readIntoString(r, out.text)) return false; }
        else               { return false; }
    }
    return true;
}

bool parseApStateMsg(Reader& r, ApStateMsg& out) {
    std::string_view key;
    while (r.nextField(key)) {
        if (key == "conn") { if (!readIntoString(r, out.conn)) return false; }
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
        if      (key == "code") { if (!readIntoString(r, out.code)) return false; }
        else if (key == "ctx")  { if (!readIntoString(r, out.ctx)) return false; }
        else                    { return false; }
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
    if (!readIntoString(r, out.t)) return false;

    bool ok = true;
    if      (out.t == "hello_ack")      ok = parseHelloAck(r, out.hello_ack);
    else if (out.t == "checked_replay") ok = parseCheckedReplay(r, out.checked_replay);
    else if (out.t == "item")           ok = parseItem(r, out.item);
    else if (out.t == "print")          ok = parsePrint(r, out.print);
    else if (out.t == "ap_state")       ok = parseApStateMsg(r, out.ap_state);
    else if (out.t == "pong")           ok = parsePong(r, out.pong);
    else if (out.t == "err")            ok = parseErr(r, out.err);
    else {
        // Unknown type: leave out.t set so handleLine can warn. Don't bother
        // draining the rest of the object — caller treats unknown as ignored.
        return true;
    }
    if (!ok) return false;
    return r.exitObject();
}

}  // namespace smoap::ap
