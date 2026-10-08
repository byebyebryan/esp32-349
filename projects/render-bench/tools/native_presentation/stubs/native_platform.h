#ifndef BENCH_NATIVE_PLATFORM_H
#define BENCH_NATIVE_PLATFORM_H

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include "lvgl.h"

#define ESP_OK 0
#define ESP_LOG_DEBUG 0
#define ESP_ERROR_CHECK(result) assert((result) == ESP_OK)

static inline int display_349_init(void) { return ESP_OK; }
static inline bool display_349_lock(int wait) { (void)wait; return true; }
static inline void display_349_unlock(void) {}
static inline void display_349_backlight(int level) { (void)level; }
static inline void esp_log_level_set(const char *tag, int level)
{ (void)tag; (void)level; }
static inline int64_t esp_timer_get_time(void)
{ return (int64_t)lv_tick_get() * 1000; }
static inline uint32_t ulTaskGetIdleRunTimeCounterForCore(int core)
{ (void)core; return 0; }

#endif
