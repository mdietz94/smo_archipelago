---
name: smo-host-tests
description: Build and run the SMO switch-mod C++ host tests (test_json, test_protocol) on Windows. Use when the user mentions "host tests", "test_json", "test_protocol", "switch-mod tests", or asks to run/build C++ tests for switch-mod/. Covers the msys2 mingw64 PATH dance (devkitPro / LLVM 19 cross compilers do NOT ship a Windows host runtime — both are AArch64-only).
---

# Switch-mod host tests (C++)

## Current status (post-Hakkun cutover, 2026-05-21)

**Dormant.** The host tests (`test_json.cpp`, `test_protocol.cpp`, `test_cappy_messenger.cpp`) lived under the exlaunch-era `switch-mod/tests/` and were not ported to the Hakkun source tree during PR #151. The cpp-host CI job is currently skipped via `if: false` in [.github/workflows/test.yml](.github/workflows/test.yml).

Restoration is a small follow-up: the tests only depend on `util/Json.cpp` and `ap/ApProtocol.cpp`, both of which were verbatim-ported from the old tree, so a `git restore --source=<pre-cutover-commit> -- switch-mod/tests/` followed by re-enabling the CI job should be sufficient. If you're tasked with that restoration, see the project's task tracker for the cutover-PR follow-up.

The rest of this skill describes how the tests work when present, so it's ready when they come back.

## Compiler location

`C:\msys64\mingw64\bin\g++.exe`. The Hakkun toolchain cross-compiles to AArch64 via LLVM 19 + libc++; neither the LLVM clang nor the (legacy) devkitA64 ship a Windows host runtime, so mingw64 g++ is what the host tests use. The produced `.exe` needs the mingw runtime DLLs (`libstdc++-6.dll`, etc.) on PATH or it won't run.

## Build + run (from PowerShell — requires tests to be present)

```pwsh
$env:Path = "C:\msys64\mingw64\bin;" + $env:Path

# test_json (JSON encoder, LineBuffer, overflow, round-trip)
& "C:\msys64\mingw64\bin\g++.exe" -std=c++20 -Wall -Wextra -O0 -g `
    switch-mod/tests/test_json.cpp switch-mod/src/util/Json.cpp `
    -Iswitch-mod/src -o test_json.exe
.\test_json.exe

# test_protocol (wire-format encode/decode round-trip)
& "C:\msys64\mingw64\bin\g++.exe" -std=c++20 -Wall -Wextra -O0 -g `
    switch-mod/tests/test_protocol.cpp switch-mod/src/ap/ApProtocol.cpp `
    switch-mod/src/util/Json.cpp -Iswitch-mod/src -o test_protocol.exe
.\test_protocol.exe

# test_cappy_messenger (SPSC ring + speech-bubble queueing)
& "C:\msys64\mingw64\bin\g++.exe" -std=c++20 -Wall -Wextra -O0 -g -DSMOAP_HOST_TEST `
    switch-mod/tests/test_cappy_messenger.cpp switch-mod/src/ui/CappyMessenger.cpp `
    -Iswitch-mod/src -o test_cappy_messenger.exe
.\test_cappy_messenger.exe
```

Expected: each exe exits 0 with `PASS` lines per test case. `test_json` covers encoder / LineBuffer / overflow / round-trip; `test_protocol` covers every wire-protocol message type with truncation + overlong-field edge cases; `test_cappy_messenger` covers the SPSC ring (`SMOAP_HOST_TEST` gates out the Hakkun-only includes).

## Cleanup

```pwsh
Remove-Item -Force test_json.exe, test_protocol.exe, test_cappy_messenger.exe
```

## When to add a new test

- New wire-protocol message type or field → add to `test_protocol.cpp`.
- New JSON encoder feature → add to `test_json.cpp`.
- New CappyMessenger queue path → add to `test_cappy_messenger.cpp`.

## Why not CTest?

The switch-mod `CMakeLists.txt` cross-compiles to AArch64 via the Hakkun toolchain (LLVM 19 + libc++). Adding `enable_testing()` would force host-runnable tests into the same cross-compile build dir, which doesn't work cleanly. The host tests are deliberately standalone: a one-line g++ invocation per test executable, no CMake involvement. If this changes (CI needs a single command), consider a separate `switch-mod/tests/host-tests/CMakeLists.txt` with no Hakkun toolchain.
