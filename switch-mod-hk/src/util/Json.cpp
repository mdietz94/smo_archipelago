#include "Json.hpp"

#include <cstdio>
#include <cstdlib>

namespace smoap::util::json {

Encoder& Encoder::beginObject() { maybeComma(); out_.append('{'); pushFrame(); return *this; }
Encoder& Encoder::endObject() {
    out_.append('}');
    popFrame();
    // We just emitted a complete value; the outer frame needs a comma before
    // its next entry. Without this, e.g. an array of objects emits "[{...}{...}".
    markNeedsComma();
    return *this;
}
Encoder& Encoder::beginArray()  { maybeComma(); out_.append('['); pushFrame(); return *this; }
Encoder& Encoder::endArray() {
    out_.append(']');
    popFrame();
    markNeedsComma();
    return *this;
}

Encoder& Encoder::key(std::string_view k) {
    maybeComma();
    out_.append('"'); out_.append(k.data(), k.size()); out_.append('"'); out_.append(':');
    clearNeedsComma();
    return *this;
}

Encoder& Encoder::value(std::string_view s) {
    maybeComma();
    out_.append('"');
    for (char c : s) {
        switch (c) {
            case '"':  out_.append('\\'); out_.append('"');  break;
            case '\\': out_.append('\\'); out_.append('\\'); break;
            case '\n': out_.append('\\'); out_.append('n');  break;
            case '\r': out_.append('\\'); out_.append('r');  break;
            case '\t': out_.append('\\'); out_.append('t');  break;
            default:   out_.append(c);                       break;
        }
    }
    out_.append('"');
    markNeedsComma();
    return *this;
}

Encoder& Encoder::value(std::int64_t v) {
    maybeComma();
    // 20 bytes covers the longest int64 ("-9223372036854775808" = 20 chars)
    // plus a NUL. snprintf into a stack buffer — no heap touch unlike
    // std::to_string, which allocates a std::string and tripped the
    // libstdc++ NULL-allocator path in subsdk9.
    char tmp[24];
    int n = std::snprintf(tmp, sizeof(tmp), "%lld", static_cast<long long>(v));
    if (n > 0) out_.append(tmp, static_cast<std::size_t>(n));
    markNeedsComma();
    return *this;
}
Encoder& Encoder::value(int v) { return value(static_cast<std::int64_t>(v)); }

Encoder& Encoder::value(bool v) {
    maybeComma();
    if (v) out_.append("true", 4);
    else   out_.append("false", 5);
    markNeedsComma();
    return *this;
}

void Encoder::maybeComma() {
    if (depth_ == 0) return;
    if (needs_comma_stack_[depth_ - 1]) out_.append(',');
}

void Encoder::pushFrame() {
    if (depth_ < kMaxDepth) {
        needs_comma_stack_[depth_] = false;
        ++depth_;
    }
}

void Encoder::popFrame() {
    if (depth_ > 0) --depth_;
}

void Encoder::markNeedsComma() {
    if (depth_ > 0) needs_comma_stack_[depth_ - 1] = true;
}

void Encoder::clearNeedsComma() {
    if (depth_ > 0) needs_comma_stack_[depth_ - 1] = false;
}

Reader::Reader(const char* data, std::size_t len) : p_(data), end_(data + len) {}

void Reader::skipWs() {
    while (p_ < end_ && (*p_ == ' ' || *p_ == '\t' || *p_ == '\n' || *p_ == '\r')) ++p_;
}

bool Reader::fail() {
    error_ = true;
    return false;
}

// Position cursor at the start of a value. In an array context, consume the
// separating comma before the value (except for the first element).
bool Reader::prepareValue() {
    if (error_) return false;
    if (depth_ > 0 && !stack_[depth_ - 1].is_object && stack_[depth_ - 1].needs_comma) {
        skipWs();
        if (p_ >= end_ || *p_ != ',') return fail();
        ++p_;
    }
    skipWs();
    return p_ < end_;
}

// After successful read of a value in an array, the next read needs a comma.
// (In an object, nextField is what sets needs_comma — value reads are a no-op.)
void Reader::markValueDone() {
    if (depth_ > 0 && !stack_[depth_ - 1].is_object) {
        stack_[depth_ - 1].needs_comma = true;
    }
}

namespace {
inline bool isHexDigit(char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}
inline int hexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return c - 'A' + 10;
}
}  // namespace

// Read a "string" literal starting at p_ (must point at opening quote).
// Decodes escape sequences in place: the input buffer is overwritten with the
// decoded bytes (always <= the encoded length, so it fits). The returned view
// points into the (now-mutated) input buffer.
bool Reader::readString(std::string_view& out) {
    if (p_ >= end_ || *p_ != '"') return false;
    ++p_;
    char* write = const_cast<char*>(p_);
    const char* start = write;
    while (p_ < end_ && *p_ != '"') {
        char c = *p_;
        if (c == '\\') {
            ++p_;
            if (p_ >= end_) return false;
            char esc = *p_++;
            switch (esc) {
                case '"':  *write++ = '"';  break;
                case '\\': *write++ = '\\'; break;
                case '/':  *write++ = '/';  break;
                case 'n':  *write++ = '\n'; break;
                case 'r':  *write++ = '\r'; break;
                case 't':  *write++ = '\t'; break;
                case 'b':  *write++ = '\b'; break;
                case 'f':  *write++ = '\f'; break;
                case 'u': {
                    if (end_ - p_ < 4) return false;
                    unsigned cp = 0;
                    for (int i = 0; i < 4; ++i) {
                        char h = *p_++;
                        if (!isHexDigit(h)) return false;
                        cp = (cp << 4) | static_cast<unsigned>(hexVal(h));
                    }
                    // Minimal: encode BMP code point as UTF-8, ignore surrogate pairs.
                    if (cp < 0x80u) {
                        *write++ = static_cast<char>(cp);
                    } else if (cp < 0x800u) {
                        *write++ = static_cast<char>(0xC0u | (cp >> 6));
                        *write++ = static_cast<char>(0x80u | (cp & 0x3Fu));
                    } else {
                        *write++ = static_cast<char>(0xE0u | (cp >> 12));
                        *write++ = static_cast<char>(0x80u | ((cp >> 6) & 0x3Fu));
                        *write++ = static_cast<char>(0x80u | (cp & 0x3Fu));
                    }
                    break;
                }
                default: return false;
            }
        } else {
            *write++ = c;
            ++p_;
        }
    }
    if (p_ >= end_ || *p_ != '"') return false;
    ++p_;  // consume closing quote
    out = std::string_view(start, static_cast<std::size_t>(write - start));
    return true;
}

