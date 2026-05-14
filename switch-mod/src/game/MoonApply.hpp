// Game-side moon-flag manipulation.
//
// Used both by the moon-get hook (extract coords from a just-collected shine)
// and by the AP item application path (write moon flags so SMO behaves as if
// a moon was collected, opening gates).

#pragma once

#include <cstdint>
#include <string>

namespace smoap::game {

// Write the moon-collected flag for (kingdom, shine_id) via GameDataHolder.
// Sets ApState::synthetic_grant_this_frame around the call so our own
// moon-get hook does not re-emit the check upstream.
//
// Idempotent: safe to call repeatedly with the same args (no-op if already set).
void grantShine(const std::string& kingdom, const std::string& shine_id);

// Reverse: from a ShineActor* (or whatever the hook receives), pull out the
// canonical kingdom name and shine_id used in apworld/data/locations.json.
// Returns false if the shine cannot be identified (silent drop).
bool extractShineCoords(/* ShineActor* shine, */
                       std::string& out_kingdom,
                       std::string& out_shine_id);

}  // namespace smoap::game
