// Logging — kernel debug log + bridge log forwarder + optional SD-card sink.
//
// Every SMOAP_LOG_* call goes through log(), which:
//   1. hk::svc::OutputDebugString — kernel debug log. Visible in Ryujinx;
//      on real Switch routed to lm where binlog visibility is spotty
//      (and Atmosphere does NOT redirect lm to a per-title file on disk
//      despite older docs claiming otherwise — see Log.hpp).
//   2. Forwards messages at or above SMOAP_LOG_FORWARD_MIN_LEVEL to the
//      PC client via the bridge's outbound_logs ring.
//   3. Always accumulates to a 16 KiB in-memory ring buffer (last ~200
//      log lines). Read by ui::ApDebugConsole when networking breaks so
//      the player can see the failure tail on screen without a PC. The
//      SD-card drain below observes the same ring under the same lock.
//   4. If SMOAP_DEBUG_SD_LOG is defined at compile time: drains the ring
//      one-shot to sd:/smo_ap.txt at ~5 seconds into drawMain. Useful
//      for on-device boot debugging when Ryujinx isn't available and lm
//      is opaque. Off by default; enable via -DSMOAP_DEBUG_SD_LOG=ON.
//
//      Symbol resolution: the nn::fs::* entry points are looked up at
//      *runtime* via hk::ro::lookupSymbol on first drain, NOT via sail's
//      load-time .sym mechanism. Earlier attempt with syms/nn/fs.sym
//      crashed the subsdk during init on real Switch (sail's
//      SymbolEntry::apply faulted on an nn::fs::* entry that retail
//      SMO 1.0.0 doesn't export or that sail can't patch under retail's
//      memory permissions). The runtime path soft-fails per-call instead.
//      See memory/project_sail_missing_symbol_crashes_init.md.
//
// Allocator discipline (vestigial): the ring buffer is a plain char array,
// the position guarded by std::atomic_flag spinlock + std::atomic<u32>.
// The lock-free path was originally required to avoid the exlaunch-era
// libstdc++ allocator NULL-deref on worker threads; under Hakkun's musl +
// LLVM libc++ + HeapSourceDynamic the constraint is gone, but the simple
// ring + spinlock is fine to keep — drain happens on the frame thread.

#include "Log.hpp"

#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>

#include <hk/svc/api.h>

#include "../ap/ApFrameBridge.hpp"

#ifdef SMOAP_DEBUG_SD_LOG
#  include <hk/ro/RoUtil.h>
#  include <hk/types.h>
#endif

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

// ---- always-on ring buffer ----------------------------------------------
//
// Holds the last ~200 log lines (~16 KiB worth) in memory so the on-Switch
// debug overlay (ui::ApDebugConsole) can dump them when networking breaks
// and the PC-tab log forwarder isn't useful. Same spinlock + atomic
// pattern the SD-card sink used; the ring is now unconditional and the
// SD-card drain just observes it through the same lock.
constexpr std::size_t kRingCap = 16 * 1024;
char g_ring[kRingCap];
std::atomic<std::uint32_t> g_ring_used{0};
std::atomic_flag g_ring_lock = ATOMIC_FLAG_INIT;

class SpinGuard {
public:
    SpinGuard() {
        while (g_ring_lock.test_and_set(std::memory_order_acquire)) {
            // Spin briefly; ring writes complete in microseconds.
        }
    }
    ~SpinGuard() { g_ring_lock.clear(std::memory_order_release); }
};

void ringAppend(const char* buf, std::size_t len) {
    if (len == 0 || len > kRingCap) return;
    SpinGuard g;
    std::uint32_t used = g_ring_used.load(std::memory_order_relaxed);
    if (used + len > kRingCap) {
        // Drop oldest by shifting. Keeps the newest lines visible even
        // when the buffer fills.
        std::uint32_t drop = used + static_cast<std::uint32_t>(len) - kRingCap;
        if (drop > used) drop = used;
        std::memmove(g_ring, g_ring + drop, used - drop);
        used -= drop;
    }
    std::memcpy(g_ring + used, buf, len);
    g_ring_used.store(used + static_cast<std::uint32_t>(len),
                      std::memory_order_relaxed);
}

#ifdef SMOAP_DEBUG_SD_LOG

