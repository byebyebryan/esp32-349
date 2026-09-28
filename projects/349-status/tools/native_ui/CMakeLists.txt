cmake_minimum_required(VERSION 3.16)
project(349_status_native_ui LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

find_package(PkgConfig REQUIRED)
pkg_check_modules(SDL2 REQUIRED IMPORTED_TARGET sdl2)

set(LV_BUILD_CONF_PATH "${CMAKE_CURRENT_SOURCE_DIR}/lv_conf.h" CACHE PATH "" FORCE)
set(CONFIG_LV_BUILD_DEMOS OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG_INTERNAL OFF CACHE BOOL "" FORCE)

add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../../managed_components/lvgl__lvgl"
                 "${CMAKE_CURRENT_BINARY_DIR}/lvgl")

set(STATUS_MAIN "${CMAKE_CURRENT_SOURCE_DIR}/../../main")
set(CJSON_INCLUDE_DEFAULT "")
if(DEFINED ENV{IDF_PATH} AND NOT "$ENV{IDF_PATH}" STREQUAL "")
    set(CJSON_INCLUDE_DEFAULT "$ENV{IDF_PATH}/components/json/cJSON")
endif()
set(CJSON_INCLUDE_DIR "${CJSON_INCLUDE_DEFAULT}" CACHE PATH
    "ESP-IDF cJSON headers used by the state contract")
if(NOT EXISTS "${CJSON_INCLUDE_DIR}/cJSON.h")
    message(FATAL_ERROR
        "cJSON.h not found under CJSON_INCLUDE_DIR='${CJSON_INCLUDE_DIR}'. "
        "Set -DCJSON_INCLUDE_DIR=<esp-idf>/components/json/cJSON.")
endif()
if(NOT EXISTS "${CJSON_INCLUDE_DIR}/cJSON.c")
    message(FATAL_ERROR "cJSON.c not found under CJSON_INCLUDE_DIR='${CJSON_INCLUDE_DIR}'.")
endif()

add_library(native_cjson STATIC "${CJSON_INCLUDE_DIR}/cJSON.c")
target_include_directories(native_cjson PUBLIC "${CJSON_INCLUDE_DIR}")
if(UNIX)
    target_link_libraries(native_cjson PUBLIC m)
endif()

set(NATIVE_FIXTURE_SOURCES
    "${STATUS_MAIN}/deck.c"
    "${STATUS_MAIN}/deck_input.c"
    "${STATUS_MAIN}/group_input.c"
    "${STATUS_MAIN}/ui_fonts.c"
    "${STATUS_MAIN}/fonts/status_symbol_14.c"
    "${STATUS_MAIN}/fonts/status_symbol_16.c"
    "${STATUS_MAIN}/fonts/status_text_20.c"
    "${STATUS_MAIN}/fonts/status_text_22.c"
    "${STATUS_MAIN}/fonts/status_clock_80.c")

function(add_native_fixture target fixture_source)
    add_executable(${target} ${fixture_source} ${NATIVE_FIXTURE_SOURCES})
    target_include_directories(${target} PRIVATE
        "${STATUS_MAIN}"
        "${CMAKE_CURRENT_SOURCE_DIR}/stubs"
        "${CJSON_INCLUDE_DIR}")
    target_link_libraries(${target} PRIVATE lvgl native_cjson PkgConfig::SDL2)
    target_compile_definitions(${target} PRIVATE _DEFAULT_SOURCE)
    target_compile_options(${target} PRIVATE -Wall -Wextra -Werror -UNDEBUG)
endfunction()

add_native_fixture(native_ui test_ui.c)
add_native_fixture(native_groups test_groups.c)

add_executable(native_protocol
    ../native_protocol/runner.c
    ../native_protocol/stubs/native_platform.c
    "${STATUS_MAIN}/state.c"
    "${STATUS_MAIN}/proto.c"
    "${STATUS_MAIN}/dashboard.c")
target_include_directories(native_protocol PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../native_protocol/stubs"
    "${CMAKE_CURRENT_SOURCE_DIR}/../native_protocol"
    "${STATUS_MAIN}"
    "${CJSON_INCLUDE_DIR}")
target_link_libraries(native_protocol PRIVATE native_cjson m)
target_compile_definitions(native_protocol PRIVATE _DEFAULT_SOURCE)
target_compile_options(native_protocol PRIVATE -Wall -Wextra -Werror -UNDEBUG)

