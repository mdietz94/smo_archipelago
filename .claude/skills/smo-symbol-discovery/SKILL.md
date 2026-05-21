---
name: smo-symbol-discovery
description: Discover, mangle, and verify SMO 1.0.0 NSO symbols for new switch-mod hook targets. Use when the user wants to add a new hook, asks about a "mangled symbol", "sail", "SmoApSymbols.sym", "HookSymbols.hpp", "OdysseyDecomp", or how to bind a function from `hk::ro::lookupSymbol` / install a trampoline via `HkTrampoline::installAtSym`. Covers the sail .sym workflow, the OdysseyDecomp forward-decl mangling path, fakesymbols.so verification via llvm-nm, and where vtable / data symbols differ from function symbols.
---

# Adding new hook targets (SMO 1.0.0 symbol discovery)

Pipeline: identify symbol in OdysseyDecomp → produce mangled name → add to `switch-mod/syms/<bucket>.sym` (sail's symbol database) → build → verify in `fakesymbols.so` via `llvm-nm --dynamic`. Sail patches the real address in at module load via the LibHakkun runtime — no `nn::ro::LookupSymbol` boilerplate needed for trampolines.

## Sources of symbol identities

The current symbol set is split across two layers:

1. **`switch-mod/syms/game/SmoApSymbols.sym`** (and `syms/nn/nifm.sym`) — the source of truth that sail reads at link time. One mangled name per line, organized by section header (`@smo:100` for SMO 1.0.0). Comments allowed via `//`.
2. **`switch-mod/src/hooks/HookSymbols.hpp`** — C++ string constants that mirror the `.sym` entries. Used by:
   - `HkTrampoline<...>::installAtSym<"...">()` call sites (the trampoline template argument must be the literal mangled name — no indirection).
   - `hk::ro::lookupSymbol(smoap::sym::kFoo)` calls for vtable / data lookups and stored function pointers (e.g. `addHackDictionary`).

Both files must stay in sync. If a name appears in `HookSymbols.hpp` but not in the `.sym` file, sail won't pre-resolve it and `hk::ro::lookupSymbol` returns 0 at runtime. If it appears in the `.sym` file but no code references it, the linker's `--gc-sections` drops the unused stub — harmless but noisy.

## Mangling via aarch64-none-elf-g++

The Itanium ABI mangling is deterministic from the signature, so forward-declarations are sufficient — you don't need definitions. We keep devkitA64 around just for `aarch64-none-elf-g++` + `aarch64-none-elf-nm`; the LLVM toolchain we now build with doesn't need it at runtime, but devkitA64 is the easiest source of cross-target g++ for mangling.

```pwsh
# scratch.cpp — forward-decl whatever you need
@'
namespace al { class LiveActor; class IUseMessageSystem; }
namespace GameDataFunction {
    const char16_t* tryFindShineMessage(const al::LiveActor*,
                                        const al::IUseMessageSystem*,
                                        int, int);
}
'@ | Out-File -Encoding ASCII scratch.cpp

& "C:/devkitPro/devkitA64/bin/aarch64-none-elf-g++.exe" -std=c++20 -c scratch.cpp -o scratch.o
& "C:/devkitPro/devkitA64/bin/aarch64-none-elf-nm.exe" scratch.o
```

Output gives e.g. `_ZN16GameDataFunction19tryFindShineMessageEPKN2al9LiveActorEPKNS0_17IUseMessageSystemEii`.

For vtables and other RTTI-related data symbols, use `_ZTV<class>` (vtable), `_ZTI<class>` (typeinfo), `_ZTS<class>` (typeinfo name). Class name uses the same length-prefixed format as nm output: e.g. `Poetter` → `_ZTV7Poetter`.

## The sail mangling-length trap

Sail emits a stub for whatever you write into the .sym file. The linker resolves the stub against `fakesymbols.so` (also sail-generated). Runtime lookup happens by **string match** against `main.nso`'s `.dynsym`. If a length-prefix byte count is off-by-one (e.g. `_ZN15StaffRollScene` for a 14-char "StaffRollScene"), the linker is happy, but `hk::ro::lookupSymbol` returns 0 at runtime because the typo'd name isn't in `main.nso`'s dynsym.

The trap is captured in [project_sail_mangling_length_trap.md](memory/project_sail_mangling_length_trap.md). Two ways to defend:

1. **Generate via `aarch64-none-elf-g++ + nm`** as above. Don't write a mangled name by hand.
2. **Count bytes** on every length prefix: in `_ZN16GameDataFunction19tryFindShineMessage…`, the `16` matches `len("GameDataFunction")` and the `19` matches `len("tryFindShineMessage")`. Off-by-one is the most common typo.

## Verification: llvm-nm against fakesymbols.so

After a clean build, sail emits `switch-mod/build/fakesymbols.so` — a synthetic ELF whose `.dynsym` is one stub per `.sym` entry. Verify every symbol made it through:

```pwsh
& "C:/Program Files/LLVM/bin/llvm-nm.exe" --dynamic switch-mod/build/fakesymbols.so | Select-String "_Z" | Measure-Object -Line
# Expected: at least as many lines as the count of mangled entries in your .sym files
```

To check one specific entry:

```pwsh
& "C:/Program Files/LLVM/bin/llvm-nm.exe" --dynamic switch-mod/build/fakesymbols.so | Select-String "tryFindShineMessage"
```

The runtime check is the actual smoke test, though — boot the build and watch for `lookupSymbol FAILED` lines in `smoap.log`. Those name the symbol that couldn't resolve and tell you which `.sym` entry is wrong.

## Common reasons a symbol misses

- **Inlined in 1.0.0**: function exists in OdysseyDecomp but the 1.0.0 compiler inlined every call. No symbol in `main.nso`. Fall back to (a) hooking a higher-level public wrapper (e.g. `GameDataFunction::addPayShine` wraps the inlined `GameDataFile::addPayShine`), or (b) delta-polling the relevant field from `DrawMainHook` (one-frame latency, zero symbol dependency).
- **Signature mismatch**: a `const&` vs `*`, a return-type difference, an overload disambiguator. Re-check against the real declaration.
- **Namespace path wrong**: easy to misread OdysseyDecomp — verify the full nested namespace.
- **Wrong build ID**: VersionList.sym pins SMO 1.0.0 = `3ca12dfaaf9c82da064d1698df79cda1`. A future 1.0.1+ target would need its own `@smo:101` block plus the per-version mangled names (offsets diverge; the symbol name might be byte-identical or might rename around inlining).

## Adding to the project

After mangling + verification succeeds:

1. **Add to `switch-mod/syms/game/SmoApSymbols.sym`** (or a new bucket under `syms/<area>/...sym` for non-game symbols — e.g. `syms/nn/nifm.sym` for nn::nifm). One mangled line per entry, with a short `//` comment above explaining the role.
2. **Add the C++ string constant to `switch-mod/src/hooks/HookSymbols.hpp`**, grouped by milestone (see existing sections).
3. **Use it** — either as a `HkTrampoline<...>::installAtSym<"<mangled>">()` template argument (trampoline target) or via `hk::ro::lookupSymbol(smoap::sym::kFoo)` (function pointer for direct calls, vtable address for `actorIsX()` filters).

## When forward-decl mangling fails

Some types are hard to forward-declare cleanly (multi-inheritance, template instantiations, sead-namespaced things). Fallback: copy the relevant minimal headers from OdysseyDecomp into a scratch dir alongside `scratch.cpp` and let g++ resolve them. Then strip the headers — only the mangled output matters.

In practice this only comes up for sead-template-heavy types. The 50+ symbols already in the project were all manglable from pure forward-decls.

## Historical: scripts/check_nso_symbols.py (retired)

Pre-cutover, the project had a `scripts/check_nso_symbols.py` that decompressed `main.nso`'s LZ4-packed segments and grepped its `.dynstr` for the names in `HookSymbols.hpp`. It was retired in the Hakkun cutover (2026-05-21) because the sail `.sym` → `llvm-nm --dynamic fakesymbols.so` flow above gives an equivalent check at link time. If a future version of `main.nso` (1.0.1+) ships and you want to run the same kind of pre-flight check against a non-1.0.0 build, the script's logic is preserved in git history at commit `4f19fca`.
