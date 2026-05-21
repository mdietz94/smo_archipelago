---
name: smo-symbol-discovery
description: Discover, mangle, and verify SMO 1.0.0 NSO symbols for new switch-mod hook targets. Use when the user wants to add a new hook, asks about a "mangled symbol", "sail .sym", "SmoApSymbols.sym", "fakesymbols.so", "OdysseyHeaders", "OdysseyDecomp", or how to bind a function via `hk::ro::lookupSymbol` / `installAtSym<>`. Covers the sail .sym workflow, mangling via aarch64-none-elf-g++ scratch builds, and offline verification with `llvm-nm --dynamic` against the sail-generated `fakesymbols.so`.
---

# Adding new hook targets (SMO 1.0.0 symbol discovery)

Pipeline: identify symbol in OdysseyHeaders / OdysseyDecomp / lunakit → produce mangled name → add it to **both** `switch-mod/src/hooks/HookSymbols.hpp` (the `inline constexpr` name string used at call sites) **and** `switch-mod/syms/game/SmoApSymbols.sym` (sail's link-side entry; or `switch-mod/syms/nn/<lib>.sym` for system libs) → rebuild → verify via `llvm-nm --dynamic switch-mod/build/fakesymbols.so`.

The two files are complementary, not redundant:
- `HookSymbols.hpp` defines `inline constexpr const char* kFoo = "_ZN..."` constants. Call sites use them as `hk::ro::lookupSymbol(kFoo)` or `installAtSym<kFoo>()`.
- `.sym` files tell sail to emit a stub for the same mangled name into `build/fakesymbols.so` so the link is happy; at runtime sail's `hk::ro::lookupSymbol` resolves the real address against SMO's dynsym.

If you add to one but not the other: HookSymbols.hpp-only fails at link time (undefined symbol against fakesymbols.so); .sym-only fails at compile time (HookSymbols.hpp constant missing). Sail catches the typical drift at build time.

Sail is LibHakkun's symbol DB / resolver. It reads `.sym` files at build time, emits `symboldb.o` + `fakesymbols.so` + `datablocks.o` into the build dir, and links them into the .nso. At module load, `hk::ro::lookupSymbol` patches the real addresses against SMO's dynsym. The hook framework's `installAtSym<"mangled_name">()` resolves through this same path.

## Sources of symbol identities

Three layers (cross-check between them when in doubt):

1. **OdysseyHeaders** (`switch-mod/lib/OdysseyHeaders/`) — vendored headers from MonsterDruide1, declarative signatures for the public SMO 1.0.0 surface. Include from C++ TUs to get strong typing on hook signatures. Always preferred over OdysseyDecomp for forward-decls since it's already in your include path.
2. **OdysseyDecomp** ([MonsterDruide1/OdysseyDecomp](https://github.com/MonsterDruide1/OdysseyDecomp)) — full 1.0.0 decompilation, useful for understanding internal call paths or when OdysseyHeaders lacks a declaration. Read-only reference; never vendored.
3. **Lunakit reference** (no longer in tree; was in `lunakit-vendor/` pre-Hakkun) — for hook targets that lunakit binds against SMO 1.0.0, their installer call list is byte-identical canonical truth. Cross-check against the lunakit upstream repo at github.com/Amethyst-szs/smo-lunakit when verifying a high-risk symbol.

## Mangling via aarch64-none-elf-g++

The Itanium ABI mangling is deterministic from the signature, so a forward-decl scratch compile is sufficient — you don't need definitions:

```pwsh
# scratch.cpp — forward-decl whatever you need
echo @"
namespace al { class LiveActor; }
namespace GameDataFunction {
    int getCurrentShineNum(GameDataHolderAccessor const&);
}
"@ | Out-File -Encoding ASCII scratch.cpp

& "C:/devkitPro/devkitA64/bin/aarch64-none-elf-g++.exe" -std=c++20 -c scratch.cpp -o scratch.o
& "C:/devkitPro/devkitA64/bin/aarch64-none-elf-nm.exe" scratch.o
```

devkitA64 isn't a build dependency anymore (the Hakkun toolchain is LLVM 19), but the aarch64-none-elf cross-binutils it ships are still the cleanest source for an Itanium-ABI `nm` that matches what SMO 1.0.0's compiler emitted. If devkitA64 isn't installed, the same effect comes from any Itanium-ABI g++ — e.g. msys2's `mingw-w64-clang-aarch64-clang` or a Linux container.

Output gives e.g. `_ZN16GameDataFunction18getCurrentShineNumERK24GameDataHolderAccessor`. Copy that exact string into the `.sym` file.

**⚠️ Length-prefix trap.** Hand-writing a `.sym` entry and miscounting the bytes in a name-length prefix (e.g. typing `_ZN14StaffRollScene` for the 15-char `StaffRollScene`) links cleanly against `fakesymbols.so` *and then* fails the real subsdk link with `undefined symbol`, or — worse — links silently and runtime-misresolves to nothing. Always copy from `aarch64-none-elf-nm` output verbatim; never hand-edit a length prefix. See [project_sail_mangling_length_trap.md](C:\Users\maxwe\.claude\projects\C--Users-maxwe-Documents-smo-archipelago\memory\project_sail_mangling_length_trap.md).

## Where new symbols go

- `switch-mod/syms/game/SmoApSymbols.sym` — SMO functions. Group by milestone / hook family with comments above each block (see existing layout).
- `switch-mod/syms/nn/<lib>.sym` — Nintendo system library functions. Currently only `nifm.sym`; add new files for additional `nn::*` modules (e.g. `nn/socket.sym` if SMO's socket init needs explicit binding).
- The build picks up every `.sym` under `switch-mod/syms/` automatically (CMake glob).

`.sym` format is one entry per line:

```
_Z<mangled_name> = 0x<offset>  # optional offset for BAKE_SYMBOLS=TRUE; omit for runtime resolve
```

Our build uses `BAKE_SYMBOLS=FALSE` (see `switch-mod/config/config.cmake`) — names are stored as strings and resolved at module load via `hk::ro::lookupSymbol`. Don't add offset values; just the names.

## Verification against the build's fakesymbols.so

Sail emits `switch-mod/build/fakesymbols.so` during configure — a Windows-host-runnable stub library that contains every symbol you've added. `llvm-nm --dynamic` lists them, and the count is your sanity check:

```pwsh
& "C:\Program Files\LLVM\bin\llvm-nm.exe" --dynamic `
    C:\Users\maxwe\Documents\smo_archipelago\switch-mod\build\fakesymbols.so | `
    Select-String "_Z" | Measure-Object -Line
```

Expected: at least the count of entries in your `.sym` files. If a symbol you added doesn't appear in `llvm-nm --dynamic`, sail rejected the line (mangling syntax error, duplicate, etc.) — re-check the line and the build output.

## Verification against real main.nso

When you suspect a symbol exists in your `.sym` file but is NOT exported by SMO 1.0.0 (e.g. inlined, internal-only, or a typo'd mangle):

```pwsh
& "C:\Program Files\LLVM\bin\llvm-nm.exe" --dynamic --defined-only path\to\main.nso | `
    Select-String _Z<your_mangled_name_substring>
```

`main.nso` is **not** retained locally between sessions — it's copyrighted and `.gitignore`'d (`docs/main-*.nso`). If you need it, re-extract from a local SMO 1.0.0 NSP (or XCI) dump:

```pwsh
python scripts\extract_shine_map.py --nsp <SMO 1.0.0 NSP>
```

The script's hactool flow drops the decrypted NCAs into `.romfs-cache/`; `main.nso` is the executable inside the program NCA. **Never commit** the result.

Common reasons a symbol misses against `main.nso`:

- **Inlined in 1.0.0**: function exists in OdysseyDecomp but the 1.0.0 compiler inlined every call. Falls back to (a) hooking a higher-level public wrapper (e.g. `GameDataFunction::addPayShine` wraps the inlined `GameDataFile::addPayShine`), or (b) delta-polling the relevant field from a `drawMain` hook (one-frame latency, zero symbol dependency).
- **Signature mismatch**: a `const&` vs `*`, a return-type difference, an overload disambiguator. Re-check against the real declaration.
- **Namespace path wrong**: easy to misread OdysseyDecomp / OdysseyHeaders — verify the full nested namespace.

## Adding to the project (full flow)

After verification:

1. Add the mangled string to the appropriate `.sym` file. One line, just the name.
2. Rebuild via `python scripts/build_switchmod.py`. Sail re-runs as part of CMake configure.
3. Verify `llvm-nm --dynamic build/fakesymbols.so` shows the new entry.
4. Write your hook. Use `HkTrampoline<Ret, Args...>::installAtSym<"mangled_name">()` for trampoline hooks, or `hk::ro::lookupSymbol` if you just need the address.
5. Verify at runtime via the Ryujinx log — module-load resolution failures land in `[rtld]` lines.

## When forward-decl mangling fails

Some types are hard to forward-declare cleanly (multi-inheritance, template instantiations, sead-namespaced things). Fallbacks in priority order:

1. **Use OdysseyHeaders includes** in your scratch.cpp — `#include <Library/.../Foo.h>` from the vendored submodule pulls in the proper declarations. The build's include path already has `switch-mod/lib/OdysseyHeaders/include` configured, so the same `#include` line works in real hook code.
2. **Copy minimal OdysseyDecomp headers** into a scratch dir alongside `scratch.cpp` and let g++ resolve them. Strip after mangling completes; only the mangled output matters.
3. **Multi-inheritance offset gotcha**: when a function takes `IUseSceneObjHolder*` and you pass an `al::Scene*` (which multiply-inherits), the C++ compiler inserts the offset for you in source — but the mangled symbol expects the offset-adjusted pointer. Pass the cast result, not the raw scene pointer. This bit phase 3b for 19 bisect cycles; the post-#151 fix lives in `ApState.cpp`'s `rs::tryShowCapMessagePriorityLow` call site (commit `4ff5864`).