add_native_fixture(native_ui_composed test_composed.c)
target_include_directories(native_ui_composed BEFORE PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../native_protocol/stubs"
    "${CMAKE_CURRENT_SOURCE_DIR}/../native_protocol")
target_compile_definitions(native_ui_composed PRIVATE NATIVE_PROTOCOL _DEFAULT_SOURCE)
target_sources(native_ui_composed PRIVATE
    ../native_protocol/stubs/native_platform.c
    "${STATUS_MAIN}/state.c"
    "${STATUS_MAIN}/proto.c"
    "${STATUS_MAIN}/dashboard.c")

enable_testing()
add_test(NAME native_ui_regression
    COMMAND native_ui --self-test --artifacts
            "${CMAKE_CURRENT_BINARY_DIR}/artifacts/regression")
add_test(NAME native_ui_replay_smoke
    COMMAND native_ui --replay "${CMAKE_CURRENT_SOURCE_DIR}/replay_smoke.json"
            --artifacts "${CMAKE_CURRENT_BINARY_DIR}/artifacts/replay")
add_test(NAME native_ui_grouped
    COMMAND native_groups --artifacts
            "${CMAKE_CURRENT_BINARY_DIR}/artifacts/grouped")
add_test(NAME native_ui_viewer_smoke
    COMMAND native_ui --viewer --viewer-duration-ms 80 --capture viewer-smoke
            --artifacts "${CMAKE_CURRENT_BINARY_DIR}/artifacts/viewer")
set_tests_properties(native_ui_viewer_smoke PROPERTIES
    ENVIRONMENT "SDL_VIDEODRIVER=dummy")
add_test(NAME native_protocol_contract COMMAND native_protocol --self-test)
add_test(NAME native_composed_replay_smoke
    COMMAND "${CMAKE_COMMAND}"
            -DRUNNER=$<TARGET_FILE:native_ui_composed>
            -DARTIFACTS=${CMAKE_CURRENT_BINARY_DIR}/artifacts/composed
            -DINPUT=${CMAKE_CURRENT_SOURCE_DIR}/composed_smoke.jsonl
            -P "${CMAKE_CURRENT_SOURCE_DIR}/run_composed.cmake")

find_program(UV_EXECUTABLE uv)
if(UV_EXECUTABLE)
    add_test(NAME host_composed
        COMMAND "${UV_EXECUTABLE}" run --project
                "${CMAKE_CURRENT_SOURCE_DIR}/../../host" --frozen python
                "${CMAKE_CURRENT_SOURCE_DIR}/../test_grouped_composed.py"
                --native $<TARGET_FILE:native_ui_composed>
                --artifacts "${CMAKE_CURRENT_BINARY_DIR}/artifacts/host-composed")
    add_test(NAME smoke_recorder
        COMMAND "${UV_EXECUTABLE}" run --project
                "${CMAKE_CURRENT_SOURCE_DIR}/../../host" --frozen python
                "${CMAKE_CURRENT_SOURCE_DIR}/../run_grouped_smoke.py"
                --self-test --native $<TARGET_FILE:native_ui_composed>
                --artifacts "${CMAKE_CURRENT_BINARY_DIR}/artifacts/smoke-recorder")
    add_test(NAME host_actions_composed
        COMMAND "${UV_EXECUTABLE}" run --project
                "${CMAKE_CURRENT_SOURCE_DIR}/../../host" --frozen python
                "${CMAKE_CURRENT_SOURCE_DIR}/../test_notification_actions_composed.py"
                --native $<TARGET_FILE:native_ui_composed>
                --artifacts "${CMAKE_CURRENT_BINARY_DIR}/artifacts/actions-composed")
else()
    add_test(NAME host_composed
        COMMAND "${CMAKE_COMMAND}" -E echo "SKIP: uv is unavailable")
    set_tests_properties(host_composed PROPERTIES
        SKIP_REGULAR_EXPRESSION "SKIP:")
    add_test(NAME smoke_recorder
        COMMAND "${CMAKE_COMMAND}" -E echo "SKIP: uv is unavailable")
    set_tests_properties(smoke_recorder PROPERTIES SKIP_REGULAR_EXPRESSION "SKIP:")
    add_test(NAME host_actions_composed
        COMMAND "${CMAKE_COMMAND}" -E echo "SKIP: uv is unavailable")
    set_tests_properties(host_actions_composed PROPERTIES SKIP_REGULAR_EXPRESSION "SKIP:")
endif()
