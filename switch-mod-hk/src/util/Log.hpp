// Lightweight logging.
//
// Each SMOAP_LOG_* writes to svcOutputDebugString (Ryujinx-visible; on real
// Switch goes to lm where binlog visibility is spotty).
//
// Optional: configure with -DSMOAP_DEBUG_SD_LOG=ON to additionally dump
// the first ~5 seconds of log output as a one-shot file write to
// sd:/smo_ap.txt at drawMain frame 300. This is purely a boot-time
// diagnostic for cases where lm + svcOutputDebugString are both invisible
// (e.g. real Switch with no PC monitor). Off by default; see
// switch-mod/CMakeLists.txt SMOAP_DEBUG_SD_LOG option.
//
// log() is safe to call from any thread (init, worker, frame, hooks);
// allocator-free, atomic_flag spinlock + memcpy when the ring is enabled.

#pragma once

#include <cstdarg>

namespace smoap::util {

enum class LogLevel { Debug, Info, Warn, Error };

void log(LogLevel lvl, const char* fmt, ...);

}  // namespace smoap::util

// Host-test builds (test_cappy_messenger.cpp, test_protocol.cpp, etc.) define
// SMOAP_HOST_TEST and link only the pure-logic .cpp files. Stub the macros to
// no-ops so we don't drag Log.cpp + every Switch dep in for tests.
#ifdef SMOAP_HOST_TEST
#  define SMOAP_LOG_DEBUG(...) ((void)0)
#  define SMOAP_LOG_INFO(...)  ((void)0)
#  define SMOAP_LOG_WARN(...)  ((void)0)
#  define SMOAP_LOG_ERROR(...) ((void)0)
#else
#  define SMOAP_LOG_DEBUG(...) ::smoap::util::log(::smoap::util::LogLevel::Debug, __VA_ARGS__)
#  define SMOAP_LOG_INFO(...)  ::smoap::util::log(::smoap::util::LogLevel::Info,  __VA_ARGS__)
#  define SMOAP_LOG_WARN(...)  ::smoap::util::log(::smoap::util::LogLevel::Warn,  __VA_ARGS__)
#  define SMOAP_LOG_ERROR(...) ::smoap::util::log(::smoap::util::LogLevel::Error, __VA_ARGS__)
#endif

namespace smoap::util {
// No-op stub kept for source compat with older call sites.
void markFsReady();

// Compile-time-gated diagnostic: when SMOAP_DEBUG_SD_LOG is defined, drains
// the ring buffer to sd:/smo_ap.txt exactly once per session at drawMain
// frame ~300 (~5s in). When the flag is undefined, this is a no-op.
//
// Call once per frame from DrawMainHook. Cheap atomic-load early returns
// on every call except the single drain. Must run on the frame thread
// when active — the worker thread isn't safe for nn::fs.
void drainPendingToFile();
}  // namespace smoap::util
