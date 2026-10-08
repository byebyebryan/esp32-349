#include "rtc.h"

#include <string.h>

#include "driver/i2c_master.h"
#include "display_349.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

static const char *TAG = "rtc";

#define PCF85063_ADDR 0x51
#define PCF85063_CTRL1 0x00
#define PCF85063_SEC   0x04

static i2c_master_dev_handle_t s_dev;
static bool s_hw_ok;

/* Fallback clock: last host sync plus elapsed time, used when the RTC is
 * missing or its oscillator stopped. */
static bool s_fallback_valid;
static int64_t s_fallback_epoch;
static int s_fallback_offset;
static int64_t s_fallback_us;

static bool decode_bcd(uint8_t value, int minimum, int maximum, int *out)
{
    if ((value >> 4) > 9 || (value & 0x0F) > 9) {
        return false;
    }
    const int decoded = (value >> 4) * 10 + (value & 0x0F);
    if (decoded < minimum || decoded > maximum) {
        return false;
    }
    *out = decoded;
    return true;
}

static uint8_t dec2bcd(uint8_t value)
{
    return (uint8_t)(((value / 10) << 4) | (value % 10));
}

/* Sakamoto: 0 = Sunday .. 6 = Saturday. */
static int weekday_of(int year, int month, int day)
{
    static const int offsets[] = {0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4};
    if (month < 3) {
        year -= 1;
    }
    return (year + year / 4 - year / 100 + year / 400 + offsets[month - 1] + day) % 7;
}

static esp_err_t read_regs(uint8_t reg, uint8_t *buf, size_t len)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, buf, len, pdMS_TO_TICKS(100));
}

static esp_err_t write_regs(uint8_t reg, const uint8_t *buf, size_t len)
{
    uint8_t tmp[8];
    if (len + 1 > sizeof(tmp)) {
        return ESP_ERR_INVALID_SIZE;
    }
    tmp[0] = reg;
    memcpy(tmp + 1, buf, len);
    return i2c_master_transmit(s_dev, tmp, len + 1, pdMS_TO_TICKS(100));
}

esp_err_t rtc_pcf_init(void)
{
    const i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .scl_speed_hz = 100000,
        .device_address = PCF85063_ADDR,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(display_349_i2c_bus0(), &cfg, &s_dev), TAG, "add device");

    /* 24-hour mode, oscillator running. */
    const uint8_t ctrl1 = 0x00;
    if (write_regs(PCF85063_CTRL1, &ctrl1, 1) != ESP_OK) {
        ESP_LOGW(TAG, "no PCF85063, using timer fallback");
        return ESP_OK;
    }

    uint8_t sec = 0;
    if (read_regs(PCF85063_SEC, &sec, 1) == ESP_OK) {
        s_hw_ok = (sec & 0x80) == 0; /* bit 7 = oscillator stop flag */
    }
    ESP_LOGI(TAG, "PCF85063 %s", s_hw_ok ? "ok" : "oscillator stopped");
    return ESP_OK;
}

esp_err_t rtc_pcf_set(int64_t epoch_utc, int offset_sec)
{
    if (!rtc_clock_valid(epoch_utc, offset_sec)) {
        return ESP_ERR_INVALID_ARG;
    }

    const int64_t local_seconds = epoch_utc + (int64_t)offset_sec;
    const time_t local = (time_t)local_seconds;
    if ((int64_t)local != local_seconds) {
        return ESP_ERR_INVALID_ARG;
    }
    struct tm tm;
    if (gmtime_r(&local, &tm) == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_fallback_epoch = epoch_utc;
    s_fallback_offset = offset_sec;
    s_fallback_us = esp_timer_get_time();
    s_fallback_valid = true;

    if (s_dev == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    const int year = tm.tm_year + 1900;
    if (year < 2000 || year > 2099) {
        /* The PCF85063 only stores two year digits for its 2000-based era. */
        s_hw_ok = false;
        return ESP_OK;
    }

    const uint8_t buf[7] = {
        (uint8_t)(dec2bcd((uint8_t)tm.tm_sec) & 0x7F),
        dec2bcd((uint8_t)tm.tm_min),
        dec2bcd((uint8_t)tm.tm_hour),
        dec2bcd((uint8_t)tm.tm_mday),
        (uint8_t)(tm.tm_wday + 1), /* 1..7, informational only */
        dec2bcd((uint8_t)(tm.tm_mon + 1)),
        dec2bcd((uint8_t)(year % 100)),
    };
    s_hw_ok = false;
    const esp_err_t err = write_regs(PCF85063_SEC, buf, sizeof(buf));
    if (err == ESP_OK) {
        s_hw_ok = true;
    }
    return err;
}

static bool decode_hardware_time(const uint8_t buf[7], struct tm *out)
{
    int second, minute, hour, day, month, year;
    /* The oscillator-stop flag can appear after initialization or host sync.
     * Check it on every read before trusting any of the calendar registers. */
    if ((buf[0] & 0x80) != 0 ||
        !decode_bcd(buf[0] & 0x7F, 0, 59, &second) ||
        !decode_bcd(buf[1] & 0x7F, 0, 59, &minute) ||
        !decode_bcd(buf[2] & 0x3F, 0, 23, &hour) ||
        !decode_bcd(buf[3] & 0x3F, 1, 31, &day) ||
        !decode_bcd(buf[5] & 0x1F, 1, 12, &month) ||
        !decode_bcd(buf[6], 0, 99, &year)) {
        return false;
    }
    year += 2000;
    static const uint8_t month_days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    if (day > month_days[month - 1] + (month == 2 && leap)) {
        return false;
    }

    out->tm_sec = second;
    out->tm_min = minute;
    out->tm_hour = hour;
    out->tm_mday = day;
    out->tm_mon = month - 1;
    out->tm_year = year - 1900;
    out->tm_wday = weekday_of(year, month, day);
    out->tm_yday = 0;
    out->tm_isdst = 0;
    return true;
}

rtc_source_t rtc_pcf_get_local(struct tm *out)
{
    if (out == NULL) {
        return RTC_SOURCE_NONE;
    }
    if (s_dev != NULL && s_hw_ok) {
        uint8_t buf[7];
        if (read_regs(PCF85063_SEC, buf, sizeof(buf)) == ESP_OK &&
            decode_hardware_time(buf, out)) {
            return RTC_SOURCE_HW;
        }
    }

    if (s_fallback_valid) {
        const int64_t now_us = esp_timer_get_time();
        if (now_us < s_fallback_us) {
            return RTC_SOURCE_NONE;
        }
        const int64_t elapsed = (now_us - s_fallback_us) / 1000000;
        const int64_t base = s_fallback_epoch + (int64_t)s_fallback_offset;
        if (elapsed > RTC_CLOCK_MAX_EPOCH_UTC - base) {
            return RTC_SOURCE_NONE;
        }
        const int64_t local_seconds = base + elapsed;
        if (local_seconds < RTC_CLOCK_MIN_EPOCH_UTC ||
            local_seconds > RTC_CLOCK_MAX_EPOCH_UTC) {
            return RTC_SOURCE_NONE;
        }
        const time_t local = (time_t)local_seconds;
        if ((int64_t)local != local_seconds || gmtime_r(&local, out) == NULL) {
            return RTC_SOURCE_NONE;
        }
        return RTC_SOURCE_FALLBACK;
    }
    return RTC_SOURCE_NONE;
}
