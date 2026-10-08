#pragma once

#include <stdbool.h>
#include <stdint.h>

#define BUTTON_349_DEBOUNCE_US INT64_C(30000)

typedef struct {
    int64_t candidate_since_us;
    bool candidate_pressed;
    bool pressed;
    bool ready;
} button_debounce_349_t;

static inline void button_debounce_349_init(button_debounce_349_t *button,
                                            bool pressed, int64_t now_us)
{
    *button = (button_debounce_349_t){
        .candidate_since_us = now_us,
        .candidate_pressed = pressed,
        .pressed = pressed,
        .ready = !pressed,
    };
}

/* One click on a stable release; a button held at startup must first release. */
static inline bool button_debounce_349_poll(button_debounce_349_t *button,
                                           bool pressed, int64_t now_us)
{
    if (pressed != button->candidate_pressed || now_us < button->candidate_since_us) {
        button->candidate_pressed = pressed;
        button->candidate_since_us = now_us;
    }
    if (button->candidate_pressed == button->pressed ||
        now_us - button->candidate_since_us < BUTTON_349_DEBOUNCE_US) return false;
    button->pressed = button->candidate_pressed;
    if (button->pressed) return false;
    const bool clicked = button->ready;
    button->ready = true;
    return clicked;
}
