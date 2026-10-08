#include "backlight_policy.h"

static bool expired(const backlight_policy_t *policy, int64_t now_us)
{
    return now_us < policy->last_host_us ||
        now_us - policy->last_host_us >= (int64_t)policy->disconnect_s * 1000000;
}

void backlight_policy_init(backlight_policy_t *policy, int64_t now_us)
{
    *policy = (backlight_policy_t){
        .last_host_us = now_us,
        .disconnect_s = BACKLIGHT_DEFAULT_DISCONNECT_S,
        .brightness = BACKLIGHT_DEFAULT_PERCENT,
        .host_brightness = BACKLIGHT_DEFAULT_PERCENT,
        .host_screen_on = true,
    };
}

void backlight_policy_note_rx(backlight_policy_t *policy, int64_t now_us)
{
    /* A reconnect probe must not light a managed display before the new
     * daemon has replayed its current screen state. Legacy hosts still wake
     * with ordinary traffic when no display controller has been negotiated. */
    if (policy->controlled && expired(policy, now_us)) {
        policy->awaiting_control = true;
    }
    policy->last_host_us = now_us;
}

void backlight_policy_configure(backlight_policy_t *policy, int64_t now_us,
                                uint8_t brightness, int disconnect_s,
                                bool has_screen_state, bool screen_on)
{
    /* A repeated host baseline is a sync/reconnect, not a new selection.
     * A changed baseline explicitly replaces the local brightness choice. */
    if (brightness != policy->host_brightness) policy->brightness = brightness;
    policy->host_brightness = brightness;
    policy->disconnect_s = disconnect_s;
    if (has_screen_state) policy->host_screen_on = screen_on;
    policy->controlled = true;
    policy->awaiting_control = false;
    policy->last_host_us = now_us;
}

void backlight_policy_cycle_brightness(backlight_policy_t *policy)
{
    policy->brightness = policy->brightness < 25 ? 25 :
                         policy->brightness < 50 ? 50 :
                         policy->brightness < 75 ? 75 :
                         policy->brightness < 100 ? 100 : 25;
}

void backlight_policy_toggle_power(backlight_policy_t *policy)
{
    policy->manual_off = !policy->manual_off;
}

const char *backlight_policy_reason(const backlight_policy_t *policy, int64_t now_us)
{
    if (policy->manual_off) return "manual_off";
    if (expired(policy, now_us)) return "disconnected";
    if (policy->awaiting_control) return "awaiting_host";
    if (!policy->host_screen_on) return "host_screen_off";
    return "on";
}

uint8_t backlight_policy_target(const backlight_policy_t *policy, int64_t now_us)
{
    return policy->manual_off || expired(policy, now_us) || policy->awaiting_control ||
        !policy->host_screen_on ? 0 : policy->brightness;
}
