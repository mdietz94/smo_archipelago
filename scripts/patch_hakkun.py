#!/usr/bin/env python3
"""Apply Windows-port patches to the pinned LibHakkun submodule.

The spike at third_party/hakkun-spike (gitignored) discovered six source-level
patches needed to build LibHakkun + sail on Windows + msys2. Each patch is
idempotent (uses a sentinel check before applying). On first run, all six
land; subsequent runs report 'already applied' and exit cleanly.

These patches should be upstreamed to fruityloops1/LibHakkun. While upstream
PRs are in flight, this script reapplies them locally after submodule init.
If a PR review stalls > 1 week, the migration plan calls for forking
LibHakkun to mdietz94/LibHakkun-smo and re-pinning the submodule — at which
point this script becomes obsolete.

Patches applied:
  1. sys/sail/CMakeLists.txt — drop hardcoded clang/clang++ compiler.
  2. sys/sail/src/main.cpp — std::filesystem::path::c_str() is wchar_t* on Windows.
  3. sys/sail/src/fakelib.cpp — quote clangBinary path in popen cmdline.
  4. sys/cmake/sail.cmake — expand sys/addons/*/syms glob (cmd.exe doesn't).
  5. sys/cmake/generate_exefs.cmake — prefix elf2nso.py with `python`.
  6. (env only) Copy sys/sail/build/sail.exe → sys/sail/build/sail (no ext).
     Handled by scripts/build_switchmod.py.

  7. (correctness) include/hk/hook/Trampoline.h + src/hk/hook/Trampoline.cpp:
     Add AArch64 PC-relative prologue relocation to TrampolineHook. Upstream
     copies the first instruction verbatim into the trampoline pool (TODO at
     Trampoline.h:67 says "Relocate instruction, or at least abort if
     instruction needs to be relocated"); when the original is
     adrp/adr/b/bl/b.cond/cbz/cbnz/tbz/tbnz, calling .orig() executes the
     instruction at the wrong PC and the guest crashes — observed in
     Ryujinx ARMeilleure 0xC0000005 on SMO 1.0.0 stage load. Patch expands
     TrampolineBackup to 8 slots and emits movz/movk + indirect/direct
     branch sequences as needed. Worth upstreaming to fruityloops1/LibHakkun.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAKKUN = os.path.join(REPO_ROOT, "switch-mod", "sys")


def patch_file(path: str, old: str, new: str, sentinel: str) -> str:
    """Apply a literal-string patch. Idempotent via sentinel check.

    Returns 'applied', 'already-applied', or 'missing'.
    """
    if not os.path.exists(path):
        return "missing"
    content = open(path, encoding="utf-8").read()
    if sentinel in content:
        return "already-applied"
    if old not in content:
        # The expected old text isn't present and the sentinel isn't either.
        # Either upstream has moved (need to revisit this patch) or we're
        # already mid-migration to a fork. Fail loud.
        sys.exit(f"[patch_hakkun] '{path}': old text not found and sentinel absent; upstream likely changed")
    new_content = content.replace(old, new, 1)
    open(path, "w", encoding="utf-8", newline="\n").write(new_content)
    return "applied"


def report(name: str, result: str) -> None:
    print(f"  [{result:>15}] {name}")


def main() -> int:
    if not os.path.isdir(HAKKUN):
        sys.exit(f"[patch_hakkun] {HAKKUN} not found — `git submodule update --init` first")

    print(f"[patch_hakkun] applying Windows-port patches to {HAKKUN}")

    # Patch 1: drop hardcoded compiler in sys/sail/CMakeLists.txt.
    # These set() lines come AFTER project() so they do nothing useful (compiler
    # already detected), but their values DO get baked into ninja rules, which
    # is what breaks the host build with our env-var-supplied gcc.
    report(
        "sail CMakeLists.txt clang/clang++ removal",
        patch_file(
            os.path.join(HAKKUN, "sail", "CMakeLists.txt"),
            "set(CMAKE_C_COMPILER clang)\nset(CMAKE_CXX_COMPILER clang++)\nset(CMAKE_CXX_STANDARD 23)",
            "set(CMAKE_CXX_STANDARD 23)",
            sentinel="# SMO_HAKKUN_PATCH_1",
        ),
    )
    _maybe_add_sentinel(
        os.path.join(HAKKUN, "sail", "CMakeLists.txt"),
        "set(CMAKE_CXX_STANDARD 23)",
        "# SMO_HAKKUN_PATCH_1: removed hardcoded clang/clang++ — host build uses CC/CXX env vars\n",
    )

    report(
        "sail main.cpp filesystem::path wchar_t fix",
        patch_file(
            os.path.join(HAKKUN, "sail", "src", "main.cpp"),
            "            const char* path = entry.path().c_str();",
            "            std::string path_str = entry.path().string();  // SMO_HAKKUN_PATCH_2: Windows wchar_t fix\n            const char* path = path_str.c_str();",
            sentinel="SMO_HAKKUN_PATCH_2",
        ),
    )

    report(
        "sail fakelib.cpp clang path quoting",
        patch_file(
            os.path.join(HAKKUN, "sail", "src", "fakelib.cpp"),
            "    static void compile(const char* outPath, const char* clangBinary, const char* language, const std::string& source, const std::string& flags, const char* filename) {\n        std::string cmd = clangBinary;",
            "    static void compile(const char* outPath, const char* clangBinary, const char* language, const std::string& source, const std::string& flags, const char* filename) {\n        // SMO_HAKKUN_PATCH_3: quote clangBinary for Windows paths with spaces.\n        std::string cmd;\n        cmd.push_back('\"');\n        cmd.append(clangBinary);\n        cmd.push_back('\"');",
            sentinel="SMO_HAKKUN_PATCH_3",
        ),
    )

    report(
        "sail.cmake addons glob expansion",
        patch_file(
            os.path.join(HAKKUN, "cmake", "sail.cmake"),
            "        if (ADDONS_SYMS_EMPTY_TEST)\n            set(SAIL_CMD ${SAIL_CMD} ${CMAKE_CURRENT_SOURCE_DIR}/sys/addons/*/syms)\n        endif()",
            "        if (ADDONS_SYMS_EMPTY_TEST)\n            # SMO_HAKKUN_PATCH_4: expand glob ourselves (cmd.exe doesn't).\n            file(GLOB ADDONS_SYM_DIRS LIST_DIRECTORIES TRUE ${CMAKE_CURRENT_SOURCE_DIR}/sys/addons/*/syms)\n            foreach (d IN LISTS ADDONS_SYM_DIRS)\n                if (IS_DIRECTORY ${d})\n                    set(SAIL_CMD ${SAIL_CMD} ${d})\n                endif()\n            endforeach()\n        endif()",
            sentinel="SMO_HAKKUN_PATCH_4",
        ),
    )

    report(
        "generate_exefs.cmake python prefix",
        patch_file(
            os.path.join(HAKKUN, "cmake", "generate_exefs.cmake"),
            "            COMMAND ${PROJECT_SOURCE_DIR}/sys/tools/elf2nso.py ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}${CMAKE_EXECUTABLE_SUFFIX}.baked ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}.nso -c",
            "            # SMO_HAKKUN_PATCH_5: explicit python invocation.\n            COMMAND python ${PROJECT_SOURCE_DIR}/sys/tools/elf2nso.py ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}${CMAKE_EXECUTABLE_SUFFIX}.baked ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}.nso -c",
            sentinel="SMO_HAKKUN_PATCH_5",
        ),
    )
    report(
        "generate_exefs.cmake python prefix (non-baked)",
        patch_file(
            os.path.join(HAKKUN, "cmake", "generate_exefs.cmake"),
            "            COMMAND ${PROJECT_SOURCE_DIR}/sys/tools/elf2nso.py ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}${CMAKE_EXECUTABLE_SUFFIX} ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}.nso -c",
            "            # SMO_HAKKUN_PATCH_5b: explicit python invocation (non-baked path).\n            COMMAND python ${PROJECT_SOURCE_DIR}/sys/tools/elf2nso.py ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}${CMAKE_EXECUTABLE_SUFFIX} ${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}.nso -c",
            sentinel="SMO_HAKKUN_PATCH_5b",
        ),
    )

    # ------------------------------------------------------------------
    # Patch 7: AArch64 PC-relative prologue relocation in TrampolineHook.
    # ------------------------------------------------------------------
    # Three coordinated edits across two files:
    #   7a. Trampoline.h — expand TrampolineBackup struct (2 slots -> 8).
    #   7b. Trampoline.h — replace the "copy origInstr + makeB + clearCache"
    #       block inside installAtOffset with a call to the new relocator.
    #   7c. Trampoline.cpp — append the relocator implementation.
    # Sentinel: SMO_HAKKUN_PATCH_7 inside each modified location.

    report(
        "Trampoline.h TrampolineBackup struct expansion",
        patch_file(
            os.path.join(HAKKUN, "hakkun", "include", "hk", "hook", "Trampoline.h"),
            "        struct TrampolineBackup {\n"
            "            Instr origInstr;\n"
            "            Instr bRetInstr;\n"
            "\n"
            "            ptr getRx() const;\n"
            "        };",
            "        // SMO_HAKKUN_PATCH_7a: expanded backup so the AArch64 prologue\n"
            "        // relocator below can emit up to 8 instructions (movz + 3 movks +\n"
            "        // ldr-with-base + b orig+4 is the worst case for ldr-literal).\n"
            "        // For non-PC-relative prologues only instrs[0] (the verbatim\n"
            "        // original) and instrs[1] (b orig+4) are used; the remaining\n"
            "        // slots are nop-padded.\n"
            "        //\n"
            "        // Each entry is page-aligned (0x1000 bytes per entry, ~256 KiB for\n"
            "        // 64-entry pool) so that nested trampolines (e.g. SaveLoadHook's\n"
            "        // GameDataFile::initializeData calling MoonGetHook's setGotShine\n"
            "        // calling ScenarioFlagHook's setMainScenarioNo) never share an\n"
            "        // ARMeilleure JIT translation block with their callee's pool\n"
            "        // slot. Without this, recursive `.orig()` traffic across\n"
            "        // contiguous 32-byte slots in the same 4 KiB page reproducibly\n"
            "        // crashes the Ryujinx JIT (0xC0000005 in ARMeilleure.Translation\n"
            "        // .Translator.Execute) ~50s into gameplay on SMO 1.0.0.\n"
            "        struct alignas(0x1000) TrampolineBackup {\n"
            "            static constexpr int cMaxSlots = 8;\n"
            "            Instr instrs[cMaxSlots];\n"
            "\n"
            "            ptr getRx() const;\n"
            "        };\n"
            "\n"
            "        // Decode `orig` (the instruction at function-entry PC `orig_pc`)\n"
            "        // and emit equivalent code into backup->instrs[] so that executing\n"
            "        // from backup->getRx() has the same architectural effect, then\n"
            "        // (unless the original was an unconditional B-to-target) append a\n"
            "        // b orig_pc+4 so control resumes inside the hooked function.\n"
            "        void installRelocatedPrologue(TrampolineBackup* backup,\n"
            "                                       Instr orig, ptr orig_pc);",
            sentinel="SMO_HAKKUN_PATCH_7a",
        ),
    )

    # 7b — replace install body that did verbatim copy + makeB.
    # The original block runs after sTrampolinePool.allocate() and consists of
    # the assignment, the gap check + makeB, and the clearCache. All three are
    # subsumed by installRelocatedPrologue().
    report(
        "Trampoline.h installAtOffset reloc-aware body",
        patch_file(
            os.path.join(HAKKUN, "hakkun", "include", "hk", "hook", "Trampoline.h"),
            "            mBackup = detail::sTrampolinePool.allocate();\n"
            "            HK_ABORT_UNLESS(mBackup != nullptr, \"TrampolinePool full! Current size: 0x%x\", HK_HOOK_TRAMPOLINE_POOL_SIZE);\n"
            "            mBackup->origInstr = mOrigInstr; // TODO: Relocate instruction, or at least abort if instruction needs to be relocated\n"
            "\n"
            "            const ptr from = mBackup->getRx() + sizeof(Instr), to = getAt() + sizeof(Instr);\n"
            "            const s64 gap = to - from;\n"
            "            HK_ABORT_UNLESS(abs(gap) <= cMaxBranchDistance, \"Trampoline: Branch exceeded max branch distance (%zd > %zu)\", abs(gap), cMaxBranchDistance);\n"
            "\n"
            "            mBackup->bRetInstr = makeB(from, to);\n"
            "            svc::clearCache(mBackup->getRx(), sizeof(detail::TrampolineBackup));",
            "            mBackup = detail::sTrampolinePool.allocate();\n"
            "            HK_ABORT_UNLESS(mBackup != nullptr, \"TrampolinePool full! Current size: 0x%x\", HK_HOOK_TRAMPOLINE_POOL_SIZE);\n"
            "            // SMO_HAKKUN_PATCH_7b: decode + relocate the original prologue\n"
            "            // instead of copying it verbatim. Handles adrp/adr/b/bl/b.cond/\n"
            "            // cbz/cbnz/tbz/tbnz; falls back to verbatim copy for plain\n"
            "            // non-PC-relative instructions (stp/sub/etc.). Also handles\n"
            "            // the back-branch to orig+4 and the icache flush.\n"
            "            detail::installRelocatedPrologue(mBackup, mOrigInstr, getAt());",
            sentinel="SMO_HAKKUN_PATCH_7b",
        ),
    )

    # 7c — append the relocator implementation to Trampoline.cpp.
    report(
        "Trampoline.cpp installRelocatedPrologue impl",
        patch_file(
            os.path.join(HAKKUN, "hakkun", "src", "hk", "hook", "Trampoline.cpp"),
            "        ptr TrampolineBackup::getRx() const {\n"
            "            ptr rw = ptr(this);\n"
            "\n"
            "            return ptr(sTrampolinePoolData) + (rw - sRwAddr);\n"
            "        }\n"
            "\n"
            "    } // namespace detail\n"
            "\n"
            "} // namespace hk::hook",
            "        ptr TrampolineBackup::getRx() const {\n"
            "            ptr rw = ptr(this);\n"
            "\n"
            "            return ptr(sTrampolinePoolData) + (rw - sRwAddr);\n"
            "        }\n"
            "\n"
            "        // SMO_HAKKUN_PATCH_7c: AArch64 prologue relocator.\n"
            "        //\n"
            "        // Upstream TrampolineHook copies the original first instruction\n"
            "        // verbatim into the trampoline pool. That's correct only for non\n"
            "        // PC-relative instructions; for adrp/adr/b/bl/conditional branches\n"
            "        // /ldr-literal, executing the same encoding at a different PC\n"
            "        // computes wrong addresses and the guest crashes. This relocator\n"
            "        // decodes the original and emits equivalent code that produces the\n"
            "        // same architectural effect when executed from the trampoline pool.\n"
            "        //\n"
            "        // X16 (IP0) is used as scratch for indirect-branch sequences. Per\n"
            "        // AAPCS64, IP0 is volatile across function entries, so clobbering\n"
            "        // it on the way into the hooked function is safe.\n"
            "        namespace {\n"
            "            constexpr Instr cNopInstr = 0xd503201fu;\n"
            "            constexpr bool isAdrp(Instr i)  { return (i & 0x9f000000u) == 0x90000000u; }\n"
            "            constexpr bool isAdr(Instr i)   { return (i & 0x9f000000u) == 0x10000000u; }\n"
            "            constexpr bool isB(Instr i)     { return (i & 0xfc000000u) == 0x14000000u; }\n"
            "            constexpr bool isBL(Instr i)    { return (i & 0xfc000000u) == 0x94000000u; }\n"
            "            constexpr bool isBCond(Instr i) { return (i & 0xff000010u) == 0x54000000u; }\n"
            "            constexpr bool isCbzCbnz(Instr i){ return (i & 0x7e000000u) == 0x34000000u; }\n"
            "            constexpr bool isTbzTbnz(Instr i){ return (i & 0x7e000000u) == 0x36000000u; }\n"
            "\n"
            "            constexpr int reg5(Instr i) { return int(i & 0x1fu); }\n"
            "\n"
            "            constexpr s64 sext(u64 v, int bits) {\n"
            "                u64 m = u64(1) << (bits - 1);\n"
            "                return s64((v ^ m) - m);\n"
            "            }\n"
            "            constexpr s64 adrpTarget(Instr i, ptr pc) {\n"
            "                u64 immlo = (i >> 29) & 0x3u;\n"
            "                u64 immhi = (i >> 5) & 0x7ffffu;\n"
            "                s64 imm = sext((immhi << 2) | immlo, 21);\n"
            "                return s64(pc & ~u64(0xfff)) + (imm << 12);\n"
            "            }\n"
            "            constexpr s64 adrTarget(Instr i, ptr pc) {\n"
            "                u64 immlo = (i >> 29) & 0x3u;\n"
            "                u64 immhi = (i >> 5) & 0x7ffffu;\n"
            "                return s64(pc) + sext((immhi << 2) | immlo, 21);\n"
            "            }\n"
            "            constexpr s64 b26Target(Instr i, ptr pc) {\n"
            "                return s64(pc) + (sext(i & 0x3ffffffu, 26) << 2);\n"
            "            }\n"
            "            constexpr s64 b19Target(Instr i, ptr pc) {\n"
            "                return s64(pc) + (sext((i >> 5) & 0x7ffffu, 19) << 2);\n"
            "            }\n"
            "            constexpr s64 tb14Target(Instr i, ptr pc) {\n"
            "                return s64(pc) + (sext((i >> 5) & 0x3fffu, 14) << 2);\n"
            "            }\n"
            "\n"
            "            constexpr Instr makeMovz64(int Xd, u16 imm16, int hw) {\n"
            "                return 0xd2800000u | (u32(hw) << 21) | (u32(imm16) << 5) | u32(Xd);\n"
            "            }\n"
            "            constexpr Instr makeMovk64(int Xd, u16 imm16, int hw) {\n"
            "                return 0xf2800000u | (u32(hw) << 21) | (u32(imm16) << 5) | u32(Xd);\n"
            "            }\n"
            "            constexpr Instr makeBr(int Xn) { return 0xd61f0000u | (u32(Xn) << 5); }\n"
            "            constexpr Instr makeBlr(int Xn) { return 0xd63f0000u | (u32(Xn) << 5); }\n"
            "            constexpr Instr makeB_imm26(s64 disp) {\n"
            "                return 0x14000000u | (u32((disp >> 2) & 0x3ffffff));\n"
            "            }\n"
            "            constexpr Instr makeBL_imm26(s64 disp) {\n"
            "                return 0x94000000u | (u32((disp >> 2) & 0x3ffffff));\n"
            "            }\n"
            "            constexpr Instr makeBCond_imm19(int cond, s64 disp) {\n"
            "                return 0x54000000u | (u32((disp >> 2) & 0x7ffff) << 5) | u32(cond & 0xf);\n"
            "            }\n"
            "            constexpr Instr makeCb_imm19(bool nz, bool sf, int rt, s64 disp) {\n"
            "                return (sf ? 0x80000000u : 0u) | 0x34000000u | (nz ? 0x01000000u : 0u)\n"
            "                       | (u32((disp >> 2) & 0x7ffff) << 5) | u32(rt);\n"
            "            }\n"
            "            constexpr Instr makeTb_imm14(bool nz, int bit, int rt, s64 disp) {\n"
            "                int b5 = (bit >> 5) & 1, b40 = bit & 0x1f;\n"
            "                return (b5 ? 0x80000000u : 0u) | 0x36000000u | (nz ? 0x01000000u : 0u)\n"
            "                       | (u32(b40) << 19) | (u32((disp >> 2) & 0x3fff) << 5) | u32(rt);\n"
            "            }\n"
            "\n"
            "            // Emit movz Xtemp, lo16; movk ..., LSL 16; movk ..., LSL 32; movk ..., LSL 48.\n"
            "            // Always 4 instructions, fully populates Xtemp with `addr`.\n"
            "            inline void emitMov64(Instr* out, int Xtemp, u64 addr) {\n"
            "                out[0] = makeMovz64(Xtemp, u16(addr & 0xffff), 0);\n"
            "                out[1] = makeMovk64(Xtemp, u16((addr >> 16) & 0xffff), 1);\n"
            "                out[2] = makeMovk64(Xtemp, u16((addr >> 32) & 0xffff), 2);\n"
            "                out[3] = makeMovk64(Xtemp, u16((addr >> 48) & 0xffff), 3);\n"
            "            }\n"
            "\n"
            "            // Conditional dispatch core: emit\n"
            "            //   <inverted-cond> +24    ; skip the 5-instr indirect long jump if NOT taken\n"
            "            //   movz/movk X16, target  ; 4 instrs\n"
            "            //   br X16                 ; 1 instr\n"
            "            // After the 6 instrs, control falls through to b orig+4 (caller appended).\n"
            "            // Returns the number of slots written (always 6).\n"
            "            inline int emitConditionalLongJump(Instr* out, Instr invertedSkip,\n"
            "                                                u64 target) {\n"
            "                out[0] = invertedSkip;       // jumps to out[6] when NOT taken\n"
            "                emitMov64(&out[1], 16, target);\n"
            "                out[5] = makeBr(16);\n"
            "                return 6;\n"
            "            }\n"
            "        }\n"
            "\n"
            "        // 26-bit imm range: ±128 MiB. Pre-shift, encoded value is bits 25..0.\n"
            "        static constexpr s64 cBr26Min = -(s64(1) << 27);\n"
            "        static constexpr s64 cBr26Max =  (s64(1) << 27) - 1;\n"
            "\n"
            "        // SMC-friendly diagnostic: log the instruction class chosen for each\n"
            "        // trampoline install so we can verify the relocator covered every\n"
            "        // hook target. Routed through OutputDebugString directly (not\n"
            "        // SMOAP_LOG_*) so this code stays a pure LibHakkun patch.\n"
            "        static void logRelocClass(Instr orig, ptr orig_pc, const char* cls) {\n"
            "            char buf[80];\n"
            "            int n = 0;\n"
            "            const char prefix[] = \"[hk-reloc] @\";\n"
            "            for (size_t i = 0; i < sizeof(prefix) - 1; ++i) buf[n++] = prefix[i];\n"
            "            for (int s = 60; s >= 0; s -= 4) {\n"
            "                u32 d = u32((orig_pc >> s) & 0xfu);\n"
            "                buf[n++] = char(d < 10 ? '0' + d : 'a' + (d - 10));\n"
            "            }\n"
            "            buf[n++] = ' '; buf[n++] = 'o'; buf[n++] = 'p'; buf[n++] = '=';\n"
            "            for (int s = 28; s >= 0; s -= 4) {\n"
            "                u32 d = u32((orig >> s) & 0xfu);\n"
            "                buf[n++] = char(d < 10 ? '0' + d : 'a' + (d - 10));\n"
            "            }\n"
            "            buf[n++] = ' ';\n"
            "            for (int i = 0; cls[i] && n < int(sizeof(buf)) - 2; ++i) buf[n++] = cls[i];\n"
            "            buf[n++] = '\\n';\n"
            "            buf[n] = '\\0';\n"
            "            svc::OutputDebugString(buf, size(n));\n"
            "        }\n"
            "\n"
            "        void installRelocatedPrologue(TrampolineBackup* backup, Instr orig,\n"
            "                                       ptr orig_pc) {\n"
            "            // Initialize all slots to NOP. If the relocator writes fewer than\n"
            "            // cMaxSlots instructions, the unused tail executes as nop on the\n"
            "            // (defensive) path where control reaches it. The back-branch we\n"
            "            // emit will normally skip the tail entirely.\n"
            "            for (int i = 0; i < TrampolineBackup::cMaxSlots; ++i)\n"
            "                backup->instrs[i] = cNopInstr;\n"
            "\n"
            "            const ptr trampoline_pc = backup->getRx();\n"
            "            int n = 0;\n"
            "            bool needsFallthrough = true;\n"
            "\n"
            "            auto emitLongIndirect = [&](u64 target, bool isCall) {\n"
            "                emitMov64(&backup->instrs[n], 16, target);\n"
            "                n += 4;\n"
            "                backup->instrs[n++] = isCall ? makeBlr(16) : makeBr(16);\n"
            "            };\n"
            "\n"
            "            if (isAdrp(orig) || isAdr(orig)) {\n"
            "                logRelocClass(orig, orig_pc, isAdrp(orig) ? \"adrp\" : \"adr\");\n"
            "                // adrp Xd, page (or adr Xd, addr) -> load computed addr into Xd via 4 movz/movk.\n"
            "                u64 tgt = u64(isAdrp(orig) ? adrpTarget(orig, orig_pc)\n"
            "                                            : adrTarget(orig, orig_pc));\n"
            "                emitMov64(&backup->instrs[n], reg5(orig), tgt);\n"
            "                n += 4;\n"
            "            } else if (isB(orig) || isBL(orig)) {\n"
            "                logRelocClass(orig, orig_pc, isBL(orig) ? \"bl\" : \"b\");\n"
            "                u64 tgt = u64(b26Target(orig, orig_pc));\n"
            "                s64 disp = s64(tgt) - s64(trampoline_pc + ptr(n) * sizeof(Instr));\n"
            "                if (disp >= cBr26Min && disp <= cBr26Max) {\n"
            "                    backup->instrs[n++] = isBL(orig) ? makeBL_imm26(disp)\n"
            "                                                      : makeB_imm26(disp);\n"
            "                } else {\n"
            "                    emitLongIndirect(tgt, isBL(orig));\n"
            "                }\n"
            "                if (isB(orig)) needsFallthrough = false;  // unconditional, never falls through\n"
            "            } else if (isBCond(orig)) {\n"
            "                logRelocClass(orig, orig_pc, \"b.cond\");\n"
            "                u64 tgt = u64(b19Target(orig, orig_pc));\n"
            "                int cond = int(orig & 0xfu);\n"
            "                // Invert cond bit 0 to skip the 5-instr long jump when the original would NOT take.\n"
            "                Instr skip = makeBCond_imm19(cond ^ 1, 6 * sizeof(Instr));\n"
            "                n += emitConditionalLongJump(&backup->instrs[n], skip, tgt);\n"
            "            } else if (isCbzCbnz(orig)) {\n"
            "                logRelocClass(orig, orig_pc, ((orig >> 24) & 1u) ? \"cbnz\" : \"cbz\");\n"
            "                u64 tgt = u64(b19Target(orig, orig_pc));\n"
            "                bool sf = ((orig >> 31) & 1u) != 0;\n"
            "                bool nz = ((orig >> 24) & 1u) != 0;  // 1=CBNZ, 0=CBZ\n"
            "                int rt = reg5(orig);\n"
            "                // Invert: original CBZ -> emit CBNZ skip, and vice versa.\n"
            "                Instr skip = makeCb_imm19(!nz, sf, rt, 6 * sizeof(Instr));\n"
            "                n += emitConditionalLongJump(&backup->instrs[n], skip, tgt);\n"
            "            } else if (isTbzTbnz(orig)) {\n"
            "                logRelocClass(orig, orig_pc, ((orig >> 24) & 1u) ? \"tbnz\" : \"tbz\");\n"
            "                u64 tgt = u64(tb14Target(orig, orig_pc));\n"
            "                bool nz = ((orig >> 24) & 1u) != 0;  // 1=TBNZ, 0=TBZ\n"
            "                int b5 = int((orig >> 31) & 1u);\n"
            "                int b40 = int((orig >> 19) & 0x1fu);\n"
            "                int rt = reg5(orig);\n"
            "                Instr skip = makeTb_imm14(!nz, (b5 << 5) | b40, rt, 6 * sizeof(Instr));\n"
            "                n += emitConditionalLongJump(&backup->instrs[n], skip, tgt);\n"
            "            } else {\n"
            "                // Non-PC-relative (or unhandled PC-relative): copy verbatim.\n"
            "                // The log line below tells us if any hook's first instruction\n"
            "                // is one we don't recognize — likely an LDR <reg>, literal.\n"
            "                logRelocClass(orig, orig_pc, \"verbatim\");\n"
            "                backup->instrs[n++] = orig;\n"
            "            }\n"
            "\n"
            "            if (needsFallthrough) {\n"
            "                const ptr from = trampoline_pc + ptr(n) * sizeof(Instr);\n"
            "                const ptr to = orig_pc + sizeof(Instr);\n"
            "                const s64 gap = s64(to) - s64(from);\n"
            "                HK_ABORT_UNLESS(abs(gap) <= cMaxBranchDistance,\n"
            "                    \"Trampoline: back-branch exceeded max distance (%zd > %zu)\",\n"
            "                    abs(gap), cMaxBranchDistance);\n"
            "                backup->instrs[n++] = makeB(from, to);\n"
            "            }\n"
            "\n"
            "            svc::clearCache(trampoline_pc, sizeof(TrampolineBackup));\n"
            "        }\n"
            "\n"
            "    } // namespace detail\n"
            "\n"
            "} // namespace hk::hook",
            sentinel="SMO_HAKKUN_PATCH_7c",
        ),
    )

    print("[patch_hakkun] done")
    return 0


def _maybe_add_sentinel(path: str, after_line: str, sentinel: str) -> None:
    """Insert a sentinel comment after a given line so future re-runs detect 'already applied'."""
    if not os.path.exists(path):
        return
    content = open(path, encoding="utf-8").read()
    if sentinel.strip() in content:
        return
    if after_line not in content:
        return
    new_content = content.replace(after_line, after_line + "\n" + sentinel.rstrip() + "\n", 1)
    open(path, "w", encoding="utf-8", newline="\n").write(new_content)


if __name__ == "__main__":
    sys.exit(main())
