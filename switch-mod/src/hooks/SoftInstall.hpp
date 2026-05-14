// Soft-install wrapper for hooks.
//
// LunaKit's HOOK_DEFINE_TRAMPOLINE provides `Hook::InstallAtSymbol(sym)` which
// aborts via R_ABORT_UNLESS if nn::ro::LookupSymbol fails — opaque from the
// outside (no indication of WHICH symbol failed).
//
// For M3 diagnostic visibility, this wrapper does a probe lookup FIRST. On
// success it logs the resolved address and delegates to InstallAtSymbol. On
// failure it logs and skips, letting the module keep running so we can see
// all symbol failures in one boot instead of one-at-a-time.

#pragma once

#include "lib/nx/nx.h"  // result.h via extern "C" wrapper
#include "nn/ro.h"
#include "../util/Log.hpp"

namespace smoap::hooks {

template <typename Hook>
bool softInstallAtSymbol(const char* sym) {
    uintptr_t address = 0;
    const Result rc = nn::ro::LookupSymbol(&address, sym);
    if (R_FAILED(rc)) {
        SMOAP_LOG_ERROR("LookupSymbol FAILED rc=0x%x sym=%s", rc, sym);
        return false;
    }
    SMOAP_LOG_INFO("LookupSymbol OK @ 0x%lx sym=%s", address, sym);
    Hook::InstallAtSymbol(sym);  // safe — we just confirmed it resolves
    return true;
}

}  // namespace smoap::hooks
