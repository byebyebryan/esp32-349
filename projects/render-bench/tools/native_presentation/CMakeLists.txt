cmake_minimum_required(VERSION 3.16)
project(render_bench_presentation LANGUAGES C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(LVGL_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../managed_components/lvgl__lvgl"
    CACHE PATH "Locked project LVGL source")
set(LV_BUILD_CONF_PATH "${CMAKE_CURRENT_SOURCE_DIR}/lv_conf.h" CACHE PATH "" FORCE)
set(CONFIG_LV_BUILD_DEMOS OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG_INTERNAL OFF CACHE BOOL "" FORCE)
add_subdirectory("${LVGL_SOURCE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/lvgl")

add_executable(render_bench_presentation runner.c)
target_include_directories(render_bench_presentation PRIVATE stubs)
target_link_libraries(render_bench_presentation PRIVATE lvgl m)
target_compile_options(render_bench_presentation PRIVATE
    -Wall -Wextra -Werror -Wno-unused-parameter -UNDEBUG)
