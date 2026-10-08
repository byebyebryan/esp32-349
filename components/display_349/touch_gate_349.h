#pragma once

#include <stdbool.h>

/* After darkness, require three clear samples before accepting a new press.
 * One dropped AXS15231B sample must not turn a held finger into a new click. */
typedef struct {
    bool enabled;
    bool waiting_release;
    unsigned release_samples;
} touch_gate_349_t;

static inline void touch_gate_349_enable(touch_gate_349_t *gate, bool enabled)
{
    if (gate->enabled != enabled) {
        gate->enabled = enabled;
        gate->waiting_release = true;
        gate->release_samples = 0;
    }
}

static inline bool touch_gate_349_accept(touch_gate_349_t *gate, bool pressed)
{
    if (!gate->enabled) return false;
    if (!gate->waiting_release) return pressed;
    if (pressed) gate->release_samples = 0;
    else if (++gate->release_samples >= 3) gate->waiting_release = false;
    return false;
}