// Type shapes mirror lunakit-vendor/src/nn/fs/{fs_files,fs_mount,fs_types}.hpp;
// keep the FileHandle / WriteOption layouts byte-identical to the SDK or the
// calling convention drifts on retail hardware.
namespace nnfs {
struct FileHandle { unsigned long long _internal; };
struct WriteOption { int flags; };
constexpr int kWriteOptionFlush = 1 << 0;
constexpr int kOpenModeWrite = 2;  // Read=1, Write=2, Append=4

using MountSdCardForDebugFn = bool (*)(char const*);
using CreateFileFn          = unsigned int (*)(char const*, long long);
using OpenFileFn            = unsigned int (*)(FileHandle*, char const*, int);
using CloseFileFn           = void (*)(FileHandle);
using WriteFileFn           = unsigned int (*)(FileHandle, long long,
                                               void const*, unsigned long long,
                                               WriteOption const&);
using DeleteFileFn          = unsigned int (*)(char const*);

// Cached function pointers. Resolved once at first drain via
// hk::ro::lookupSymbol; nullptr after lookup means "not exported on this
// firmware" and the drain bails out (soft-fail, no module abort).
MountSdCardForDebugFn s_MountSdCardForDebug = nullptr;
CreateFileFn          s_CreateFile          = nullptr;
OpenFileFn            s_OpenFile            = nullptr;
CloseFileFn           s_CloseFile           = nullptr;
WriteFileFn           s_WriteFile           = nullptr;
DeleteFileFn          s_DeleteFile          = nullptr;
bool                  s_symbols_resolved    = false;
bool                  s_symbols_ok          = false;

// Mangled via aarch64-none-elf-g++ + nm against the lunakit-vendor
// signatures. Kept here (not in HookSymbols.hpp) because this is the
// only TU that references them and we explicitly want runtime — not
// sail load-time — resolution.
bool resolveSymbols() {
    if (s_symbols_resolved) return s_symbols_ok;
    s_symbols_resolved = true;

    auto resolve = [](const char* mangled) -> ::ptr {
        const ::ptr addr = hk::ro::lookupSymbol(mangled);
        if (addr == 0) {
            SMOAP_LOG_WARN("[sd-log] lookupSymbol miss: %s", mangled);
        }
        return addr;
    };
    // Try two manglings in sequence and return whichever resolves. Logs a
    // single miss line for the primary; the alt is exercised silently.
    auto resolveAlt = [](const char* primary, const char* alt) -> ::ptr {
        ::ptr addr = hk::ro::lookupSymbol(primary);
        if (addr != 0) return addr;
        addr = hk::ro::lookupSymbol(alt);
        if (addr == 0) {
            SMOAP_LOG_WARN("[sd-log] lookupSymbol miss: %s (also tried %s)",
                           primary, alt);
        }
        return addr;
    };

    // s64/u64 in Nintendo headers typedef to `long`/`unsigned long` on LP64
    // aarch64 (encoded `l`/`m`), NOT to `long long`/`unsigned long long`
    // (`x`/`y`). aarch64-none-elf-g++ on the host emits the `x`/`y` form for
    // the lunakit-vendor signatures because the scratch typedef'd s64 as
    // `long long`. Both are 64-bit and call-convention-equivalent, but the
    // mangled name differs — main.nso exports the `l`/`m` form. Try the
    // `l`/`m` variant first since that matches what SMO actually ships.
    const ::ptr a_mount  = resolve("_ZN2nn2fs19MountSdCardForDebugEPKc");
    const ::ptr a_create = resolveAlt("_ZN2nn2fs10CreateFileEPKcl",
                                      "_ZN2nn2fs10CreateFileEPKcx");
    const ::ptr a_open   = resolve("_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci");
    const ::ptr a_close  = resolve("_ZN2nn2fs9CloseFileENS0_10FileHandleE");
    const ::ptr a_write  = resolveAlt(
        "_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE",
        "_ZN2nn2fs9WriteFileENS0_10FileHandleExPKvyRKNS0_11WriteOptionE");
    const ::ptr a_delete = resolve("_ZN2nn2fs10DeleteFileEPKc");

    if (!(a_mount && a_create && a_open && a_close && a_write && a_delete)) {
        SMOAP_LOG_WARN("[sd-log] nn::fs symbol set incomplete on this build; "
                       "boot-time SD capture disabled");
        return false;
    }

    s_MountSdCardForDebug = reinterpret_cast<MountSdCardForDebugFn>(a_mount);
    s_CreateFile          = reinterpret_cast<CreateFileFn>(a_create);
    s_OpenFile            = reinterpret_cast<OpenFileFn>(a_open);
    s_CloseFile           = reinterpret_cast<CloseFileFn>(a_close);
    s_WriteFile           = reinterpret_cast<WriteFileFn>(a_write);
    s_DeleteFile          = reinterpret_cast<DeleteFileFn>(a_delete);
    s_symbols_ok = true;
    return true;
}

}  // namespace nnfs

