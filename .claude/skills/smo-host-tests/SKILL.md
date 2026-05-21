---
name: smo-host-tests
description: Build and run the SMO switch-mod C++ host tests (test_json, test_protocol, test_cappy_messenger, test_shine_lookup) on Windows. Use when the user mentions "host tests", "test_json", "test_protocol", "test_cappy_messenger", "test_shine_lookup", "switch-mod tests", or asks to run/build C++ tests for switch-mod/. Covers the msys2 mingw64 PATH dance + the host-side ApState::nowMs stub the CappyMessenger settle gate depends on.
---

# Switch-mod host tests (C++)

The Switch mod ships four host-runnable tests covering the JSON encoder, wire protocol, CappyMessenger speech-bubble logic, and shine_lookup (Phase 4 named-set indexing). They run on the host (Windows) compiled with standalone msys2 mingw64 g++. devkitPro doesn't ship a host compiler (its aarch64-target g++ can only emit Switch binaries).

## Compiler location

`C:\msys64\mingw64\bin\g++.exe`. The produced `.exe` needs the mingw runtime DLLs (`libstdc++-6.dll`, etc.) on PATH or it won't run.

## Build + run (from PowerShell)

```pwsh
$env:Path = "C:\msys64\mingw64\bin;" + $env:Path

# test_json (JSON encoder, LineBuffer, overflow, round-trip)
g++ -std=c++20 -Wall -Wextra -O0 -g `
    switch-mod/tests/test_json.cpp switch-mod/src/util/Json.cpp `
    -Iswitch-mod/src -o test_json.exe
.\test_json.exe

# test_protocol (wire-format encode/decode round-trip)
g++ -std=c++20 -Wall -Wextra -O0 -g `
    switch-mod/tests/test_protocol.cpp switch-mod/src/ap/ApProtocol.cpp `
    switch-mod/src/util/Json.cpp -Iswitch-mod/src -o test_protocol.exe
.\test_protocol.exe

# test_cappy_messenger (filter rules, settle gate, label substitution)
g++ -std=c++20 -Wall -Wextra -O0 -g -DSMOAP_HOST_TEST `
    switch-mod/tests/test_cappy_messenger.cpp switch-mod/src/ui/CappyMessenger.cpp `
    -Iswitch-mod/src -o test_cappy_messenger.exe
.\test_cappy_messenger.exe

# test_shine_lookup (shine_uid resolution, named-set indexing)
g++ -std=c++20 -Wall -Wextra -O0 -g -DSMOAP_HOST_TEST `
    switch-mod/tests/test_shine_lookup.cpp `
    -Iswitch-mod/src -o test_shine_lookup.exe
.\test_shine_lookup.exe
```

Expected: each exe exits 0 with `All tests passed` (or per-case `PASS` lines for `test_cappy_messenger`). `test_json` covers encoder/LineBuffer/overflow/round-trip; `test_protocol` covers every wire-protocol message type with truncation + overlong-field edge cases; `test_cappy_messenger` covers filter rules + scene-settle gate + label substitution + queue-overflow; `test_shine_lookup` covers `shineUidByStageObj` / `shineUidByDisplayName` / `isProgressionShine` + the named-moons bit indexing.

## The ApState::nowMs host-test stub

`switch-mod/src/ui/CappyMessenger.cpp` uses `smoap::ap::ApState::nowMs()` for its frame+wallclock settle gate. The full `ApState.cpp` translation unit pulls in `hk:: services` we don't link host-side, so `test_cappy_messenger.cpp` defines a stub directly in the test:

```cpp
#include "ap/ApState.hpp"
namespace { std::int64_t g_test_now_ms = 0; }
std::int64_t smoap::ap::ApState::nowMs() { return ++g_test_now_ms; }
```

Monotonic, deterministic, no Switch headers needed. If a new test needs the same pattern, copy the stub block.

## Cleanup

```pwsh
Remove-Item -Force test_json.exe, test_protocol.exe, test_cappy_messenger.exe, test_shine_lookup.exe
```

## When to add a new test

- New wire-protocol message type or field → add to `test_protocol.cpp`.
- New JSON encoder feature → add to `test_json.cpp`.
- New CappyMessenger filter rule, settle-gate edge case, or substitution behavior → add to `test_cappy_messenger.cpp`.
- New shine_table.h column / shine_lookup helper → add to `test_shine_lookup.cpp`.

Pattern from M6.1: any field that holds a string in the Switch wire-protocol must be a fixed `char[N]` — the worker thread can NOT use `std::string` historically (libstdc++ allocator NULL-derefs in the exlaunch-era subsdk9). Hakkun's musl + LLVM libc++ + HeapSourceDynamic addon lifts the restriction at runtime, but the fixed-buffer pattern stays in the wire format because the message shapes are committed contracts. Tests should cover the truncation behavior at the N boundary.

## CI mirror

The four tests are wired into `.github/workflows/test.yml` as the `cpp-host` job — same g++ command-lines as above, just on ubuntu's stock `g++` (>= 13). If a test fails locally and works in CI (or vice versa), check the mingw vs ubuntu C++ standard-library version: the CappyMessenger pump uses `<atomic>` ops that mingw / libstdc++ differ on around release-ordering.
