---
name: smo-host-tests
description: Build and run the SMO switch-mod C++ host tests (test_json, test_protocol) on Windows. Use when the user mentions "host tests", "test_json", "test_protocol", "switch-mod tests", or asks to run/build C++ tests for switch-mod/. Covers the msys2 mingw64 PATH dance (devkitPro does NOT ship a host compiler — devkitA64 is AArch64-only).
---

# Switch-mod host tests (C++)

The Switch mod ships small host-runnable tests for the JSON encoder + wire protocol. They run on the host (Windows) compiled with standalone msys2 mingw64 g++ — devkitPro doesn't ship a host compiler (devkitA64 is AArch64-only).

Memory: `project_host_test_compiler.md`.

## Compiler location

`C:\msys64\mingw64\bin\g++.exe`. The produced `.exe` needs the mingw runtime DLLs (`libstdc++-6.dll`, etc.) on PATH or it won't run.

## Build + run (from PowerShell)

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
```

Expected: both exes exit 0 with `PASS` lines per test case. As of 2026-05-17: 27 tests in `test_json` (encoder/LineBuffer/overflow/round-trip), all in `test_protocol` including `decode_checked_replay_truncates_past_cap`, `decode_field_overlong_string_truncates`, and the 7 added in M6 phase D.

## Cleanup

```pwsh
Remove-Item -Force test_json.exe, test_protocol.exe
```

## When to add a new test

- New wire-protocol message type or field → add to `test_protocol.cpp`.
- New JSON encoder feature → add to `test_json.cpp`.

Pattern from M6.1: any field that holds a string in the Switch wire-protocol must be a fixed `char[N]` (worker thread can NOT use std::string — libstdc++ allocator NULL-derefs). Memory: `project_libstdcpp_allocator_broken_in_subsdk9.md`. Tests should cover the truncation behavior at the N boundary.

## Why not CTest?

The switch-mod CMakeLists.txt cross-compiles to AArch64 via devkitA64. Adding `enable_testing()` would force host-runnable tests into the same cross-compile build dir, which doesn't work cleanly. The host tests are deliberately standalone: a one-line g++ invocation per test executable, no CMake involvement. If this changes (CI needs a single command), consider a separate `switch-mod/tests/host-tests/CMakeLists.txt` with no devkit toolchain.
