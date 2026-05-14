// Tiny SAX-ish JSON parser/encoder.
//
// We avoid pulling in nlohmann/json or rapidjson — those have STL-exception
// dependencies and pull large code into the module. We need only flat
// objects with string/int/bool/null/array fields, so a hand-rolled scanner
// is ~600 lines and zero allocations beyond the string outputs.

#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace smoap::util::json {

class Encoder {
public:
    Encoder& beginObject();
    Encoder& endObject();
    Encoder& beginArray();
    Encoder& endArray();
    Encoder& key(std::string_view k);
    Encoder& value(std::string_view s);
    Encoder& value(const char* s) { return value(std::string_view(s)); }
    Encoder& value(std::int64_t v);
    Encoder& value(int v);
    Encoder& value(bool v);

    std::string take() && { return std::move(out_); }

private:
    void maybeComma();
    std::string out_;
    std::vector<bool> needs_comma_stack_;
};

// Minimal scan API. Returns false on malformed input.
//
// String escape sequences are decoded in place (the buffer pointed to by
// `data` is mutated). Pass a writable buffer — a non-const char array from
// the TCP receive path, not a string literal.
class Reader {
public:
    Reader(const char* data, std::size_t len);

    bool nextString(std::string_view& out);
    bool nextInt(std::int64_t& out);
    bool nextBool(bool& out);
    bool isNull();

    // Iterate object fields. After enterObject(), call nextField() repeatedly
    // until it returns false. Each successful call sets out_key and positions
    // the cursor at the value (read with one of the above).
    bool enterObject();
    bool exitObject();
    bool nextField(std::string_view& out_key);

    bool enterArray();
    bool exitArray();
    bool hasMoreInArray() const;

private:
    void skipWs();
    bool fail();
    bool prepareValue();
    void markValueDone();
    bool readString(std::string_view& out);

    struct Frame { bool is_object; bool needs_comma; };
    static constexpr int kMaxDepth = 8;

    const char* p_;
    const char* end_;
    Frame stack_[kMaxDepth]{};
    int depth_ = 0;
    bool error_ = false;
};

}  // namespace smoap::util::json
