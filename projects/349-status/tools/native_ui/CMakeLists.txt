cmake_minimum_required(VERSION 3.16)
project(349_status_native_ui LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

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

add_executable(native_ui
    test_ui.c
    "${STATUS_MAIN}/deck.c"
    "${STATUS_MAIN}/deck_input.c"
    "${STATUS_MAIN}/fonts/status_text_20.c"
    "${STATUS_MAIN}/fonts/status_text_22.c"
    "${STATUS_MAIN}/fonts/status_clock_80.c")
target_include_directories(native_ui PRIVATE
    "${STATUS_MAIN}"
    "${CMAKE_CURRENT_SOURCE_DIR}/stubs"
    "${CJSON_INCLUDE_DIR}")
target_link_libraries(native_ui PRIVATE lvgl)
target_compile_definitions(native_ui PRIVATE _DEFAULT_SOURCE)
target_compile_options(native_ui PRIVATE -Wall -Wextra -Werror -UNDEBUG)
