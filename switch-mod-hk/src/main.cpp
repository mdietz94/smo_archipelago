// Spicy Meatball Overdrive — Hakkun edition entry point.
//
// Phase 1: empty hkMain — proves the toolchain produces a loadable .nso.
// Phase 3 adds the AP socket pool init. Phase 4 adds the 26 trampoline + 1
// inline hook installs. This file is intentionally minimal during phases 1-2.

extern "C" void hkMain() {
}
