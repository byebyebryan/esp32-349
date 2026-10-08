#include "backlight_policy.h"

static bool expired(const backlight_policy_t *policy, int64_t now_us)
{
    return now_us < policy->last_host_us ||
        now_us - policy->last_host_us >= (int64_t)policy->disconnect_s * 1000000;
}

static bool automatic_on(const backlight_policy_t *policy, int64_t now_us)
{
    return !policy->manual_off && !expired(policy, now_us) &&
        !policy->awaiting_control && policy->host_screen_on;
}

void backlight_policy_init(backlight_policy_t *policy, int64_t now_us)
{
    *policy = (backlight_policy_t){
        .last_host_us = now_us,
        .disconnect_s = BACKLIGHT_DEFAULT_DISCONNECT_S,
        .boost_s = BACKLIGHT_DEFAULT_BOOST_S,
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
    if (expired(policy, now_us)) {
        policy->boost_until_us = 0;
        if (policy->controlled) policy->awaiting_control = true;
    }
    policy->last_host_us = now_us;
}

void backlight_policy_configure(backlight_policy_t *policy, int64_t now_us,
                                uint8_t brightness, int disconnect_s,
                                bool has_screen_state, bool screen_on)
{
    /* A repeated host baseline is a sync/reconnect, not a new selection.
     * A changed baseline explicitly replaces the local brightness choice. */
    if (!automatic_on(policy, now_us) || brightness != policy->host_brightness) {
        policy->boost_until_us = 0;
    }
    if (brightness != policy->host_brightness) policy->brightness = brightness;
    policy->host_brightness = brightness;
    policy->disconnect_s = disconnect_s;
    if (has_screen_state) policy->host_screen_on = screen_on;
    policy->controlled = true;
    policy->awaiting_control = false;
    policy->last_host_us = now_us;
    if (!automatic_on(policy, now_us)) policy->boost_until_us = 0;
}

void backlight_policy_cycle_brightness(backlight_policy_t *policy)
{
    policy->boost_until_us = 0;
    policy->brightness = policy->brightness < 25 ? 25 :
                         policy->brightness < 50 ? 50 :
                         policy->brightness < 75 ? 75 :
                         policy->brightness < 100 ? 100 : 25;
}

void backlight_policy_toggle_power(backlight_policy_t *policy)
{
    policy->boost_until_us = 0;
    policy->manual_off = !policy->manual_off;
}

void backlight_policy_set_boost_duration(backlight_policy_t *policy, int seconds)
{
    if (seconds != policy->boost_s || seconds == 0) policy->boost_until_us = 0;
    policy->boost_s = seconds;
}

void backlight_policy_note_notification(backlight_policy_t *policy, int64_t now_us)
{
    /* Off decisions win; an arrival while dark never queues a later boost. */
    if (policy->boost_s > 0 && automatic_on(policy, now_us)) {
        policy->boost_until_us = now_us + (int64_t)policy->boost_s * 1000000;
    }
}

int backlight_policy_boost_remaining_ms(const backlight_policy_t *policy, int64_t now_us)
{
    if (!automatic_on(policy, now_us) || now_us >= policy->boost_until_us) return 0;
    return (int)((policy->boost_until_us - now_us + 999) / 1000);
}

const char *backlight_policy_reason(const backlight_policy_t *policy, int64_t now_us)
{
    if (policy->manual_off) return "manual_off";
    if (expired(policy, now_us)) return "disconnected";
    if (policy->awaiting_control) return "awaiting_host";
    if (!policy->host_screen_on) return "host_screen_off";
    if (backlight_policy_boost_remaining_ms(policy, now_us) > 0) return "notification_boost";
    return "on";
}

uint8_t backlight_policy_target(const backlight_policy_t *policy, int64_t now_us)
{
    if (!automatic_on(policy, now_us)) return 0;
    return backlight_policy_boost_remaining_ms(policy, now_us) > 0 ? 100 : policy->brightness;
}
