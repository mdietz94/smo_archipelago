// DeathHook public surface.
//
// install() is called from exl_main. synthKillMario() is the inbound-DeathLink
// apply path: ApState calls this from the frame thread with the cached
// PlayerHitPointData* that DeathHook saw on the last organic death, and we
// re-enter PlayerHitPointData::kill via the trampoline's Orig pointer (which
// skips our Callback, so the synthetic death doesn't echo back out as a fresh
// outbound DeathLink).

#pragma once

class PlayerHitPointData;

namespace smoap::hooks {

void installDeathHook();

// Kill Mario from outside the hook path by invoking the un-hooked original.
// Caller must ensure hp != nullptr and that suppressing the outbound report
// is desired (set ApState::synthetic_death_this_frame before calling).
void synthKillMario(PlayerHitPointData* hp);

}  // namespace smoap::hooks
