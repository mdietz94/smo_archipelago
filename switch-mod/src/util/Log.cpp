// Logging: kernel debug log only.
//
// Output goes via svcOutputDebugString from every thread (always safe; raw
// kernel syscall, no per-thread state needed). Ryujinx surfaces this as
// `[smoap inf] ...` lines in its log window. On real Switch the output
// lands in lm capture when Atmosphere's log_manager is enabled — but that
// setting crashes the user's HATS pack, so on real Switch logs are
// effectively write-only. We rely on the bridge's PC-side log + the web
// tracker for runtime visibility there.
//
// We deliberately do NOT try to write to the SD card via nn::fs:
//   - MountSdCardForDebug fails on retail-mode emulators / newer firmware.
//   - nn::fs IPC calls crash on threads spawned via raw svcCreateThread.
//   - The buffering / lock-free ring / frame-thread drain we needed to
//     make file logging safe added complexity disproportionate to the win.

#include "Log.hpp"

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include "lib/nx/nx.h"

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

}  // namespace

void markFsReady() {}            // kept as a no-op for source compat
void drainPendingToFile() {}     // kept as a no-op for source compat

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
    buf[total++] = '\n';

    svcOutputDebugString(buf, total);
}

}  // namespace smoap::util
