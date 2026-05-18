// Logging — kernel debug log, optionally also an SD-card file dump.
//
// Every SMOAP_LOG_* call goes through log(), which:
//   1. svcOutputDebugString — kernel debug log (Ryujinx surface; on real
//      Switch with Atmosphere it goes to lm, where binlog visibility is
//      spotty and we mostly don't rely on it).
//   2. If SMOAP_DEBUG_SD_LOG is defined at compile time: also accumulates
//      to an 8 KiB in-memory ring buffer, drained one-shot to sd:/smo_ap.txt
//      at ~5 seconds into drawMain. Useful for boot-time debugging when
//      neither lm nor svcOutputDebugString is visible. Off by default;
//      enable via -DSMOAP_DEBUG_SD_LOG=ON at cmake configure.
//
// Allocator discipline (M6.1): the ring buffer is a plain char array, the
// position guarded by std::atomic_flag spinlock + std::atomic<u32>. No
// std::string, no std::mutex (the libstdc++ allocator NULL-derefs on the
// worker thread). All callers — init, worker, frame, hook callbacks — go
// through the same lock-free-write path.

#include "Log.hpp"

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include "lib/nx/nx.h"

#ifdef SMOAP_DEBUG_SD_LOG
#  include <atomic>
#  include "nn/fs/fs_mount.hpp"
#  include "nn/fs/fs_files.hpp"
#  include "nn/fs/fs_types.hpp"
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

#ifdef SMOAP_DEBUG_SD_LOG

// Ring buffer for SD logging. Holds enough text to cover the init burst
// + the worker thread's first ~5s of activity (the window we capture).
constexpr std::size_t kRingCap = 8192;
char g_ring[kRingCap];
std::atomic<std::uint32_t> g_ring_used{0};
// Single spinlock guarding {g_ring, g_ring_used}. Atomic_flag is safe in
// our subsdk environment; std::mutex would NULL-deref via the libstdc++
// allocator (see M6.1 notes in CLAUDE.md).
std::atomic_flag g_ring_lock = ATOMIC_FLAG_INIT;

// One-shot drain strategy. Sustained file I/O from the frame thread aborts
// in nn::fs (Result 0xCA8 from internal FlushFile, hit 5+ times during
// development). The proven-working LmSinkFlushDiagToFile prototype only
// ever wrote once and that worked, so we mimic that: drain ONCE at
// kDrainAtFrame (~5s of drawMain), set g_drain_done, never touch nn::fs
// again. ~5s is enough for the worker thread to attempt its first connect
// and log success/timeout — which is the only thing this diagnostic is
// for in practice.
constexpr std::uint32_t kDrainAtFrame = 300;  // ~5 seconds at 60 fps
std::uint32_t g_drawmain_frames = 0;
std::atomic<bool> g_drain_done{false};

// SD root, NOT under atmosphere/contents/<TID>/ — Atmosphere writes
// romfs_metadata.bin into that directory during boot and the resulting
// dir-level lock conflict made our WriteFile abort with TargetLocked
// (Result 0xCA8). Root SD path avoids the conflict.
const char* kLogFilePath = "sd:/smo_ap.txt";

class SpinGuard {
public:
    SpinGuard() {
        while (g_ring_lock.test_and_set(std::memory_order_acquire)) {
            // Spin. Drainer holds for ~ms during file I/O; writers (60Hz
            // frame, 1Hz heartbeat, sporadic hooks) wait briefly.
        }
    }
    ~SpinGuard() { g_ring_lock.clear(std::memory_order_release); }
};

void ringAppend(const char* buf, std::size_t len) {
    if (len == 0) return;
    SpinGuard g;
    std::uint32_t used = g_ring_used.load(std::memory_order_relaxed);
    if (used + len > kRingCap) {
        // Drop oldest by shifting. Keeps newest log lines visible even when
        // the buffer fills before our one-shot drain fires.
        std::uint32_t drop = used + static_cast<std::uint32_t>(len) - kRingCap;
        if (drop > used) drop = used;
        std::memmove(g_ring, g_ring + drop, used - drop);
        used -= drop;
    }
    std::memcpy(g_ring + used, buf, len);
    g_ring_used.store(used + static_cast<std::uint32_t>(len),
                      std::memory_order_relaxed);
}

#endif  // SMOAP_DEBUG_SD_LOG

}  // namespace

void markFsReady() {
    // Kept as a no-op for source compat. drainPendingToFile() mounts SD
    // itself on first use.
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
    buf[total++] = '\n';

    svcOutputDebugString(buf, total);

#ifdef SMOAP_DEBUG_SD_LOG
    ringAppend(buf, total);
#endif
}

void drainPendingToFile() {
#ifdef SMOAP_DEBUG_SD_LOG
    // One-shot: drain exactly once per session, at frame kDrainAtFrame.
    // All other calls are cheap atomic-load early returns.
    if (g_drain_done.load(std::memory_order_acquire)) return;
    if (++g_drawmain_frames < kDrainAtFrame) return;

    // Mark done FIRST so a re-entry (shouldn't happen on single-threaded
    // drawMain, but defensive) doesn't double-write.
    g_drain_done.store(true, std::memory_order_release);

    char snapshot[kRingCap];
    std::size_t snap_len = 0;
    {
        SpinGuard g;
        snap_len = g_ring_used.load(std::memory_order_relaxed);
        if (snap_len == 0) return;
        std::memcpy(snapshot, g_ring, snap_len);
    }

    // Exact lunakit FsHelper::writeFileToPath pattern (lunakit-vendor/src/
    // helpers/fsHelper.cpp): CreateFile sized to the EXACT data we're
    // about to write, not 0. CreateFile(0) + WriteFile(N) extends the
    // file and aborts in nn::fs past trivial sizes (Result 0xCA8 in
    // FlushFile). Pre-sizing eliminates the extension path entirely.
    (void)nn::fs::MountSdCardForDebug("sd");
    nn::fs::DeleteFile(kLogFilePath);
    if (R_FAILED(nn::fs::CreateFile(kLogFilePath,
                                    static_cast<std::int64_t>(snap_len)))) {
        return;
    }

    nn::fs::FileHandle fh;
    if (R_FAILED(nn::fs::OpenFile(&fh, kLogFilePath, 2))) return;

    // Per lunakit: bail without CloseFile on WriteFile failure.
    nn::fs::WriteOption opt = nn::fs::WriteOption::CreateOption(
        nn::fs::WriteOptionFlag_Flush);
    if (R_FAILED(nn::fs::WriteFile(fh, 0, snapshot, snap_len, opt))) return;
    nn::fs::CloseFile(fh);
#endif  // SMOAP_DEBUG_SD_LOG
}

}  // namespace smoap::util
