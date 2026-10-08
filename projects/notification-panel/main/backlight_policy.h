#pragma once

#include <stdbool.h>
#include <stdint.h>

#define BACKLIGHT_DEFAULT_PERCENT 50
#define BACKLIGHT_DEFAULT_DISCONNECT_S 300
#define BACKLIGHT_MAX_DISCONNECT_S 86400
#define BACKLIGHT_DEFAULT_BOOST_S 30
#define BACKLIGHT_MAX_BOOST_S 86400

typedef struct {
    int64_t last_host_us;
    int disconnect_s;
    int boost_s;
    int64_t boost_until_us;
    uint8_t brightness;
    uint8_t host_brightness;
    bool host_screen_on;
    bool controlled;
    bool awaiting_control;
    bool manual_off;
} backlight_policy_t;

void backlight_policy_init(backlight_policy_t *policy, int64_t now_us);
void backlight_policy_note_rx(backlight_policy_t *policy, int64_t now_us);
void backlight_policy_cycle_brightness(backlight_policy_t *policy);
void backlight_policy_toggle_power(backlight_policy_t *policy);
void backlight_policy_set_boost_duration(backlight_policy_t *policy, int seconds);
void backlight_policy_note_notification(backlight_policy_t *policy, int64_t now_us);
int backlight_policy_boost_remaining_ms(const backlight_policy_t *policy, int64_t now_us);
void backlight_policy_configure(backlight_policy_t *policy, int64_t now_us,
                                uint8_t brightness, int disconnect_s,
                                bool has_screen_state, bool screen_on);
uint8_t backlight_policy_target(const backlight_policy_t *policy, int64_t now_us);
const char *backlight_policy_reason(const backlight_policy_t *policy, int64_t now_us);
