// Always-on minimal status overlay.
//
// Renders 2 lines in the corner of the screen using agl::DrawContext (the
// same primitive LunaKit uses for its toast messages). Independent of
// LunaKit's ImGui context so it works with or without LunaKit installed.

#pragma once

namespace smoap::ui {

// Called once after GameSystemInit completes.
void initHud();

// Called from the drawMain trampoline once per frame (after applyOnFrame).
void drawHudFrame();

}  // namespace smoap::ui
