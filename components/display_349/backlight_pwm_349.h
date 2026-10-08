#pragma once

#include <stdint.h>

/* V2 AP3032 filtered-PWM feedback: R48=5.1k, R49+R52=49k,
 * VFB=200mV, GPIO high=3.3V. With PWM average Vpwm,
 * Vsense = VFB * (1 + 5.1/49) - Vpwm * 5.1/49.
 * The nominal current reaches zero at 64.29% high duty, not 100%.
 * Map requested current fraction across that range; BL_EN owns true off.
 * LEDC 8-bit periods contain 256 counts. This is nominal current, not lux. */
static inline uint32_t backlight_pwm_349_duty(uint8_t percent)
{
    if (percent > 100) percent = 100;
    const uint64_t numerator = (uint64_t)(100 - percent) * 256 * 200 * (49000 + 5100);
    const uint64_t denominator = UINT64_C(100) * 3300 * 5100;
    return (uint32_t)((numerator + denominator / 2) / denominator);
}
