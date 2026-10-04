#include "ui_fonts.h"

#if !LV_FONT_SOURCE_HAN_SANS_SC_14_CJK || !LV_FONT_SOURCE_HAN_SANS_SC_16_CJK
#error "349-status requires the Source Han 14/16 CJK fallback fonts"
#endif
LV_FONT_DECLARE(status_symbol_14);
LV_FONT_DECLARE(status_symbol_16);

void status_ui_fonts_init(status_ui_fonts_t *fonts)
{
    fonts->cjk_16 = lv_font_source_han_sans_sc_16_cjk;
    fonts->cjk_16.fallback = &status_symbol_16;
    fonts->cjk_14 = lv_font_source_han_sans_sc_14_cjk;
    fonts->cjk_14.fallback = &status_symbol_14;
    fonts->zone = lv_font_montserrat_16;
    fonts->zone.fallback = &fonts->cjk_16;
    fonts->body = lv_font_montserrat_14;
    fonts->body.fallback = &fonts->cjk_14;
    fonts->small = lv_font_montserrat_12;
    fonts->small.fallback = &fonts->cjk_14;
}
