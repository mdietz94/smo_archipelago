// Logging — kernel debug log + bridge log forwarder.
//
// Every SMOAP_LOG_* call goes through log(), which:
//   1. hk::svc::OutputDebugString — kernel debug log (Ryujinx surface; on real
//      Switch with Atmosphere it goes to lm, where binlog visibility is
//      spotty and we mostly don't rely on it).
//   2. Forwards messages at or above SMOAP_LOG_FORWARD_MIN_LEVEL to the
//      PC client via the bridge's outbound_logs ring.
//
// Note (Hakkun migration 2026-05-20): the exlaunch-era SMOAP_DEBUG_SD_LOG
// boot-time SD-card capture path has been removed during the phase 3a port —
// it depended on `nn::fs::*` which Hakkun does not yet wrap. `markFsReady`
// and `drainPendingToFile` are retained as no-op stubs so unchanged callers
// (DrawMainHook is the only one) keep linking.

#include "Log.hpp"

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include <hk/svc/api.h>

#include "../ap/ApFrameBridge.hpp"

// Compile-time threshold: only INFO+ get mirrored to the PC client by default.
// DEBUG forwarding would flood the wire (some per-frame hook paths log at
// DEBUG). Override at build time with -DSMOAP_LOG_FORWARD_MIN_LEVEL=0 for DEBUG.
// 0=Debug, 1=Info, 2=Warn, 3=Error.
#ifndef SMOAP_LOG_FORWARD_MIN_LEVEL
#  define SMOAP_LOG_FORWARD_MIN_LEVEL 1
#endif

// Compile-time threshold for the kernel debug-log sink (hk::svc::OutputDebugString
// → Ryujinx, lm on real Switch). Same scale as above; default INFO keeps the
// per-frame DEBUG diagnostics out of normal Ryujinx logs. Rebuild with
// -DSMOAP_LOG_SINK_MIN_LEVEL=0 to surface DEBUG when investigating an issue.
#ifndef SMOAP_LOG_SINK_MIN_LEVEL
#  define SMOAP_LOG_SINK_MIN_LEVEL 1
#endif

namespace smoap::util {

namespace {

const char* prefix(LogLevel lvl) {
    switch (lvl) {
        case LogLevel::Debug: return "[smoap dbg] ";
        case LogLevel::Info:  return "[smoap inf] ";
        case LogLevel::Warn:  return "[smoap wrn] ";
        case LogLevel::Error: return "[smoap err] ";
    }
    return "[smoap ?] ";
}

const char* wireLevelName(LogLevel lvl) {
    switch (lvl) {
        case LogLevel::Debug: return "debug";
        case LogLevel::Info:  return "info";
        case LogLevel::Warn:  return "warn";
        case LogLevel::Error: return "error";
    }
    return "info";
}

}  // namespace

void markFsReady() {
    // No-op stub kept for source compat with older call sites.
}

void log(LogLevel lvl, const char* fmt, ...) {
    char buf[512];
    const char* pfx = prefix(lvl);
    const std::size_t pfx_len = std::strlen(pfx);
    if (pfx_len >= sizeof(buf) - 2) return;
    std::memcpy(buf, pfx, pfx_len);

    va_list ap;
    va_start(ap, fmt);
    const int n = std::vsnprintf(buf + pfx_len, sizeof(buf) - pfx_len - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;

    std::size_t total = pfx_len + static_cast<std::size_t>(n);
    if (total >= sizeof(buf) - 1) total = sizeof(buf) - 2;

    // Forward to the bridge BEFORE appending '\n' so the message text the PC
    // tab sees doesn't include a trailing newline (the Python logger adds its
    // own per-record line break). enqueueRemoteLog itself is no-op when the
    // bridge isn't connected, so this is cheap on the disconnect path too.
    if (static_cast<int>(lvl) >= SMOAP_LOG_FORWARD_MIN_LEVEL) {
        // Send body without the "[smoap xxx] " prefix — the level is a
        // structured field on the wire, and the PC tab is already labelled.
        const char saved = buf[total];
        buf[total] = '\0';
        smoap::ap::enqueueRemoteLog(wireLevelName(lvl), buf + pfx_len);
        buf[total] = saved;
    }

    buf[total++] = '\n';

    if (static_cast<int>(lvl) >= SMOAP_LOG_SINK_MIN_LEVEL) {
        hk::svc::OutputDebugString(buf, total);
    }
}

void drainPendingToFile() {
    // No-op stub. The exlaunch-era SD-card boot-capture diagnostic is gone;
    // see the file-top comment for rationale.
}

}  // namespace smoap::util
