// Trampoline on GameDataFunction::addHackDictionary(GameDataHolderWriter,
// const char*). Filters out writes for captures the player hasn't unlocked
// via AP so the in-game Capture List stays in sync with AP state — usable
// as the player's authoritative "what am I allowed to capture" reference.
//
// Pairs with M7 phase A's deferred forceKillHack (CaptureStartHook): that
// path lets `Orig(startHack)` run, during which SMO's organic capture flow
// writes the hack name into the dictionary. No removeFromHackDictionary
// symbol exists, so we filter on the write side.
//
// Re-entry contract: CaptureGate::grantCapture calls into the same function
// pointer this hook is patched onto, so our own grant path re-enters the
// Callback. ApState::applyOnFrame sets captures_unlocked[bit] BEFORE
// calling grantCapture, so the filter's captureBlocked() check returns
// false on the AP-grant path and Orig runs normally.
//
// SaveLoadHook flips ApState::save_load_passthrough around its Orig so
// initializeData can rehydrate the dictionary from save unconditionally —
// otherwise our filter would silently truncate the save's captures down to
// whatever the bridge has rehello'd so far.

#pragma once

namespace smoap::hooks {

void installAddHackDictionaryHook();

}  // namespace smoap::hooks
