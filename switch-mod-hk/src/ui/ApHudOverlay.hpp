// Connection-status heartbeat log (and per-frame entry point reserved for
// future on-screen UI). In-game item notifications are owned by
// CappyMessenger — this file does NOT render text.

#pragma once

namespace smoap::ui {

// Called once after GameSystemInit completes.
void initHud();

// Called from the drawMain trampoline once per frame (after applyOnFrame).
// Logs an AP-connection heartbeat to lm.log every ~1s. No on-screen drawing.
void drawHudFrame();

}  // namespace smoap::ui
