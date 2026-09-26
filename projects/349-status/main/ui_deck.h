#pragma once

#include <stdint.h>
#include "lvgl.h"

void ui_deck_init(lv_obj_t *parent, const lv_font_t *small, const lv_font_t *meta);
void ui_deck_show(bool visible);
void ui_deck_tick(uint32_t dirty);