bool Reader::enterObject() {
    if (!prepareValue()) return fail();
    if (*p_ != '{') return fail();
    ++p_;
    if (depth_ >= kMaxDepth) return fail();
    stack_[depth_++] = Frame{true, false};
    return true;
}

bool Reader::exitObject() {
    if (error_) return false;
    if (depth_ == 0 || !stack_[depth_ - 1].is_object) return fail();
    skipWs();
    if (p_ >= end_ || *p_ != '}') return fail();
    ++p_;
    --depth_;
    markValueDone();
    return true;
}

bool Reader::nextField(std::string_view& out_key) {
    if (error_) return false;
    if (depth_ == 0 || !stack_[depth_ - 1].is_object) return fail();
    skipWs();
    if (p_ >= end_) return fail();
    if (*p_ == '}') return false;  // No more fields; leave } for exitObject.
    Frame& f = stack_[depth_ - 1];
    if (f.needs_comma) {
        if (*p_ != ',') return fail();
        ++p_;
        skipWs();
    }
    if (p_ >= end_ || *p_ != '"') return fail();
    if (!readString(out_key)) return fail();
    skipWs();
    if (p_ >= end_ || *p_ != ':') return fail();
    ++p_;
    skipWs();
    f.needs_comma = true;
    return true;
}

bool Reader::enterArray() {
    if (!prepareValue()) return fail();
    if (*p_ != '[') return fail();
    ++p_;
    if (depth_ >= kMaxDepth) return fail();
    stack_[depth_++] = Frame{false, false};
    return true;
}

bool Reader::exitArray() {
    if (error_) return false;
    if (depth_ == 0 || stack_[depth_ - 1].is_object) return fail();
    skipWs();
    if (p_ >= end_ || *p_ != ']') return fail();
    ++p_;
    --depth_;
    markValueDone();
    return true;
}

bool Reader::hasMoreInArray() const {
    if (error_) return false;
    if (depth_ == 0 || stack_[depth_ - 1].is_object) return false;
    const char* q = p_;
    while (q < end_ && (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r')) ++q;
    if (q >= end_) return false;
    return *q != ']';
}

bool Reader::nextString(std::string_view& out) {
    if (!prepareValue()) return fail();
    if (!readString(out)) return fail();
    markValueDone();
    return true;
}

bool Reader::nextInt(std::int64_t& out) {
    if (!prepareValue()) return fail();
    bool neg = false;
    if (*p_ == '-') {
        neg = true;
        ++p_;
        if (p_ >= end_) return fail();
    }
    if (*p_ < '0' || *p_ > '9') return fail();
    std::int64_t v = 0;
    while (p_ < end_ && *p_ >= '0' && *p_ <= '9') {
        v = v * 10 + (*p_ - '0');
        ++p_;
    }
    // Reject floats/exponents — caller said no need to handle them.
    if (p_ < end_ && (*p_ == '.' || *p_ == 'e' || *p_ == 'E')) return fail();
    out = neg ? -v : v;
    markValueDone();
    return true;
}

bool Reader::nextBool(bool& out) {
    if (!prepareValue()) return fail();
    if (end_ - p_ >= 4 && p_[0] == 't' && p_[1] == 'r' && p_[2] == 'u' && p_[3] == 'e') {
        p_ += 4;
        out = true;
        markValueDone();
        return true;
    }
    if (end_ - p_ >= 5 && p_[0] == 'f' && p_[1] == 'a' && p_[2] == 'l' && p_[3] == 's' && p_[4] == 'e') {
        p_ += 5;
        out = false;
        markValueDone();
        return true;
    }
    return fail();
}

// Tentative: if the next value is null, consume it and return true. Otherwise
// rewind to the original position and return false without marking error.
// Callers can chain: `if (r.isNull()) ... else r.nextString(s)`.
bool Reader::isNull() {
    if (error_) return false;
    const char* save = p_;
    if (depth_ > 0 && !stack_[depth_ - 1].is_object && stack_[depth_ - 1].needs_comma) {
        skipWs();
        if (p_ >= end_ || *p_ != ',') { p_ = save; return false; }
        ++p_;
    }
    skipWs();
    if (end_ - p_ >= 4 && p_[0] == 'n' && p_[1] == 'u' && p_[2] == 'l' && p_[3] == 'l') {
        p_ += 4;
        markValueDone();
        return true;
    }
    p_ = save;
    return false;
}

}  // namespace smoap::util::json
