cmake_minimum_required(VERSION 3.16)

set(CMAKE_TOOLCHAIN_FILE "${CMAKE_SOURCE_DIR}/sys/cmake/toolchain.cmake")
project(smo_archipelago VERSION 1.0 LANGUAGES ASM C CXX)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

include(config/config.cmake)

# ---------------------------------------------------------------------------
# Version + bridge defines. The strings
# land verbatim in HELLO + log lines so the bridge can correlate sessions.
# Override per-machine at configure time:
#   cmake -DBRIDGE_HOST=192.168.x.x ...
# ---------------------------------------------------------------------------
set(SMO_VERSION            "1.0.0"         CACHE STRING "Target SMO version")
set(SMO_AP_MOD_VERSION     "0.1.0"         CACHE STRING "Module version baked into HELLO")
set(BRIDGE_HOST            "192.168.1.187" CACHE STRING "PC LAN IP for the bridge")
set(BRIDGE_PORT            17777           CACHE STRING "Bridge TCP port")
set(BRIDGE_RETRY_MS        3000            CACHE STRING "Reconnect backoff seed (ms)")
set(BRIDGE_RECV_TIMEOUT_MS 200             CACHE STRING "Per-iteration recv timeout (ms)")
set(DISCOVERY_PORT         17776           CACHE STRING "UDP discovery probe port (Switch -> bridge; bridge -> Switch reply)")

add_compile_definitions(
    SMO_VERSION_STRING="${SMO_VERSION}"
    SMO_AP_MOD_VERSION_STRING="${SMO_AP_MOD_VERSION}"
    BRIDGE_HOST_STRING="${BRIDGE_HOST}"
    BRIDGE_PORT_VALUE=${BRIDGE_PORT}
    BRIDGE_RETRY_MS_VALUE=${BRIDGE_RETRY_MS}
    BRIDGE_RECV_TIMEOUT_MS_VALUE=${BRIDGE_RECV_TIMEOUT_MS}
    DISCOVERY_PORT_VALUE=${DISCOVERY_PORT}
)

file(GLOB_RECURSE SOURCES_ASM ${PROJECT_SOURCE_DIR}/src/*.S)
file(GLOB_RECURSE SOURCES_C ${PROJECT_SOURCE_DIR}/src/*.c)
file(GLOB_RECURSE SOURCES_CXX ${PROJECT_SOURCE_DIR}/src/*.cpp)
set(SOURCES ${SOURCES_ASM} ${SOURCES_C} ${SOURCES_CXX})
add_executable(${PROJECT_NAME} ${SOURCES})

# OdysseyHeaders provides SMO's class layouts (sead, al, agl, game, nn, nvn,
# vapours, etc.). Linking the INTERFACE target propagates the include paths.
add_subdirectory(lib/OdysseyHeaders)
target_link_libraries(${PROJECT_NAME} PRIVATE OdysseyHeaders)

include(sys/cmake/module.cmake)
include(sys/cmake/deploy.cmake)
include(sys/cmake/sail.cmake)
usesail(${PROJECT_NAME})
