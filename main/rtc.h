#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <time.h>

#include "esp_err.h"

#define RTC_CLOCK_MIN_EPOCH_UTC (-INT64_C(62135596800))
#define RTC_CLOCK_MAX_EPOCH_UTC INT64_C(253402300799)
#define RTC_CLOCK_MAX_OFFSET_SEC 86400

/* Keep wire parsing and RTC updates on the same supported calendar range. */
static inline bool rtc_clock_valid(int64_t epoch_utc, int offset_sec)
{
    if (epoch_utc < RTC_CLOCK_MIN_EPOCH_UTC || epoch_utc > RTC_CLOCK_MAX_EPOCH_UTC ||
        offset_sec < -RTC_CLOCK_MAX_OFFSET_SEC || offset_sec > RTC_CLOCK_MAX_OFFSET_SEC) {
        return false;
    }
    const int64_t local = epoch_utc + (int64_t)offset_sec;
    return local >= RTC_CLOCK_MIN_EPOCH_UTC && local <= RTC_CLOCK_MAX_EPOCH_UTC;
}

typedef enum {
    RTC_SOURCE_NONE = 0,
    RTC_SOURCE_HW,
    RTC_SOURCE_FALLBACK,
} rtc_source_t;

/* PCF85063 on I2C0; non-fatal if absent (falls back to the last clock sync). */
esp_err_t rtc_pcf_init(void);

/* Set the hardware RTC from a UTC epoch + offset; always updates the fallback. */
esp_err_t rtc_pcf_set(int64_t epoch_utc, int offset_sec);

/* Local broken-down time; returns where the time came from. */
rtc_source_t rtc_pcf_get_local(struct tm *out);
