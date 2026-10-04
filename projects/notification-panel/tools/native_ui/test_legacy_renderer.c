#define _POSIX_C_SOURCE 200809L

#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"
#include "proto.h"
#include "state.h"
#include "ui_deck.h"
#include "ui_fonts.h"

#include "../../main/ui.c"

static int64_t s_now_us = 1000000;

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "CHECK failed at %s:%d: %s\n", \
                __FILE__, __LINE__, #condition); \
        exit(1); \
    } \
} while (0)

int64_t esp_timer_get_time(void)
{
    return s_now_us;
}

bool link_host_connected(void)
{
    return true;
}

void proto_send_input_dismiss(int id)
{
    (void)id;
}

void ui_deck_init(lv_obj_t *parent, const lv_font_t *small, const lv_font_t *meta)
{
    (void)parent;
    (void)small;
    (void)meta;
}

void ui_deck_show(bool visible)
{
    (void)visible;
}

void ui_deck_tick(uint32_t dirty)
{
    (void)dirty;
}

static lv_display_t *make_display(void)
{
    lv_init();
    lv_display_t *display = lv_display_create(DISPLAY_349_H_RES, DISPLAY_349_V_RES);
    CHECK(display != NULL);
    return display;
}

static int render_progress_value(float value, bool has_value)
{
    status_zone_t zone = { .has_value = has_value, .value = value };
    lv_obj_t *parent = lv_obj_create(lv_screen_active());
    make_progress_zone(parent, &zone);
    lv_obj_t *container = lv_obj_get_child(parent, 0);
    CHECK(container != NULL);
    lv_obj_t *bar = lv_obj_get_child(container, 0);
    CHECK(bar != NULL);
    return lv_bar_get_value(bar);
}

static void set_media(float pos, float len)
{
    state_lock();
    status_media_t *media = &state_get()->media;
    memset(media, 0, sizeof(*media));
    media->valid = true;
    strcpy(media->state, "paused");
    strcpy(media->title, "Native safety test");
    media->pos = pos;
    media->len = len;
    state_unlock();
}

static void test_legacy_renderer_casts(void)
{
    lv_display_t *display = make_display();
    status_ui_fonts_init(&s_fonts);

    CHECK(render_progress_value(0.5f, true) == 50);
    CHECK(render_progress_value(0.0f, false) == 0);
    CHECK(render_progress_value(-2.0f, true) == 0);
    CHECK(render_progress_value(2.0f, true) == 100);
    CHECK(render_progress_value(NAN, true) == 0);
    CHECK(render_progress_value(INFINITY, true) == 0);
    CHECK(render_progress_value(-INFINITY, true) == 0);

    state_init();
    status_zone_t media_zone = { .w = 200 };
    lv_obj_t *media_parent = lv_obj_create(lv_screen_active());
    make_media_zone(media_parent, &media_zone);

    /* The legacy media percentage used to cast an infinite float ratio to int. */
    set_media(FLT_MAX, FLT_MIN);
    ui_update_media();
    CHECK(lv_bar_get_value(s_media_bar) == 100);

    set_media(NAN, 1.0f);
    ui_update_media();
    CHECK(lv_bar_get_value(s_media_bar) == 0);

    set_media(INFINITY, 1.0f);
    ui_update_media();
    CHECK(lv_bar_get_value(s_media_bar) == 0);

    set_media(-2.0f, 1.0f);
    ui_update_media();
    CHECK(lv_bar_get_value(s_media_bar) == 0);

    set_media(2.0f, 1.0f);
    ui_update_media();
    CHECK(lv_bar_get_value(s_media_bar) == 100);

    lv_display_delete(display);
    lv_deinit();
}

int main(void)
{
    test_legacy_renderer_casts();
    puts("native legacy renderer: pass (progress guards and bounded media arithmetic)");
    return 0;
}
