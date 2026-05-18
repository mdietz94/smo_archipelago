---
name: smo-symbol-discovery
description: Discover, mangle, and verify SMO 1.0.0 NSO symbols for new switch-mod hook targets. Use when the user wants to add a new hook, asks about a "mangled symbol", "check_nso_symbols", "HookSymbols.hpp", "OdysseyDecomp", "lunakit symbols", or how to bind a function from `nn::ro::LookupSymbol`. Covers the lunakit-verified path, the OdysseyDecomp forward-decl path, mangling via aarch64-none-elf-g++, and offline verification against the real main.nso.
---

# Adding new hook targets (SMO 1.0.0 symbol discovery)

Pipeline: identify symbol in OdysseyDecomp / lunakit → produce mangled name → verify against real `main.nso` → add to `HookSymbols.hpp` + `scripts/check_nso_symbols.py`.

## Sources of symbol identities

8 symbols currently in `switch-mod/src/hooks/HookSymbols.hpp` (count is approximate — see file). Two ways they get there:

1. **Lunakit-verified hooks** (3 symbols, byte-identical): copy from `lunakit-vendor/src/program/main.cpp` `InstallAtSymbol(...)` call list. These are the canonical 1.0.0 source.
2. **OdysseyDecomp forward-decls** (5+ symbols, computed): for symbols lunakit doesn't hook, forward-declare the signature in a scratch `.cpp` from MonsterDruide1's [OdysseyDecomp](https://github.com/MonsterDruide1/OdysseyDecomp) (a 1.0.0 decompilation) and let GCC compute the mangle.

## Mangling via aarch64-none-elf-g++

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

Output gives e.g. `_ZN16GameDataFunction18getCurrentShineNumERK24GameDataHolderAccessor`. Itanium ABI mangling is deterministic from the signature, so forward-decls are sufficient — you don't need definitions.

## Verification against real main.nso

```pwsh
.\bridge\.venv\Scripts\python scripts\check_nso_symbols.py C:\Users\maxwe\Downloads\main.nso
```

Decompresses the NSO segments (LZ4 block) and grep's the `.dynstr` table for the mangled names in `HookSymbols.hpp` + the script's own constant list. Expect `HIT` for every symbol. If a symbol misses, common reasons:

- **Inlined in 1.0.0**: function exists in OdysseyDecomp but the 1.0.0 compiler inlined every call. Falls back to (a) hooking a higher-level public wrapper (e.g. `GameDataFunction::addPayShine` wraps the inlined `GameDataFile::addPayShine`), or (b) delta-polling the relevant field from `DrawMainHook` (one-frame latency, zero symbol dependency).
- **Signature mismatch**: a `const&` vs `*`, a return-type difference, an overload disambiguator. Re-check against the real declaration.
- **Namespace path wrong**: easy to misread OdysseyDecomp — verify the full nested namespace.

## main.nso location

User has SMO 1.0.0 NSP installed natively (no Atmosphere downgrade overlay). Local copies live at `C:\Users\maxwe\Downloads\SMO_1.0.0.nsp` and `C:\Users\maxwe\Downloads\main.nso` (15.4 MB extracted). **Never commit** — `.gitignore` covers `docs/main-*.nso` and the Downloads location is outside the repo.

## Adding to the project

After verification succeeds:

1. Add the mangled string constant to `switch-mod/src/hooks/HookSymbols.hpp`, grouped by milestone (see existing sections).
2. Add the same constant to `scripts/check_nso_symbols.py`'s symbol list so future verification passes catch any drift.
3. Resolve via `nn::ro::LookupSymbol` at module init (see existing patterns in `MoonGetHook.cpp` etc.), store as a function pointer.

## When forward-decl mangling fails

Some types are hard to forward-declare cleanly (multi-inheritance, template instantiations, sead-namespaced things). Fallback: copy the relevant minimal headers from OdysseyDecomp into a scratch dir alongside `scratch.cpp` and let g++ resolve them. Then strip the headers — only the mangled output matters.
