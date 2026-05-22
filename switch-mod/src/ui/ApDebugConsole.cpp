// On-Switch ImGui debug overlay. Uses LibHakkun's ImGui addon (NVN
// backend) — the exact same setup Kgamer77/SMOO-Plus-Hakkun ships.
//
// Init flow (matches Kgamer's pattern):
//   1. hkMain calls `ImGuiBackendNvn::instance()->installHooks(false)` —
//      this asks the Nvn addon's bootstrap trampoline to NOT auto-init
//      ImGui when NVN comes up. We'll do it lazily.
//   2. First drawDebugConsole() call: if we haven't initialized ImGui yet,
//      call setup() (creates a 2 MiB sead::ExpHeap, wires allocator, calls
//      tryInitialize). Lazy because NVN must be live first.
//   3. Subsequent draws: NewFrame → render our window → Render → backend
//      draws into the current command buffer.
//
// Visibility rule: hidden until 5s after boot AND TCP has been down for >5s.
// Hides instantly on TCP-up. See header.

#include "ApDebugConsole.hpp"

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "../ap/ApDiscovery.hpp"
#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

#ifdef SMOAP_HAS_DEBUG_RENDERER
#  include "imgui.h"
#  include "hk/gfx/ImGuiBackendNvn.h"
#  include <sead/heap/seadExpHeap.h>
#  include "al/Library/Memory/HeapUtil.h"
#  include "al/Library/System/GameSystemInfo.h"  // al::DrawSystemInfo definition
#  include "game/System/Application.h"
#  include "agl/common/aglDrawContext.h"
#  include "EmbeddedFontKarla.hpp"
#endif

