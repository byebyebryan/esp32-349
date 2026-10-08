#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "backlight_policy.h"
#include "touch_gate_349.h"
#include "button_debounce_349.h"
#include "backlight_pwm_349.h"

int main(void)
{
    /* Check the electrical model independently of the integer mapping.
     * Full-range inverted duty used to drive the 25% step past LED cutoff. */
    const uint8_t pwm_levels[] = {25, 50, 75, 100};
    uint32_t previous_duty = 256;
    const double full_sense_mv = 200.0 * (1.0 + 5100.0 / 49000.0);
    for (unsigned i = 0; i < sizeof(pwm_levels); i++) {
        const uint32_t duty = backlight_pwm_349_duty(pwm_levels[i]);
        const double sense_mv = full_sense_mv -
            3300.0 * duty / 256.0 * 5100.0 / 49000.0;
        const double fraction = sense_mv / full_sense_mv;
        assert(duty < previous_duty && sense_mv > 0);
        assert(fraction > pwm_levels[i] / 100.0 - 0.005 &&
               fraction < pwm_levels[i] / 100.0 + 0.005);
        previous_duty = duty;
    }
    assert(backlight_pwm_349_duty(100) == 0 && backlight_pwm_349_duty(255) == 0);
    for (uint8_t level = 1; level < 100; level++) {
        assert(backlight_pwm_349_duty(level) >= backlight_pwm_349_duty(level + 1));
        assert(backlight_pwm_349_duty(level) < 256);
    }

    backlight_policy_t p;
    backlight_policy_init(&p, 0);
    assert(backlight_policy_target(&p, 299999999) == 50);
    assert(backlight_policy_target(&p, 300000000) == 0);
    assert(strcmp(backlight_policy_reason(&p, 300000000), "disconnected") == 0);
    backlight_policy_note_rx(&p, 301000000);
    assert(backlight_policy_target(&p, 301000000) == 50); /* Legacy host. */
    backlight_policy_configure(&p, 302000000, 65, 300, true, false);
    for (int64_t now = 303000000; now < 610000000; now += 4000000) {
        backlight_policy_note_rx(&p, now);
        assert(backlight_policy_target(&p, now) == 0); /* Heartbeats don't wake. */
    }
    backlight_policy_configure(&p, 611000000, 65, 300, true, true);
    assert(backlight_policy_target(&p, 910999999) == 65);
    assert(backlight_policy_target(&p, 911000000) == 0);
    backlight_policy_note_rx(&p, 912000000);
    assert(backlight_policy_target(&p, 912000000) == 0); /* Probe doesn't flash on. */
    assert(strcmp(backlight_policy_reason(&p, 912000000), "awaiting_host") == 0);
    backlight_policy_configure(&p, 913000000, 70, 300, true, false);
    assert(backlight_policy_target(&p, 913000000) == 0);
    backlight_policy_configure(&p, 914000000, 70, 300, false, false);
    assert(backlight_policy_target(&p, 914000000) == 0); /* Unknown retains off. */
    backlight_policy_configure(&p, 915000000, 70, 300, true, true);
    assert(backlight_policy_target(&p, 915000000) == 70);
    backlight_policy_configure(&p, 915000000, 70, 86400, true, true);
    assert(backlight_policy_target(&p, 915000000 + INT64_C(86400000000) - 1) == 70);
    assert(backlight_policy_target(&p, 915000000 + INT64_C(86400000000)) == 0);

    backlight_policy_init(&p, 0);
    const uint8_t levels[] = {75, 100, 25, 50};
    for (unsigned i = 0; i < sizeof(levels); i++) {
        backlight_policy_cycle_brightness(&p);
        assert(p.brightness == levels[i]);
        backlight_policy_configure(&p, 0, 50, 300, true, true);
        assert(backlight_policy_target(&p, 0) == levels[i]); /* Sync doesn't undo. */
    }
    backlight_policy_toggle_power(&p);
    assert(p.manual_off && backlight_policy_target(&p, 0) == 0);
    backlight_policy_cycle_brightness(&p);
    assert(p.brightness == 75 && backlight_policy_target(&p, 0) == 0);
    backlight_policy_configure(&p, 10, 50, 300, true, true);
    backlight_policy_note_rx(&p, 20);
    assert(p.manual_off && p.brightness == 75);
    assert(strcmp(backlight_policy_reason(&p, 20), "manual_off") == 0);
    backlight_policy_configure(&p, 30, 65, 300, true, true);
    assert(p.brightness == 65 && p.manual_off); /* Changed host baseline replaces selection only. */
    backlight_policy_toggle_power(&p);
    assert(backlight_policy_target(&p, 30) == 65);
    backlight_policy_configure(&p, 40, 65, 300, true, false);
    backlight_policy_toggle_power(&p);
    backlight_policy_toggle_power(&p);
    assert(backlight_policy_target(&p, 40) == 0); /* On returns to automatic. */
    backlight_policy_configure(&p, 50, 65, 300, true, true);
    backlight_policy_cycle_brightness(&p);
    assert(p.brightness == 75);
    backlight_policy_toggle_power(&p);
    backlight_policy_toggle_power(&p);
    assert(backlight_policy_target(&p, 300000050) == 0); /* Buttons don't renew liveness. */
    backlight_policy_note_rx(&p, 300000060);
    assert(backlight_policy_target(&p, 300000060) == 0);
    backlight_policy_configure(&p, 300000070, 65, 300, true, true);
    assert(backlight_policy_target(&p, 300000070) == 75); /* Reconnect retains selection. */
    backlight_policy_init(&p, 300000080);
    assert(!p.manual_off && p.brightness == 50); /* Reset discards RAM choices. */

    button_debounce_349_t button;
    button_debounce_349_init(&button, false, 0);
    assert(!button_debounce_349_poll(&button, true, 1));
    assert(!button_debounce_349_poll(&button, false, 5000));
    assert(!button_debounce_349_poll(&button, true, 10000));
    assert(!button_debounce_349_poll(&button, true, 39999));
    assert(!button.pressed);
    assert(!button_debounce_349_poll(&button, true, 40000));
    assert(button.pressed);
    assert(!button_debounce_349_poll(&button, true, 10000000)); /* No hold repeat. */
    assert(!button_debounce_349_poll(&button, false, 10000001));
    assert(!button_debounce_349_poll(&button, true, 10005000));
    assert(!button_debounce_349_poll(&button, false, 10010000));
    assert(!button_debounce_349_poll(&button, false, 10039999));
    assert(button_debounce_349_poll(&button, false, 10040000));
    assert(!button_debounce_349_poll(&button, false, 10080000));
    button_debounce_349_init(&button, true, 0);
    assert(!button_debounce_349_poll(&button, false, 10));
    assert(!button_debounce_349_poll(&button, false, 30010)); /* Boot-held release is inert. */
    assert(!button_debounce_349_poll(&button, true, 40000));
    assert(!button_debounce_349_poll(&button, true, 70000));
    assert(!button_debounce_349_poll(&button, false, 80000));
    assert(button_debounce_349_poll(&button, false, 110000));

    touch_gate_349_t gate = {.enabled = true};
    assert(touch_gate_349_accept(&gate, true));
    touch_gate_349_enable(&gate, false);
    assert(!touch_gate_349_accept(&gate, true));
    assert(!touch_gate_349_accept(&gate, false));
    touch_gate_349_enable(&gate, true);
    assert(!touch_gate_349_accept(&gate, true));
    assert(!touch_gate_349_accept(&gate, false));
    assert(!touch_gate_349_accept(&gate, true)); /* Dropped sample while held. */
    assert(!touch_gate_349_accept(&gate, false));
    assert(!touch_gate_349_accept(&gate, false));
    assert(!touch_gate_349_accept(&gate, false));
    assert(touch_gate_349_accept(&gate, true));
    puts("backlight deadlines, local controls, debounce, reconnect and dark touch checks passed");
    return 0;
}
