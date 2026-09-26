#pragma once
#include "lvgl.h"

/* Copy descriptors so fallback chains never mutate shared const fonts. */
typedef struct {
    lv_font_t zone, body, small, cjk_14, cjk_16;
} status_ui_fonts_t;
void status_ui_fonts_init(status_ui_fonts_t *fonts);
