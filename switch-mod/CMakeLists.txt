# Spicy Meatball Overdrive — Switch module.
#
# Builds an exlaunch subsdk9 ELF that ships as:
#   sd:/atmosphere/contents/0100000000010000/exefs/subsdk9
#
# Toolchain + helpers come from the lunakit-vendor submodule (a vendored
# copy of LunaKit's working CMake setup, which already contains a working
# exlaunch port with the InstallAtSymbol patch we depend on).
#
# Usage:
#   cmake -S . -B build -G Ninja \
#         -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake
#   cmake --build build
#   cmake --install build   # populates sd-overlay/

cmake_minimum_required(VERSION 3.21)
project(smo_archipelago CXX C ASM)

if (NOT SWITCH)
    message(FATAL_ERROR
        "Not targeting Switch. Pass -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake")
endif ()

set(CMAKE_C_STANDARD 17)
set(CMAKE_CXX_STANDARD 23)

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
set(TITLE_ID "0100000000010000" CACHE STRING "SMO Atmosphere title ID (hex, no 0x)")
set(CONFIG_TITLE_ID "0x${TITLE_ID}")
set(SMO_VERSION "1.0.0" CACHE STRING "Target SMO version")
set(SMO_AP_MOD_VERSION "0.1.0" CACHE STRING "Module version baked into HELLO")

# ---------------------------------------------------------------------------
# ap_config.json defaults — baked into the file via configure_file at build
# time. Override per-machine: -DBRIDGE_HOST=192.168.x.x at cmake configure.
# ---------------------------------------------------------------------------
set(BRIDGE_HOST            "192.168.1.187" CACHE STRING "PC LAN IP for the bridge")
set(BRIDGE_PORT            17777           CACHE STRING "Bridge TCP port")
set(BRIDGE_RETRY_MS        3000            CACHE STRING "Reconnect backoff seed (ms)")
set(BRIDGE_RECV_TIMEOUT_MS 200             CACHE STRING "Per-iteration recv timeout (ms)")
set(BRIDGE_LOG_LEVEL       "info"          CACHE STRING "Module log level")

# -DSMOAP_DEBUG_SD_LOG=ON  enables a one-shot dump of the first ~5 seconds
# of SMOAP_LOG_* output to sd:/smo_ap.txt (frame 300 of drawMain). Useful
# for debugging boot-time issues when neither lm nor svcOutputDebugString
# is visible on the user's setup. OFF by default — costs an 8 KiB ring
# buffer + nn::fs link deps when on.
option(SMOAP_DEBUG_SD_LOG "Dump SMOAP_LOG_* output to sd:/smo_ap.txt at ~5s" OFF)

# ---------------------------------------------------------------------------
# exlaunch / module compile defs (mirrors lunakit's defines)
# ---------------------------------------------------------------------------
add_compile_definitions(
    EXL_PROGRAM_ID=${CONFIG_TITLE_ID}
    EXL_LOAD_KIND=Module
    EXL_LOAD_KIND_ENUM=EXL_LOAD_KIND_MODULE
    NNSDK=1
    SMO_VERSION_STRING="${SMO_VERSION}"
    SMO_AP_MOD_VERSION_STRING="${SMO_AP_MOD_VERSION}"
    SMO_TITLE_ID_STRING="${TITLE_ID}"
    BRIDGE_HOST_STRING="${BRIDGE_HOST}"
    BRIDGE_PORT_VALUE=${BRIDGE_PORT}
    BRIDGE_RETRY_MS_VALUE=${BRIDGE_RETRY_MS}
    BRIDGE_RECV_TIMEOUT_MS_VALUE=${BRIDGE_RECV_TIMEOUT_MS}
)

if (SMOAP_DEBUG_SD_LOG)
    add_compile_definitions(SMOAP_DEBUG_SD_LOG)
endif ()

# ---------------------------------------------------------------------------
# Vendored module templates (NPDM + linker spec + linker script)
# ---------------------------------------------------------------------------
set(LUNAKIT "${CMAKE_SOURCE_DIR}/lunakit-vendor")

configure_file(${LUNAKIT}/module/subsdk9.json.template
               ${CMAKE_CURRENT_BINARY_DIR}/subsdk9.json)

