// Wire format mirror for the Switch <-> Bridge channel.
// Authoritative spec lives in docs/wire-protocol.md and bridge/smo_ap_bridge/protocol.py.
//
// Single persistent TCP connection. Each message is one '\n'-terminated line
// of UTF-8 JSON. Field "t" is the message type. Max line: 8 KiB.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace smoap::ap {

inline constexpr std::size_t kMaxLineBytes = 8 * 1024;

enum class ItemKind : std::uint8_t {
    Moon = 0,
    Capture = 1,
    Kingdom = 2,
    Shop = 3,
    Other = 4,
};

const char* toWire(ItemKind k);          // "moon" / "capture" / ...
ItemKind fromWire(const std::string& s); // returns Other for unknown

// Switch -> Bridge ----------------------------------------------------------

struct Hello {
    std::string mod_ver;
    std::string smo_ver;
    std::string cap_table_hash;
};

struct Check {
    ItemKind kind = ItemKind::Moon;
    std::string kingdom;
    std::string shine_id;
    std::string cap;
    int slot = -1;  // -1 means absent
};

struct Status {
    std::string kingdom;
    int scenario = -1;
    int moons_collected = -1;
};

struct Goal {};

struct Ping {
    std::int64_t ts_ms = 0;
};

struct Log {
    std::string level = "info";
    std::string msg;
};

// Bridge -> Switch ----------------------------------------------------------

struct HelloAck {
    bool ok = false;
    std::string seed;
    std::string slot;
    std::string cap_table_hash;
    std::string err;
};

struct ItemRef {
    ItemKind kind = ItemKind::Other;
    std::string kingdom;
    std::string shine_id;
    std::string cap;
    std::string name;
    int slot = -1;
};

struct CheckedReplay {
    std::vector<ItemRef> ids;
};

struct Item {
    ItemKind kind = ItemKind::Other;
    std::string kingdom;
    std::string shine_id;
    std::string cap;
    std::string name;
    int slot = -1;
    std::string from;
};

struct Print {
    std::string text;
};

struct ApStateMsg {
    // Renamed from ApState to avoid collision with class smoap::ap::ApState
    // (the in-process singleton). Carries the bridge's view of the AP-server
    // connection state.
    std::string conn;  // "disconnected" | "connecting" | "ready"
};

struct Pong {
    std::int64_t ts_ms = 0;
};

struct Err {
    std::string code;
    std::string ctx;
};

// (de)serialization --------------------------------------------------------
// Implementations in ApProtocol.cpp use util/Json.hpp (no STL exceptions).

std::string encodeHello(const Hello&);
std::string encodeCheck(const Check&);
std::string encodeStatus(const Status&);
std::string encodeGoal();
std::string encodePing(const Ping&);
std::string encodeLog(const Log&);

// Returns true on parse success and fills the discriminated union outputs.
struct DecodedMsg {
    std::string t;
    HelloAck hello_ack{};
    CheckedReplay checked_replay{};
    Item item{};
    Print print{};
    ApStateMsg ap_state{};
    Pong pong{};
    Err err{};
};
bool decode(const char* data, std::size_t len, DecodedMsg& out);

}  // namespace smoap::ap
