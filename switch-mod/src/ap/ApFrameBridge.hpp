// Marshals between the frame thread and the socket thread.
//
// Hooks call into here from the frame thread. We push checks onto
// ApState::outbound_checks (lock-free) for the socket thread to drain.

#pragma once

#include <cstdint>
#include <string>

namespace smoap::ap {

// M4: hooks pass raw SMO identifiers; the bridge resolves them to AP location
// names. Switch-side dedupe still works via the FNV hash of the full Check.
//
// const char* overloads are cheaper for hook callbacks (no std::string alloc
// on the frame thread). Null is treated as empty.

// MoonGetHook -> sends raw {stage_name, object_id, shine_uid} to the bridge.
void reportMoonChecked(const char* stage_name, const char* object_id, int shine_uid);

// CaptureStartHook -> sends raw hack_name (e.g. "Goomba", "Kuribo") to the bridge.
void reportCaptureChecked(const char* hack_name);

// ScenarioFlagHook -> sends tracker-UI hint with the new scenario number.
void reportStatus(const char* stage_name, int scenario_no);

// EndingHook -> sends goal=true; idempotent via ApState::goal_sent.
void reportGoal();

// DeathHook -> sends death event; debounced via ApState::death_pending_send.
void reportDeath();

// smoap::util::log() forwarder. Pushes a Log entry into
// ApState::outbound_logs for the worker thread to ship. `level` is one of
// "info" / "warn" / "error" / "debug". `msg` is the already-formatted
// message body (no level prefix). Drops silently with a counter bump on
// ring full — pumpOnce surfaces the drop count as a synthetic WARN line.
//
// Safe to call from ANY thread (frame, worker, hook callbacks). Producer
// serialization is internal to this function (atomic_flag spinlock). The
// caller (smoap::util::log) is responsible for re-entry guarding so we
// don't recurse during pumpOnce's own logging.
void enqueueRemoteLog(const char* level, const char* msg);

}  // namespace smoap::ap
