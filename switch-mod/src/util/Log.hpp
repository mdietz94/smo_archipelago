// Lightweight logging.
//
// Writes to:
//   - SMO's debug output where available (sead::print)
//   - LunaKit's log buffer if loaded (so messages appear in its log window)
//   - sd:/atmosphere/contents/<TID>/logs/ap_<datetime>.log on init failure (M8)

#pragma once

#include <cstdarg>

namespace smoap::util {

enum class LogLevel { Debug, Info, Warn, Error };

void log(LogLevel lvl, const char* fmt, ...);

}  // namespace smoap::util

#define SMOAP_LOG_DEBUG(...) ::smoap::util::log(::smoap::util::LogLevel::Debug, __VA_ARGS__)
#define SMOAP_LOG_INFO(...)  ::smoap::util::log(::smoap::util::LogLevel::Info,  __VA_ARGS__)
#define SMOAP_LOG_WARN(...)  ::smoap::util::log(::smoap::util::LogLevel::Warn,  __VA_ARGS__)
#define SMOAP_LOG_ERROR(...) ::smoap::util::log(::smoap::util::LogLevel::Error, __VA_ARGS__)

namespace smoap::util {
// Mark FS as available. Call AFTER nn::fs::MountSdCardForDebug("sd") (done
// in GameSystemInit hook / DrawMain fallback). All log() calls before this
// are kept in the ring buffer and flushed on the next drainPendingToFile().
void markFsReady();

// Flush the ring buffer to sd:/atmosphere/contents/<TID>/smoap.log. MUST be
// called from a thread nn::fs accepts (frame thread). Call once per frame
// from inside drawMain — cheap when ring is empty.
void drainPendingToFile();
}  // namespace smoap::util
