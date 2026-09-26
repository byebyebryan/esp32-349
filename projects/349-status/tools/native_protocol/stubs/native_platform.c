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

esp_err_t rtc_pcf_set(int64_t epoch_utc, int offset_sec)
{
    s_rtc_epoch = epoch_utc;
    s_rtc_offset = offset_sec;
    s_rtc_set_us = esp_timer_get_time();
    s_rtc_valid = true;
    return ESP_OK;
}

rtc_source_t rtc_pcf_get_local(struct tm *out)
{
    if (!s_rtc_valid || out == NULL) {
        return RTC_SOURCE_NONE;
    }
    const int64_t elapsed_seconds = (esp_timer_get_time() - s_rtc_set_us) / 1000000;
    const int64_t local_seconds = s_rtc_epoch + s_rtc_offset + elapsed_seconds;
    const time_t local = (time_t)local_seconds;
    if ((int64_t)local != local_seconds || gmtime_r(&local, out) == NULL) {
        return RTC_SOURCE_NONE;
    }
    return RTC_SOURCE_FALLBACK;
}
