// Tiny SAX-ish JSON parser/encoder.
//
// We avoid pulling in nlohmann/json or rapidjson — those have STL-exception
// dependencies and pull large code into the module. We need only flat
// objects with string/int/bool/null/array fields, so a hand-rolled scanner
// is ~600 lines and zero allocations beyond the string outputs.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace smoap::util::json {

// Fixed-size output buffer for one wire line. Caller-owned so encoder code
// never touches the heap. 8 KiB matches `smoap::ap::kMaxLineBytes` (the
// wire-format per-line cap from docs/wire-protocol.md). On overflow,
// appends are silently dropped — `truncated()` lets callers detect it.
//
// Why fixed-size + caller-owned: libstdc++'s allocator NULL-derefs in our
// subsdk9 link (see project memory `libstdcpp_allocator_broken_in_subsdk9`).
// A previous Encoder using `std::string out_` crashed inside
// `Encoder::key()` → `out_.append()` → `__memcpy_device` once the worker's
// allocator state had drifted (~1 min post-boot, on a re-HELLO triggered
// by SaveLoadHook). Going via a stack `LineBuffer` removes the only
// remaining heap path in the encode pipeline.
class LineBuffer {
public:
    static constexpr std::size_t kCap = 8 * 1024;

    void clear() { len_ = 0; trunc_ = false; }

    const char* data() const { return buf_; }
    std::size_t size() const { return len_; }
    bool empty() const { return len_ == 0; }
    bool truncated() const { return trunc_; }

    void append(char c) {
        if (len_ < kCap) { buf_[len_++] = c; } else { trunc_ = true; }
    }
    void append(const char* s, std::size_t n) {
        std::size_t take = (len_ + n <= kCap) ? n : (kCap - len_);
        for (std::size_t i = 0; i < take; ++i) buf_[len_ + i] = s[i];
        len_ += take;
        if (take < n) trunc_ = true;
    }
    void append(std::string_view sv) { append(sv.data(), sv.size()); }

private:
    char buf_[kCap];
    std::size_t len_ = 0;
    bool trunc_ = false;
};

class Encoder {
public:
    // Wire messages nest at most ~3 deep (object → "ids" → array → object).
    // 16 leaves headroom without touching the libstdc++ allocator. Overflows
    // are silent: a push past the limit just stops tracking commas for that
    // depth, which is preferable to crashing on what would already be
    // malformed JSON.
    static constexpr int kMaxDepth = 16;

    explicit Encoder(LineBuffer& out) : out_(out) {}

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

private:
    void maybeComma();
    void pushFrame();
    void popFrame();
    void markNeedsComma();
    void clearNeedsComma();

    LineBuffer& out_;
    bool needs_comma_stack_[kMaxDepth]{};
    int depth_ = 0;
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
