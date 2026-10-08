#include "display_349.h"

#include "board_349.h"
#include "button_debounce_349.h"
#include "esp_check.h"
#include "esp_timer.h"

static button_debounce_349_t s_brightness;
static button_debounce_349_t s_power;
static bool s_initialized;

esp_err_t display_349_buttons_init(void)
{
    /* V2 schematic and Waveshare's button_bsp: BOOT0=IO0, SYS_OUT=IO16,
     * both active low. RESET goes directly to CHIP_PU and is not an input. */
    const gpio_config_t config = {
        .pin_bit_mask = (1ULL << BOARD_349_PIN_BUTTON_BRIGHTNESS) |
                        (1ULL << BOARD_349_PIN_BUTTON_POWER),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&config), "buttons349", "button GPIOs");
    const int64_t now_us = esp_timer_get_time();
    button_debounce_349_init(&s_brightness,
        gpio_get_level(BOARD_349_PIN_BUTTON_BRIGHTNESS) == 0, now_us);
    button_debounce_349_init(&s_power,
        gpio_get_level(BOARD_349_PIN_BUTTON_POWER) == 0, now_us);
    s_initialized = true;
    return ESP_OK;
}

display_349_buttons_t display_349_buttons_poll(void)
{
    if (!s_initialized) return (display_349_buttons_t){0};
    const int64_t now_us = esp_timer_get_time();
    const bool brightness = gpio_get_level(BOARD_349_PIN_BUTTON_BRIGHTNESS) == 0;
    const bool power = gpio_get_level(BOARD_349_PIN_BUTTON_POWER) == 0;
    const bool brightness_click = button_debounce_349_poll(&s_brightness, brightness, now_us);
    const bool power_click = button_debounce_349_poll(&s_power, power, now_us);
    return (display_349_buttons_t){
        .brightness_click = brightness_click,
        .power_click = power_click,
        .brightness_pressed = s_brightness.pressed,
        .power_pressed = s_power.pressed,
    };
}
