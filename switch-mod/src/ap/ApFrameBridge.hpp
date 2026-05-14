// Marshals between the frame thread and the socket thread.
//
// Hooks call into here from the frame thread. We push checks onto
// ApState::outbound_checks (lock-free) for the socket thread to drain.

#pragma once

#include <string>

namespace smoap::ap {

// Called by MoonGetHook from the frame thread (game's main thread).
// Dedupes via ApState::locations_checked.
void reportMoonChecked(const std::string& kingdom, const std::string& shine_id);

// Called by CaptureStartHook (read-only path) from the frame thread.
void reportCaptureChecked(const std::string& cap);

// Called by ScenarioFlagHook to broadcast progress.
void reportStatus(const std::string& kingdom, int scenario, int moons_collected);

// Called by EndingHook exactly once per save when the goal triggers.
void reportGoal();

}  // namespace smoap::ap
