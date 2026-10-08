#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "display_349.h"
#include "esp_timer.h"
#include "rtc.h"

static uint8_t s_regs[11];
static int s_bus, s_device;
static int64_t s_now_us = 1000000;
static esp_err_t s_read_error, s_write_error;
static const int64_t LEAP_DAY_EPOCH = INT64_C(1709164800); /* 2024-02-29 UTC. */

int64_t esp_timer_get_time(void)
{
    return s_now_us;
}

i2c_master_bus_handle_t display_349_i2c_bus0(void)
{
    return &s_bus;
}

esp_err_t i2c_master_bus_add_device(i2c_master_bus_handle_t bus,
                                  const i2c_device_config_t *config,
                                  i2c_master_dev_handle_t *device)
{
    assert(bus == &s_bus && config->device_address == 0x51);
    assert(config->dev_addr_length == I2C_ADDR_BIT_LEN_7 && config->scl_speed_hz == 100000);
    *device = &s_device;
    return ESP_OK;
}

esp_err_t i2c_master_transmit_receive(i2c_master_dev_handle_t device,
                                    const uint8_t *write_buffer, size_t write_size,
                                    uint8_t *read_buffer, size_t read_size, int timeout_ms)
{
    assert(device == &s_device && write_size == 1 && timeout_ms == 100);
    assert(write_buffer[0] + read_size <= sizeof(s_regs));
    if (s_read_error != ESP_OK) return s_read_error;
    memcpy(read_buffer, s_regs + write_buffer[0], read_size);
    return ESP_OK;
}

esp_err_t i2c_master_transmit(i2c_master_dev_handle_t device,
                            const uint8_t *buffer, size_t size, int timeout_ms)
{
    assert(device == &s_device && size >= 2 && timeout_ms == 100);
    assert(buffer[0] + size - 1 <= sizeof(s_regs));
    if (s_write_error != ESP_OK) return s_write_error;
    memcpy(s_regs + buffer[0], buffer + 1, size - 1);
    return ESP_OK;
}

static void expect_fallback(void)
{
    struct tm value = {0};
    assert(rtc_pcf_get_local(&value) == RTC_SOURCE_FALLBACK);
    assert(value.tm_year == 124 && value.tm_mon == 1 && value.tm_mday == 29);
    assert(value.tm_hour == 0 && value.tm_min == 0 && value.tm_sec == 10);
}

static void test_invalid_hardware_reads(void)
{
    assert(rtc_pcf_set(LEAP_DAY_EPOCH, 0) == ESP_OK);
    s_now_us += 10000000;
    s_regs[4] |= 0x80; /* Oscillator stopped after a successful host sync. */
    expect_fallback();
    s_regs[4] &= 0x7F;

    static const uint8_t valid[7] = {0x12, 0x34, 0x23, 0x29, 4, 0x02, 0x24};
    /* Register offset from seconds, invalid value: include invalid BCD that
     * would otherwise decode to an in-range decimal number. */
    static const uint8_t invalid[][2] = {
        {0, 0x60}, {0, 0x0A}, {1, 0x60}, {1, 0x0A},
        {2, 0x24}, {2, 0x0A}, {3, 0x00}, {3, 0x32}, {3, 0x0A},
        {5, 0x00}, {5, 0x13}, {5, 0x0A}, {6, 0x0A}, {6, 0xA0},
        {3, 0x30}, {6, 0x23}, /* February 30, February 29 in a non-leap year. */
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
        memcpy(s_regs + 4, valid, sizeof(valid));
        s_regs[4 + invalid[i][0]] = invalid[i][1];
        expect_fallback();
    }
    memcpy(s_regs + 4, valid, sizeof(valid));
    s_regs[7] = 0x31;
    s_regs[9] = 0x04; /* April 31. */
    expect_fallback();

    memcpy(s_regs + 4, valid, sizeof(valid));
    s_read_error = ESP_FAIL;
    expect_fallback();
    s_read_error = ESP_OK;
    struct tm value = {0};
    assert(rtc_pcf_get_local(&value) == RTC_SOURCE_HW);
    assert(value.tm_hour == 23 && value.tm_min == 34 && value.tm_sec == 12);
    assert(value.tm_year == 124 && value.tm_mon == 1 && value.tm_mday == 29 && value.tm_wday == 4);

    s_regs[10] = 0x00; /* The year 2000 is a leap year too. */
    assert(rtc_pcf_get_local(&value) == RTC_SOURCE_HW && value.tm_year == 100);
    s_regs[7] = 0x31; s_regs[9] = 0x12; s_regs[10] = 0x99;
    assert(rtc_pcf_get_local(&value) == RTC_SOURCE_HW && value.tm_year == 199);

    /* A failed write leaves the freshly synchronized timer fallback usable. */
    s_write_error = ESP_FAIL;
    assert(rtc_pcf_set(LEAP_DAY_EPOCH, 0) == ESP_FAIL);
    s_now_us += 10000000;
    expect_fallback();
    s_write_error = ESP_OK;
    assert(rtc_pcf_set(LEAP_DAY_EPOCH, 0) == ESP_OK);
    assert(rtc_pcf_get_local(&value) == RTC_SOURCE_HW && value.tm_hour == 0 && value.tm_sec == 0);
}

int main(void)
{
    struct tm value = {0};
    assert(rtc_pcf_get_local(NULL) == RTC_SOURCE_NONE);
    assert(rtc_pcf_get_local(&value) == RTC_SOURCE_NONE);
    assert(rtc_pcf_set(LEAP_DAY_EPOCH, 0) == ESP_ERR_INVALID_STATE);
    s_now_us += 10000000;
    expect_fallback();
    assert(rtc_pcf_init() == ESP_OK);
    test_invalid_hardware_reads();
    puts("native RTC: stopped/invalid reads use fallback; valid hardware recovers");
    return 0;
}
