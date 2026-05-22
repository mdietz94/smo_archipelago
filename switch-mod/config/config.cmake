set(LINKFLAGS -nodefaultlibs)
set(LLDFLAGS --no-demangle --gc-sections)

set(OPTIMIZE_OPTIONS_DEBUG -O2 -gdwarf-4)
# Conservative codegen — -O3 + -ffast-math + -flto with LLVM 19 emits
# aggressive instruction sequences (vectorized atomics, SIMD math) that
# ARMeilleure (Ryujinx's JIT) may mistranslate, producing 0xC0000005
# faults during long-running gameplay. Stay at -O2 for now; re-evaluate
# once we have real-Switch parity datapoints across all hot paths.
set(OPTIMIZE_OPTIONS_RELEASE -O2 -fno-strict-aliasing)
set(WARN_OPTIONS -Werror=return-type -Wno-invalid-offsetof)

set(INCLUDES include)

set(ASM_OPTIONS "")
set(C_OPTIONS -ffunction-sections -fdata-sections)
set(CXX_OPTIONS "")
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED TRUE)

set(IS_32_BIT FALSE)
set(TARGET_IS_STATIC FALSE)
set(MODULE_NAME smo_archipelago)
set(TITLE_ID 0x0100000000010000)
# subsdk9 is the Atmosphère exefs slot SMO Archipelago mods land in.
set(MODULE_BINARY subsdk9)
set(SDK_PAST_1900 FALSE)
set(USE_SAIL TRUE)

set(TRAMPOLINE_POOL_SIZE 0x40)
set(BAKE_SYMBOLS FALSE)

# HeapSourceDynamic is essential — routes operator new / malloc / free to
# SMO's own allocator. Without this addon, std::vector::push_back /
# std::string growth would call musl malloc directly and NULL-deref on
# hk::os::Thread instances.
#
# Nvn + ImGui + DebugRenderer enable the on-Switch debug overlay
# (ui::ApDebugConsole). Kgamer77/SMOO-Plus-Hakkun ships the same set on
# the same LibHakkun lineage with no init-time issues — the key is the
# @sdk module entry in config/VersionList.sym (without it, sail mis-
# resolves nvnBootstrapLoader to a RedStar.nss stub). lib/imgui (Dear
# ImGui submodule) must be checked out for the ImGui addon to compile.
set(HAKKUN_ADDONS HeapSourceDynamic Nvn ImGui DebugRenderer)