namespace smoap::ui {

namespace {

// Boot-time + connect-time grace windows (ms).
constexpr std::int64_t kBootGraceMs       = 5000;
constexpr std::int64_t kDisconnectGraceMs = 5000;

// State inputs. Written from any thread; read on the frame thread.
std::atomic<bool>         s_tcp_connected{false};
std::atomic<std::int64_t> s_last_connect_ms{0};
std::atomic<std::int64_t> s_boot_ms{0};

#ifdef SMOAP_HAS_DEBUG_RENDERER
sead::Heap* s_imgui_heap = nullptr;
bool        s_setup_done = false;
bool        s_setup_failed = false;
#endif

bool overlayShouldShow() {
    const std::int64_t boot_ms = s_boot_ms.load(std::memory_order_relaxed);
    if (boot_ms == 0) return false;
    const std::int64_t now = ap::ApState::nowMs();
    const std::int64_t since_boot = now - boot_ms;
    if (since_boot < kBootGraceMs) return false;
    if (s_tcp_connected.load(std::memory_order_acquire)) return false;
    const std::int64_t last_conn = s_last_connect_ms.load(std::memory_order_relaxed);
    const std::int64_t since_disconnect =
        (last_conn == 0) ? since_boot : (now - last_conn);
    return since_disconnect > kDisconnectGraceMs;
}

#ifdef SMOAP_HAS_DEBUG_RENDERER

void formatIp(char* out, std::size_t cap, std::uint32_t ip_ho) {
    std::snprintf(out, cap, "%u.%u.%u.%u",
                  (ip_ho >> 24) & 0xFF, (ip_ho >> 16) & 0xFF,
                  (ip_ho >> 8)  & 0xFF, (ip_ho >> 0)  & 0xFF);
}

// Kgamer-style lazy setup. Called from drawDebugConsole on first draw,
// after NVN is up. Allocates 2 MiB ExpHeap + wires allocator + tryInit.
bool ensureSetup() {
    if (s_setup_done) return true;
    if (s_setup_failed) return false;

    s_imgui_heap = sead::ExpHeap::create(
        2 * 1024 * 1024, "ApImGuiHeap", al::getStationedHeap(),
        8, sead::Heap::cHeapDirection_Forward, false);
    if (!s_imgui_heap) {
        SMOAP_LOG_ERROR("[overlay] sead::ExpHeap::create failed; overlay disabled");
        s_setup_failed = true;
        return false;
    }

    auto* backend = hk::gfx::ImGuiBackendNvn::instance();
    backend->setAllocator({
        [](::size sz, ::size align) -> void* {
            return s_imgui_heap->tryAlloc(sz, align);
        },
        [](void* p) -> void {
            if (s_imgui_heap) s_imgui_heap->free(p);
        },
    });
    if (!backend->tryInitialize()) {
        SMOAP_LOG_ERROR("[overlay] ImGuiBackendNvn::tryInitialize failed; overlay disabled");
        s_setup_failed = true;
        return false;
    }
    // Swap the default ProggyClean (13px bitmap — blurry when stretched)
    // for Karla rasterized at 22px. TTF outlines sharpen at any size; the
    // earlier `FontGlobalScale = 1.5f` was nearest-neighbour stretching
    // the bitmap and looked aliased on a TV. The addon already built the
    // default atlas inside tryInitialize(), so we clear it, add Karla,
    // and re-upload to NVN via initTexture().
    auto& io = ImGui::GetIO();
    io.Fonts->Clear();
    ImFontConfig cfg;
    cfg.FontDataOwnedByAtlas = false;  // backing array is our static const
    cfg.OversampleH = 2;
    cfg.OversampleV = 1;
    cfg.PixelSnapH  = false;
    ImFont* font = io.Fonts->AddFontFromMemoryTTF(
        const_cast<unsigned char*>(kKarlaRegularTtfData),
        static_cast<int>(kKarlaRegularTtfSize),
        22.0f, &cfg);
    if (!font) {
        SMOAP_LOG_WARN("[overlay] AddFontFromMemoryTTF returned null; falling back to default");
        io.Fonts->AddFontDefault();
    }
    backend->initTexture(false);  // re-bake atlas + re-upload to NVN
    s_setup_done = true;
    SMOAP_LOG_INFO("[overlay] ImGui NVN backend ready (Karla 22px)");
    return true;
}

void renderOverlayWindow() {
    ap::DiscoveryReport rep{};
    ap::snapshotDiscoveryReport(rep);

    char seed_s[24] = "?";
    if (rep.self_ip != 0) formatIp(seed_s, sizeof(seed_s), rep.self_ip);

    const bool tcp_up = s_tcp_connected.load(std::memory_order_acquire);
    const std::int64_t now = ap::ApState::nowMs();
    const std::int64_t since_boot = now - s_boot_ms.load(std::memory_order_relaxed);

    ImGui::SetNextWindowPos(ImVec2(20, 20), ImGuiCond_Always);
    ImGui::SetNextWindowSize(ImVec2(900, 500), ImGuiCond_FirstUseEver);
    constexpr int kFlags = ImGuiWindowFlags_NoMove
                         | ImGuiWindowFlags_NoCollapse
                         | ImGuiWindowFlags_NoFocusOnAppearing
                         | ImGuiWindowFlags_NoNav
                         | ImGuiWindowFlags_NoSavedSettings;
    if (!ImGui::Begin("Spicy Meatball Overdrive  -- debug", nullptr, kFlags)) {
        ImGui::End();
        return;
    }

    ImGui::Text("Connection: %s    uptime %llds",
                tcp_up ? "OK (TCP up)" : "DISCONNECTED",
                static_cast<long long>(since_boot / 1000));
    ImGui::Text("Sweep seed: %s    last sweep probed=%u replies=%u  loopback=%s",
                seed_s,
                static_cast<unsigned>(rep.probed_count),
                static_cast<unsigned>(rep.replies),
                rep.loopback_used ? "yes" : "no");
    if (rep.last_bridge_port != 0) {
        ImGui::Text("Last bridge reply: %s:%u  (at %llds)",
                    rep.last_bridge_host, rep.last_bridge_port,
                    static_cast<long long>(rep.last_success_ms / 1000));
    } else {
        ImGui::Text("Last bridge reply: (none received yet)");
    }
    ImGui::Separator();
    ImGui::Text("Recent log:");

    // 16 KiB scratch lives at file scope to keep frame-thread stack small.
    static char s_log_buf[16 * 1024];
    std::size_t log_len = 0;
    util::snapshotRecentLogs(s_log_buf, sizeof(s_log_buf) - 1, &log_len);
    s_log_buf[log_len] = '\0';

    ImGui::BeginChild("log_scroll", ImVec2(0, 0), false,
                      ImGuiWindowFlags_HorizontalScrollbar);
    ImGui::TextUnformatted(s_log_buf, s_log_buf + log_len);
    if (ImGui::GetScrollY() >= ImGui::GetScrollMaxY() - 10.0f) {
        ImGui::SetScrollHereY(1.0f);
    }
    ImGui::EndChild();

    ImGui::End();
}

#endif  // SMOAP_HAS_DEBUG_RENDERER

}  // namespace

void notifyConnectChange(bool connected_now) {
    const bool was = s_tcp_connected.exchange(connected_now, std::memory_order_acq_rel);
    if (connected_now != was) {
        s_last_connect_ms.store(ap::ApState::nowMs(), std::memory_order_relaxed);
        SMOAP_LOG_INFO("[overlay] TCP %s -> %s",
                       was ? "up" : "down",
                       connected_now ? "up" : "down");
    }
}

void initDebugConsole() {
    s_boot_ms.store(ap::ApState::nowMs(), std::memory_order_release);
#ifdef SMOAP_HAS_DEBUG_RENDERER
    // Kgamer-pattern: do the ImGui setup HERE (called from gameSystemInit
    // pre-orig in main.cpp), BEFORE SMO does its NVN init. That way the
    // addon's nvnDeviceInitialize override (which fires later inside
    // SMO's NVN bring-up via our installHooks-installed bootstrap hook)
    // hands the device to an ALREADY-initialized ImGui backend instead
    // of one waiting to be set up.
    if (!ensureSetup()) {
        SMOAP_LOG_WARN("[overlay] initDebugConsole: ensureSetup failed");
    }
#else
    SMOAP_LOG_INFO("[overlay] built without SMOAP_HAS_DEBUG_RENDERER — debug overlay disabled");
#endif
}

void drawDebugConsole() {
#ifdef SMOAP_HAS_DEBUG_RENDERER
    if (!overlayShouldShow()) return;

    auto* backend = hk::gfx::ImGuiBackendNvn::instance();
    // Lazy-setup on first eligible draw, once NVN has come up. If NVN
    // hasn't bootstrapped yet, the addon won't have a device wired and
    // tryInitialize will return false — keep retrying each frame until
    // it succeeds. (s_setup_failed gates this if we want to give up.)
    if (!ensureSetup()) return;

    // Need the current frame's NVN command buffer.
    auto* app = Application::instance();
    if (!app || !app->mDrawSystemInfo) return;
    auto* drawContext = app->mDrawSystemInfo->drawContext;
    if (!drawContext) return;

    ImGui::NewFrame();
    renderOverlayWindow();
    ImGui::Render();
    // Inline the NVN cmd-buffer pointer — it's a detail::Ptr<void>
    // wrapper, not a raw pointer, so we can't `auto*` it locally
    // without an explicit cast. Pass it through verbatim, matching
    // Kgamer77/SMOO-Plus-Hakkun:src/main.cpp.
    backend->draw(ImGui::GetDrawData(),
                  drawContext->getCommandBuffer()->ToData()->pNvnCommandBuffer);
#endif
}

}  // namespace smoap::ui