// One-shot drain strategy. Sustained file I/O from the frame thread aborts
// in nn::fs (Result 0xCA8 from internal FlushFile, hit during development).
// Drain ONCE at kDrainAtFrame (~5s of drawMain), set g_drain_done, never
// touch nn::fs again. ~5s is enough for the worker thread to attempt its
// first connect and log success/timeout — which is the only thing this
// diagnostic is for in practice.
constexpr std::uint32_t kDrainAtFrame = 300;  // ~5 seconds at 60 fps
std::uint32_t g_drawmain_frames = 0;
std::atomic<bool> g_drain_done{false};

// SD root, NOT under atmosphere/contents/<TID>/ — Atmosphere writes
// romfs_metadata.bin into that directory during boot and the resulting
// dir-level lock conflict made WriteFile abort with TargetLocked
// (Result 0xCA8). Root SD path avoids the conflict.
const char* kLogFilePath = "sd:/smo_ap.txt";

#endif  // SMOAP_DEBUG_SD_LOG

}  // namespace

void markFsReady() {
    // No-op stub kept for source compat with older call sites.
    // drainPendingToFile() mounts SD itself on first use.
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

    // Capture every level (incl. DEBUG) into the always-on ring. The
    // on-Switch debug overlay reads this when networking breaks; the
    // optional SD-card drain (below) observes the same ring.
    ringAppend(buf, total);
}

char* snapshotRecentLogs(char* out, std::size_t cap, std::size_t* out_len) {
    if (!out || cap == 0) {
        if (out_len) *out_len = 0;
        return out;
    }
    SpinGuard g;
    const std::uint32_t used = g_ring_used.load(std::memory_order_relaxed);
    const std::size_t take = (used < cap) ? used : cap;
    std::memcpy(out, g_ring, take);
    if (out_len) *out_len = take;
    return out;
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

    // Runtime symbol resolution. Soft-fail to no-op if any nn::fs::* entry
    // point isn't exported on this firmware.
    if (!nnfs::resolveSymbols()) return;

    char snapshot[kRingCap];
    std::size_t snap_len = 0;
    {
        SpinGuard g;
        snap_len = g_ring_used.load(std::memory_order_relaxed);
        if (snap_len == 0) return;
        std::memcpy(snapshot, g_ring, snap_len);
    }

    // Exact lunakit FsHelper::writeFileToPath pattern: CreateFile sized to
    // the EXACT data we're about to write, not 0. CreateFile(0) +
    // WriteFile(N) extends the file and aborts in nn::fs past trivial
    // sizes (Result 0xCA8 in FlushFile). Pre-sizing eliminates the
    // extension path entirely.
    (void)nnfs::s_MountSdCardForDebug("sd");
    (void)nnfs::s_DeleteFile(kLogFilePath);
    if (nnfs::s_CreateFile(kLogFilePath,
                           static_cast<long long>(snap_len)) != 0) {
        return;
    }

    nnfs::FileHandle fh{};
    if (nnfs::s_OpenFile(&fh, kLogFilePath, nnfs::kOpenModeWrite) != 0) return;

    // Per lunakit: bail without CloseFile on WriteFile failure.
    nnfs::WriteOption opt{ nnfs::kWriteOptionFlush };
    if (nnfs::s_WriteFile(fh, 0, snapshot, snap_len, opt) != 0) return;
    nnfs::s_CloseFile(fh);
#endif  // SMOAP_DEBUG_SD_LOG
}

}  // namespace smoap::util
