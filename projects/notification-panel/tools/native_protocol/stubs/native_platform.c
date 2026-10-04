#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/semphr.h"
#include "rtc.h"

void *heap_caps_calloc(size_t count, size_t size, unsigned int capabilities)
{
    (void)capabilities;
    return calloc(count, size);
}

void heap_caps_free(void *memory)
{
    free(memory);
}

SemaphoreHandle_t xSemaphoreCreateMutex(void)
{
    return (void *)1;
}

int xSemaphoreTake(SemaphoreHandle_t semaphore, unsigned int timeout)
{
    (void)semaphore;
    (void)timeout;
    return 1;
}

int xSemaphoreGive(SemaphoreHandle_t semaphore)
{
    (void)semaphore;
    return 1;
}

static const esp_app_desc_t s_app_description = { .version = "native-test" };

const esp_app_desc_t *esp_app_get_description(void)
{
    return &s_app_description;
}

void esp_app_get_elf_sha256(char *dst, int length)
{
    if (length > 0) {
        strncpy(dst, "native-test", (size_t)length);
        dst[length - 1] = '\0';
    }
}

uint32_t esp_random(void)
{
    return 0x3492026u;
}

static bool s_rtc_valid;
static int64_t s_rtc_epoch;
static int s_rtc_offset;
static int64_t s_rtc_set_us;
static int s_rtc_set_count;

esp_err_t rtc_pcf_set(int64_t epoch_utc, int offset_sec)
{
    if (!rtc_clock_valid(epoch_utc, offset_sec)) {
        return ESP_ERR_INVALID_ARG;
    }
    const int64_t local_seconds = epoch_utc + (int64_t)offset_sec;
    const time_t local = (time_t)local_seconds;
    struct tm broken_down;
    if ((int64_t)local != local_seconds || gmtime_r(&local, &broken_down) == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_rtc_epoch = epoch_utc;
    s_rtc_offset = offset_sec;
    s_rtc_set_us = esp_timer_get_time();
    s_rtc_valid = true;
    s_rtc_set_count++;
    return ESP_OK;
}

bool native_protocol_rtc_last_set(int64_t *epoch_utc, int *offset_sec, int *set_count)
{
    if (!s_rtc_valid) {
        return false;
    }
    if (epoch_utc != NULL) *epoch_utc = s_rtc_epoch;
    if (offset_sec != NULL) *offset_sec = s_rtc_offset;
    if (set_count != NULL) *set_count = s_rtc_set_count;
    return true;
}

rtc_source_t rtc_pcf_get_local(struct tm *out)
{
    if (!s_rtc_valid || out == NULL) {
        return RTC_SOURCE_NONE;
    }
    const int64_t now_us = esp_timer_get_time();
    if (now_us < s_rtc_set_us) return RTC_SOURCE_NONE;
    const int64_t elapsed_seconds = (now_us - s_rtc_set_us) / 1000000;
    const int64_t base = s_rtc_epoch + (int64_t)s_rtc_offset;
    if (elapsed_seconds > RTC_CLOCK_MAX_EPOCH_UTC - base) return RTC_SOURCE_NONE;
    const int64_t local_seconds = base + elapsed_seconds;
    if (local_seconds < RTC_CLOCK_MIN_EPOCH_UTC ||
        local_seconds > RTC_CLOCK_MAX_EPOCH_UTC) return RTC_SOURCE_NONE;
    const time_t local = (time_t)local_seconds;
    if ((int64_t)local != local_seconds || gmtime_r(&local, out) == NULL) {
        return RTC_SOURCE_NONE;
    }
    return RTC_SOURCE_FALLBACK;
}
