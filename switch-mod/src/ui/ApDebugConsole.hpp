// On-Switch ImGui-backed debug console. Pops up when SMOClient is
// unreachable so the player can see (a) the discovery report (own IP,
// probed subnet, last reply target), (b) the connection state, and
// (c) the tail of the in-memory log ring — without having to plug into
// the PC.
//
// Visibility rule (only auto-mode; no input wiring in this iteration):
//
//   visible = (ms_since_boot > 5000) AND (!tcp_connected) AND
//             (ms_since_last_connect > 5000)
//
// Hide-on-connect is instant. The 5 s boot grace + 5 s disconnect grace
// give a healthy fresh boot a clear 10 s window where the overlay never
// flickers.
//
// Renders via LibHakkun's addons/ImGui NVN backend. The whole TU is
// guarded by `SMOAP_HAS_IMGUI` — when the addon submodules aren't
// present (e.g. CI without imgui-branch sys/), init() and draw() compile
// to no-ops so the module still builds.

#pragma once

namespace al { class Scene; }

namespace smoap::ui {

// One-time init from GameSystem::init post-orig: creates the ImGui
// ExpHeap and calls ImGuiBackendNvn::tryInitialize(). Safe to call when
// the backend isn't compiled in (no-op).
void initDebugConsole();

// Per-frame entry from drawMainHook post-orig. Pulls connection state +
// log ring + discovery report and renders the overlay if visibility
// conditions are met. Cheap when hidden — early-returns before any
// ImGui calls.
void drawDebugConsole();

// Tell the overlay we're TCP-connected to SMOClient. Hides the overlay
// instantly and resets the disconnect timestamp. Called from ApClient's
// connectOnce() success path (frame thread or worker thread; sequenced
// via atomics).
void notifyConnectChange(bool connected_now);

}  // namespace smoap::ui