# Bake bridge defaults into ap_config.json from the template.
configure_file(${CMAKE_SOURCE_DIR}/romfs/ap_config.json.in
               ${CMAKE_CURRENT_BINARY_DIR}/ap_config.json
               @ONLY)

# subsdk9.specs.template references @SUBSDK_LD_PATH@. Apply lunakit's MINGW
# path fixup so the linker accepts the path on Windows.
set(SUBSDK_LD_PATH ${LUNAKIT}/module/subsdk9.ld)
execute_process(COMMAND uname OUTPUT_VARIABLE uname OUTPUT_STRIP_TRAILING_WHITESPACE)
if (uname MATCHES "^MINGW")
    string(REGEX REPLACE "^/([a-zA-Z])/" "\\1:/" SUBSDK_LD_PATH "${SUBSDK_LD_PATH}")
endif ()
configure_file(${LUNAKIT}/module/subsdk9.specs.template
               ${CMAKE_CURRENT_BINARY_DIR}/subsdk9.specs)

# ---------------------------------------------------------------------------
# Include paths
# ---------------------------------------------------------------------------
include_directories(
    ${CMAKE_SOURCE_DIR}/src                 # our headers
    ${LUNAKIT}/src                          # lib.hpp, nn.hpp, common.hpp, helpers.h, types.h
    ${LUNAKIT}/src/lib                      # exlaunch core headers
    ${LUNAKIT}/src/nn                       # nn:: headers
    ${LUNAKIT}/src/nvn                      # Nintendo NVN graphics SDK (declarations only — transitively
                                            # pulled in by some lunakit headers we touch. We do not draw
                                            # to NVN; in-game text is owned by SMO via CappyMessenger.)
    ${LUNAKIT}/src/sead                     # sead headers
    ${LUNAKIT}/src/program                  # setting.hpp (used by lunakit lib code)
)

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

