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

## Three traps to avoid when writing `.sym` entries by hand

Sail emits a stub for whatever you write into the .sym file. The linker resolves the stub against `fakesymbols.so` (also sail-generated). Runtime lookup happens by **string match** against `main.nso`'s `.dynsym`. Three landmines, all of which sail and the linker happily ignore:

1. **Length-prefix off-by-one.** Itanium ABI mangling is `_ZN<len><name><len><name>...Ev`. `aarch64-none-elf-nm` computes the lengths automatically; hand-written entries do not. Concrete misses: `SubmitNetworkRequestAndWait` is 27 chars (not 28), `IsNetworkAvailable` is 18 (not 19). Build links clean against the typo'd `fakesymbols.so`; runtime `hk::ro::lookupSymbol` silently returns 0 because the typo'd name isn't in `main.nso`. Defense: mangle via `aarch64-none-elf-g++ + nm`, never write by hand.

2. **`s64`/`u64` mangle as `l`/`m` on aarch64 LP64**, NOT `x`/`y`. Nintendo's SDK headers typedef them to `long`/`unsigned long`, so e.g. `nn::fs::CreateFile(char const*, s64)` mangles to `_ZN2nn2fs10CreateFileEPKcl` — not `…EPKcx`. If you write a scratch file with `using s64 = long long;` to feed g++, mangling silently produces the wrong (`x`/`y`) form and the symbol won't resolve at runtime. Defense: use `using s64 = long;`/`u64 = unsigned long;` (or `<cstdint>` + `int64_t`/`uint64_t`) in mangling scratch files. When in doubt, register both manglings via a `resolveAlt(primary, alt)` helper — see Log.cpp.

3. **Sail load-time crash on a missing symbol.** When sail's `loadSymbols()` runs at subsdk init, it applies every `.sym` entry kept alive by the linker (i.e. referenced by code that survives `--gc-sections`). If the underlying symbol isn't in the real `main.nso` dynsym, `hk::sail::detail::SymbolEntry::apply` aborts the entire module before `hkMain` runs. Atmosphere crash signature: User Break, faulting frames `__module_entry__` → `loadSymbols` → `lookupSymbolFromDb` → `SymbolEntry::apply`. The `llvm-nm --dynamic fakesymbols.so` check below is necessary but NOT sufficient (fakesymbols.so is sail-generated from your `.sym`, so it always finds the name there). **Real verification** is against the actual `main.nso`: extract via the `smo-extract-data` skill, then `llvm-nm --dynamic main.nso | grep <mangled>`. For optional functionality whose symbol might not exist on retail (debug helpers, `nn::fs::*` SD-card calls), prefer runtime `hk::ro::lookupSymbol(mangled)` + nullptr check + function-pointer cast — that path soft-fails per-call instead of aborting init. Caveat: `hk::ro::lookupSymbol` itself has been observed to abort on retail when other module-state corruption is present (see [memory/project_hk_ro_lookup_unsafe_on_retail.md](../../../.claude/projects/C--Users-maxwe-Documents-smo-archipelago/memory/project_hk_ro_lookup_unsafe_on_retail.md)).

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

The runtime check is the actual smoke test, though — boot the build and watch for `lookupSymbol FAILED` lines in Ryujinx's log (`%APPDATA%\Ryujinx\Logs\Ryujinx_*.log`) or, if the bridge has connected, in `<Archipelago>/logs/SMOClient.txt`. Those name the symbol that couldn't resolve and tell you which `.sym` entry is wrong.

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

In practice this only comes up for sead-template-heavy types. The 40+ symbols already in the project were all manglable from pure forward-decls.