# Our module sources.
file(GLOB_RECURSE OUR_SOURCES_CXX  CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/src/*.cpp ${CMAKE_SOURCE_DIR}/src/*.c)
file(GLOB_RECURSE OUR_SOURCES_ASM  CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/src/*.s)
file(GLOB_RECURSE OUR_HEADERS      CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/src/*.h ${CMAKE_SOURCE_DIR}/src/*.hpp)

# Curated subset of vendored lunakit sources. We deliberately exclude
# lunakit's program/ (its own main.cpp and ImGui integration), devgui/,
# imgui_backend/, helpers/, devworlds/, glslc/, nvn/, nvnTool/ — none of
# which we need for M3 and most of which would conflict with our module.
file(GLOB_RECURSE LIB_SOURCES_CXX    CONFIGURE_DEPENDS ${LUNAKIT}/src/lib/*.cpp ${LUNAKIT}/src/lib/*.c)
file(GLOB_RECURSE LIB_SOURCES_ASM    CONFIGURE_DEPENDS ${LUNAKIT}/src/lib/*.s)
file(GLOB_RECURSE NN_SOURCES_CXX     CONFIGURE_DEPENDS ${LUNAKIT}/src/nn/*.cpp ${LUNAKIT}/src/nn/*.c)
file(GLOB_RECURSE RS_SOURCES_CXX     CONFIGURE_DEPENDS ${LUNAKIT}/src/rs/*.cpp ${LUNAKIT}/src/rs/*.c)
# logger/ deliberately excluded — LoadLogger.hpp pulls in imgui.h which we
# don't link against in M3. We have our own util/Log.cpp.

# ---------------------------------------------------------------------------
# Switch tooling helpers (find devkitPro tools + nso/npdm builders)
# ---------------------------------------------------------------------------
include(${LUNAKIT}/cmake/SwitchTools.cmake)

# ---------------------------------------------------------------------------
# subsdk9 target
# ---------------------------------------------------------------------------
# Distinguish the linker's ELF output from the post-processed NSO. The NSO
# (extension-less "subsdk9") is what Atmosphere consumes; the ELF is the
# intermediate. Without this they would collide.
set(CMAKE_EXECUTABLE_SUFFIX ".elf")

add_executable(subsdk9
    ${OUR_SOURCES_CXX}
    ${OUR_SOURCES_ASM}
    ${OUR_HEADERS}
    ${LIB_SOURCES_CXX}
    ${LIB_SOURCES_ASM}
    ${NN_SOURCES_CXX}
    ${RS_SOURCES_CXX}
)

set_target_properties(subsdk9 PROPERTIES
    CONFIG_JSON "${CMAKE_CURRENT_BINARY_DIR}/subsdk9.json"
)

# Lunakit's vendored exlaunch sources have a const-correctness pattern in
# typed_storage.hpp that newer GCC 15 libstdc++ rejects (std::construct_at
# refuses const T*). Granting -fpermissive locally on those files keeps us
# away from patching the vendored tree, while keeping our own code under
# strict checks.
set_source_files_properties(
    ${LIB_SOURCES_CXX}
    ${NN_SOURCES_CXX}
    ${RS_SOURCES_CXX}
    ${OUR_SOURCES_CXX}  # our code transitively includes lib headers, same issue
    PROPERTIES COMPILE_FLAGS "-fpermissive -Wno-deprecated-declarations"
)

# Build NSO + NPDM. add_nso_target_subsdk sets LINK_FLAGS to libnx's
# switch.specs, but for an exlaunch subsdk we must override that to point at
# our custom subsdk9.specs (which uses subsdk9.ld and exlaunch's init symbol).
# Order matters: this set_target_properties must come AFTER add_nso_target_subsdk.
add_nso_target_subsdk(subsdk9)
__generate_npdm(subsdk9)
set_target_properties(subsdk9 PROPERTIES
    LINK_FLAGS "-specs=${CMAKE_CURRENT_BINARY_DIR}/subsdk9.specs -g3 ${ARCH} -nostartfiles -nostdlib"
)

add_custom_target(subsdk9_meta ALL DEPENDS create_npdm subsdk9_nso)

# ---------------------------------------------------------------------------
# Ryujinx auto-deploy (preferred dev loop — boot emulator before Switch)
# ---------------------------------------------------------------------------
# Pass -DRYU_PATH=<dir> at configure time pointing at the Ryujinx user-data
# directory (typically %APPDATA%/Ryujinx). The post-build hook copies
# subsdk9 + main.npdm + ap_config.json into the per-title mods dir Ryujinx
# scans on game launch. Also seeds the emulated SD card with the same
# romfs/ap_config.json so the runtime nn::fs::OpenFile path resolves.
set(RYU_PATH "" CACHE PATH "Ryujinx user-data dir (e.g. C:/Users/<you>/AppData/Roaming/Ryujinx). Empty = skip Ryujinx deploy")
if (RYU_PATH)
    set(RYU_MOD_DIR  "${RYU_PATH}/mods/contents/${TITLE_ID}/smo-archipelago")
    set(RYU_SDCARD   "${RYU_PATH}/sdcard/atmosphere/contents/${TITLE_ID}")
    add_custom_command(TARGET subsdk9_meta POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${RYU_MOD_DIR}/exefs"
        COMMAND ${CMAKE_COMMAND} -E make_directory "${RYU_MOD_DIR}/romfs"
        COMMAND ${CMAKE_COMMAND} -E make_directory "${RYU_SDCARD}"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_BINARY_DIR}/subsdk9"      "${RYU_MOD_DIR}/exefs/subsdk9"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_BINARY_DIR}/main.npdm"    "${RYU_MOD_DIR}/exefs/main.npdm"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_BINARY_DIR}/ap_config.json" "${RYU_MOD_DIR}/romfs/ap_config.json"
        COMMENT "Deploying subsdk9+npdm+config to Ryujinx mods + sdcard")
endif ()

# ---------------------------------------------------------------------------
# Install -> sd-overlay/ for direct copy to the SD card
# ---------------------------------------------------------------------------
set(SD_TARGET "${CMAKE_SOURCE_DIR}/sd-overlay/atmosphere/contents/${TITLE_ID}")

install(FILES "${CMAKE_CURRENT_BINARY_DIR}/subsdk9"
        DESTINATION "${SD_TARGET}/exefs")
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/main.npdm"
        DESTINATION "${SD_TARGET}/exefs")
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/ap_config.json"
        DESTINATION "${SD_TARGET}/romfs")
