/*
 * Headless integration checks for the production LVGL deck. This translation
 * unit includes ui_deck.c so tests can inspect its private rendered slots while
 * every gesture still enters through an LVGL pointer read callback.
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"
#include "state.h"

#include "../../main/ui_deck.c"

#define DISPLAY_WIDTH 640
#define DISPLAY_HEIGHT 172
#define FRAME_PIXELS (DISPLAY_WIDTH * DISPLAY_HEIGHT)

static status_state_t s_state;
static status_notif_t s_notifs[STATUS_MAX_NOTIFS];
static int64_t s_now_us = 1000000;
static bool s_host_connected = true;
static int s_dismiss_count;
static int s_last_dismiss_id = -1;
static lv_display_t *s_display;
static lv_indev_t *s_pointer;
static lv_point_t s_pointer_point;
static lv_indev_state_t s_pointer_state = LV_INDEV_STATE_RELEASED;
static uint16_t s_draw_buffer[FRAME_PIXELS];
static uint16_t s_framebuffer[FRAME_PIXELS];

void state_lock(void) {}
void state_unlock(void) {}
status_state_t *state_get(void) { return &s_state; }

bool link_host_connected(void) { return s_host_connected; }

int64_t esp_timer_get_time(void) { return s_now_us; }

rtc_source_t rtc_pcf_get_local(struct tm *out)
{
    (void)out;
    return RTC_SOURCE_NONE;
}

void proto_send_input_dismiss(int id)
{
    s_dismiss_count++;
    s_last_dismiss_id = id;
}

void state_hide_notif(int id)
{
    for (int i = 0; i < s_state.hidden_count; i++) {
        if (s_state.hidden_ids[i] == id) {
            return;
        }
    }
    assert(s_state.hidden_count < STATUS_MAX_NOTIFS);
    s_state.hidden_ids[s_state.hidden_count++] = id;
}

static void pointer_read(lv_indev_t *indev, lv_indev_data_t *data)
{
    (void)indev;
    data->point = s_pointer_point;
    data->state = s_pointer_state;
    data->continue_reading = false;
}

static void display_flush(lv_display_t *display, const lv_area_t *area,
                          uint8_t *pixels)
{
    for (int32_t y = area->y1; y <= area->y2; y++) {
        if (y < 0 || y >= DISPLAY_HEIGHT) {
            continue;
        }
        const int32_t begin_x = area->x1 < 0 ? 0 : area->x1;
        const int32_t end_x = area->x2 >= DISPLAY_WIDTH ? DISPLAY_WIDTH - 1 : area->x2;
        if (begin_x > end_x) {
            continue;
        }
        /* DIRECT mode flush pointers address the full-screen framebuffer. */
        const size_t source_offset = (size_t)y * DISPLAY_WIDTH + (size_t)begin_x;
        const size_t destination_offset = (size_t)y * DISPLAY_WIDTH + (size_t)begin_x;
        memcpy(&s_framebuffer[destination_offset],
               (const uint16_t *)pixels + source_offset,
               (size_t)(end_x - begin_x + 1) * sizeof(uint16_t));
    }
    lv_display_flush_ready(display);
}

static void repaint(void);

static void write_ppm(const char *path)
{
    repaint();
    FILE *file = fopen(path, "wb");
    assert(file != NULL);
    fprintf(file, "P6\n%d %d\n255\n", DISPLAY_WIDTH, DISPLAY_HEIGHT);
    for (int i = 0; i < FRAME_PIXELS; i++) {
        const uint16_t packed = s_framebuffer[i];
        const uint8_t red5 = (uint8_t)((packed >> 11) & 0x1f);
        const uint8_t green6 = (uint8_t)((packed >> 5) & 0x3f);
        const uint8_t blue5 = (uint8_t)(packed & 0x1f);
        const uint8_t pixel[3] = {
            (uint8_t)((red5 << 3) | (red5 >> 2)),
            (uint8_t)((green6 << 2) | (green6 >> 4)),
            (uint8_t)((blue5 << 3) | (blue5 >> 2)),
        };
        assert(fwrite(pixel, sizeof(pixel), 1, file) == 1);
    }
    assert(fclose(file) == 0);
}

static void repaint(void)
{
    lv_refr_now(s_display);
}

static void step(uint32_t elapsed_ms)
{
    s_now_us += (int64_t)elapsed_ms * 1000;
    lv_tick_inc(elapsed_ms);
    lv_timer_handler();
    ui_deck_tick(0);
}

static void pointer_sample(int x, int y, bool pressed, uint32_t elapsed_ms)
{
    s_pointer_point.x = (lv_coord_t)x;
    s_pointer_point.y = (lv_coord_t)y;
    s_pointer_state = pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    s_now_us += (int64_t)elapsed_ms * 1000;
    lv_tick_inc(elapsed_ms);
    lv_indev_read(s_pointer);
    lv_timer_handler();
    ui_deck_tick(STATE_DIRTY_NOTIF);
}

static void pointer_press(int x, int y)
{
    assert(s_pointer_state == LV_INDEV_STATE_RELEASED);
    pointer_sample(x, y, true, 1);
}

static void pointer_move(int x, int y, uint32_t elapsed_ms)
{
    assert(s_pointer_state == LV_INDEV_STATE_PRESSED);
    pointer_sample(x, y, true, elapsed_ms);
}

static void pointer_release(int x, int y)
{
    assert(s_pointer_state == LV_INDEV_STATE_PRESSED);
    pointer_sample(x, y, false, 1);
}

static void finish_animation(void)
{
    for (int i = 0; i < 14 && deck_input_state(&s_input) == DECK_INPUT_SETTLING; i++) {
        step(20);
    }
    assert(deck_input_state(&s_input) != DECK_INPUT_SETTLING);
    repaint();
}

static void set_record(status_notif_t *notif, int id, int urgency,
                       const char *summary, const char *body)
{
    memset(notif, 0, sizeof(*notif));
    notif->valid = true;
    notif->id = id;
    notif->urgency = urgency;
    snprintf(notif->app, sizeof(notif->app), "app-%d", id);
    snprintf(notif->summary, sizeof(notif->summary), "%s", summary);
    snprintf(notif->body, sizeof(notif->body), "%s", body);
}

/* Assign host cache order (oldest first), without changing focus sequences. */
static void set_raw_order(const int *deck_order_newest_first, int count)
{
    assert(count >= 0 && count <= STATUS_MAX_NOTIFS);
    memset(s_notifs, 0, sizeof(s_notifs));
    for (int i = 0; i < count; i++) {
        const int id = deck_order_newest_first[count - 1 - i];
        char summary[STATUS_NOTIF_SUMMARY_MAX];
        char body[STATUS_NOTIF_BODY_MAX];
        snprintf(summary, sizeof(summary), "summary-%d", id);
        snprintf(body, sizeof(body), "body-%d", id);
        set_record(&s_notifs[i], id, 1, summary, body);
    }
    s_state.notifs = s_notifs;
    s_state.notif_capacity = STATUS_MAX_NOTIFS;
    s_state.cache_limit = STATUS_MAX_NOTIFS;
    s_state.notif_count = count;
    s_state.got_sync = true;
    s_state.last_rx_us = s_now_us;
    s_host_connected = true;
}

static void reset_deck(const int *deck_order_newest_first, int count, int overflow)
{
    if (s_pointer_state == LV_INDEV_STATE_PRESSED) {
        pointer_release(s_pointer_point.x, s_pointer_point.y);
    }
    if (deck_input_state(&s_input) == DECK_INPUT_SETTLING) {
        finish_animation();
    }
    if (deck_input_state(&s_input) == DECK_INPUT_IGNORED &&
        !deck_input_pointer_down(&s_input)) {
        ui_deck_tick(0);
    }

    const int none = 0;
    set_raw_order(&none, 0);
    s_state.notif_overflow = 0;
    s_state.hidden_count = 0;
    s_state.dashboard = (status_dashboard_t){0};
    s_state.notif_focus_seq++;
    s_state.notif_focus_id = 0;
    s_state.notif_focus_urgency = 1;
    s_state.notif_critical_seq++;
    s_state.notif_critical_id = 0;
    ui_deck_tick(STATE_DIRTY_NOTIF);

    set_raw_order(deck_order_newest_first, count);
    s_state.notif_overflow = overflow;
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_deck.count == count);
    if (count > 0) {
        assert(s_deck.has_focus && s_deck.focus_id == deck_order_newest_first[0]);
    }
    repaint();
}

static void set_normal_focus(int id)
{
    s_state.notif_focus_seq++;
    s_state.notif_focus_id = id;
    s_state.notif_focus_urgency = 1;
    s_state.notif_focus_us = s_now_us;
    ui_deck_tick(STATE_DIRTY_NOTIF);
}

static void set_critical_focus(int id)
{
    s_state.notif_critical_seq++;
    s_state.notif_critical_id = id;
    s_state.notif_critical_us = s_now_us;
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == id) {
            s_state.notifs[i].urgency = 2;
        }
    }
    ui_deck_tick(STATE_DIRTY_NOTIF);
}

static void add_notification(int id, int urgency)
{
    assert(s_state.notif_count < STATUS_MAX_NOTIFS);
    set_record(&s_notifs[s_state.notif_count], id, urgency,
               "new arrival", "arrival body");
    s_state.notif_count++;
    s_state.last_rx_us = s_now_us;
}

static int current_id(void)
{
    assert(s_cards[1].valid);
    return s_cards[1].id;
}

static int neighbor_id(int slot)
{
    assert(slot == 0 || slot == 2);
    assert(s_cards[slot].valid);
    return s_cards[slot].id;
}

static const char *label_storage(lv_obj_t *label)
{
    return (const char *)lv_obj_get_user_data(label);
}

static void test_geometry_and_slow_directions(void)
{
    const int ids[] = {103, 102, 101};
    reset_deck(ids, 3, 0);
    assert(current_id() == 103);
    assert(neighbor_id(0) == 101 && neighbor_id(2) == 102);
    assert(lv_obj_get_x(s_cards[0].root) == -392);
    assert(lv_obj_get_x(s_cards[1].root) == 8);
    assert(lv_obj_get_x(s_cards[2].root) == 408);
    write_ppm("/tmp/349-native-ui-at-rest.ppm");

    const int next = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(300, 80, 80);
    pointer_move(220, 80, 320);
    assert(s_offset == -100);
    assert(s_cards[2].id == next); /* Binding remains fixed while dragging. */
    write_ppm("/tmp/349-native-ui-mid-drag.ppm");
    pointer_release(220, 80);
    assert(deck_input_state(&s_input) == DECK_INPUT_SETTLING);
    finish_animation();
    assert(s_deck.focus_id == next);
    assert(current_id() == next);

    const int previous = neighbor_id(0);
    pointer_press(320, 80);
    pointer_move(340, 80, 80);
    pointer_move(420, 80, 320);
    pointer_release(420, 80);
    finish_animation();
    assert(s_deck.focus_id == previous);
    assert(current_id() == previous);
}

static void test_cancel_flick_body_peek_and_dismiss(void)
{
    const int ids[] = {201, 202, 203};
    reset_deck(ids, 3, 0);
    const int source = current_id();

    pointer_press(320, 80);
    pointer_move(295, 80, 30);
    pointer_release(295, 80);
    finish_animation();
    assert(s_deck.focus_id == source);

    const int flick_target = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(307, 80, 10);
    pointer_move(270, 80, 50);
    pointer_release(270, 80);
    finish_animation();
    assert(s_deck.focus_id == flick_target);

    const int body_source = current_id();
    pointer_press(320, 90);
    pointer_release(320, 90);
    assert(s_deck.focus_id == body_source);
    assert(deck_input_state(&s_input) == DECK_INPUT_IDLE);

    const int peek_target = neighbor_id(2);
    pointer_press(590, 80);
    assert(deck_input_state(&s_input) == DECK_INPUT_BUTTON_PEEK);
    pointer_release(590, 80);
    finish_animation();
    assert(s_deck.focus_id == peek_target);

    const int dismiss_source = current_id();
    const int dismiss_count = s_dismiss_count;
    pointer_press(536, 26);
    assert(deck_input_state(&s_input) == DECK_INPUT_BUTTON_DISMISS);
    pointer_release(536, 26);
    assert(s_dismiss_count == dismiss_count + 1);
    assert(s_last_dismiss_id == dismiss_source);
    assert(!reachable(dismiss_source));

    const int after_dismiss = s_deck.focus_id;
    const int button_count = s_dismiss_count;
    pointer_press(536, 26);
    pointer_move(570, 26, 30);
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    pointer_release(570, 26);
    assert(s_dismiss_count == button_count);
    assert(s_deck.focus_id == after_dismiss);
}

static void test_axis_rail_and_peek_drag(void)
{
    const int ids[] = {301, 302, 303};
    reset_deck(ids, 3, 0);
    int source = current_id();

    pointer_press(320, 80);
    pointer_move(320, 120, 40);
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    pointer_release(320, 120);
    assert(s_deck.focus_id == source);

    const int cross_target = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(100, 80, 50);
    assert(deck_input_state(&s_input) == DECK_INPUT_DRAGGING);
    pointer_release(100, 80);
    finish_animation();
    assert(s_deck.focus_id == cross_target);
    source = current_id();

    /* A press that starts on the rail keeps rail ownership after entering the deck. */
    pointer_press(100, 80);
    pointer_move(320, 80, 50);
    assert(!deck_input_busy(&s_input));
    assert(s_deck.focus_id == source);
    pointer_release(320, 80);
    finish_animation();
    assert(!deck_input_busy(&s_input));
    assert(s_deck.focus_id == source);

    pointer_press(590, 80);
    pointer_move(590, 120, 40);
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    pointer_release(590, 120);
    assert(s_deck.focus_id == source);

    pointer_press(590, 80);
    pointer_move(606, 91, 20);
    pointer_move(611, 100, 20);
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    pointer_release(611, 100);
    assert(s_deck.focus_id == source);

    const int target = neighbor_id(2);
    pointer_press(590, 80);
    pointer_move(570, 80, 80);
    pointer_move(490, 80, 320);
    pointer_release(490, 80);
    finish_animation();
    assert(s_deck.focus_id == target);
}

static void test_arrivals_replacement_and_sync_order(void)
{
    const int ids[] = {401, 402, 403};
    reset_deck(ids, 3, 0);
    const int source = current_id();
    const int target = neighbor_id(2);

    pointer_press(320, 80);
    pointer_move(300, 80, 80);
    pointer_move(220, 80, 320);
    add_notification(404, 1);
    set_normal_focus(404);
    assert(s_deck.focus_id == source);
    assert(s_cards[1].id == source && s_cards[2].id == target);
    add_notification(405, 2);
    set_critical_focus(405);
    assert(s_deck.focus_id == source);
    pointer_release(220, 80);
    finish_animation();
    assert(s_deck.focus_id == 405); /* Deferred critical runs after release. */

    const int reordered[] = {503, 501, 502};
    reset_deck((int[]){503, 502, 501}, 3, 0);
    const int stable_source = current_id();
    const int captured_target = neighbor_id(2);
    assert(stable_source == 503 && captured_target == 502);
    pointer_press(320, 80);
    pointer_move(300, 80, 80);
    pointer_move(220, 80, 320);

    /* Replacement text and sync order change while copies are captured. */
    set_raw_order(reordered, 3);
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == captured_target) {
            snprintf(s_state.notifs[i].summary,
                     sizeof(s_state.notifs[i].summary), "replacement-%d", captured_target);
            snprintf(s_state.notifs[i].body,
                     sizeof(s_state.notifs[i].body), "replacement body");
        }
    }
    s_state.last_rx_us = s_now_us;
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_deck.focus_id == stable_source);
    assert(s_cards[1].id == stable_source && s_cards[2].id == captured_target);
    assert(strstr(label_storage(s_cards[2].title), "summary-") != NULL);

    pointer_release(220, 80);
    finish_animation();
    assert(s_deck.focus_id == captured_target);
    assert(current_id() == captured_target);
    assert(strncmp(label_storage(s_cards[1].title), "replacement-", 11) == 0);
    assert(neighbor_id(2) != captured_target); /* New order is reconciled by ID. */
}

static void test_destination_and_source_expiry(void)
{
    const int ids[] = {601, 602, 603};
    reset_deck(ids, 3, 0);
    const int source = current_id();
    const int target = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(270, 80, 40);
    int after_close[] = {601, 603};
    set_raw_order(after_close, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(deck_input_state(&s_input) == DECK_INPUT_SETTLING);
    assert(s_input.settle_target_id == source && !s_input.settle_commit);
    assert(s_input.next_id == target);
    for (int i = 0; i < 12 && deck_input_state(&s_input) == DECK_INPUT_SETTLING; i++) {
        step(20);
    }
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    assert(deck_input_pointer_down(&s_input));
    assert(s_deck.focus_id == source && !reachable(target));
    pointer_release(270, 80);
    assert(deck_input_state(&s_input) == DECK_INPUT_IDLE);

    reset_deck(ids, 3, 0);
    const int expiring_source = current_id();
    pointer_press(320, 80);
    pointer_move(270, 80, 40);
    int source_closed[] = {603, 602};
    set_raw_order(source_closed, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    assert(deck_input_pointer_down(&s_input));
    assert(s_deck.focus_id == 602 && current_id() == 602);
    assert(!reachable(expiring_source));
    pointer_release(270, 80);
    assert(deck_input_state(&s_input) == DECK_INPUT_IDLE);
    assert(s_deck.focus_id == 602);
}

static void test_post_release_races_and_normal_arrival(void)
{
    const int ids[] = {650, 651, 652};
    reset_deck(ids, 3, 0);
    const int source = current_id();
    const int target = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(270, 80, 40);
    pointer_release(270, 80);
    assert(deck_input_state(&s_input) == DECK_INPUT_SETTLING);

    /* The destination may disappear between release and settle completion. */
    const int without_target[] = {source, 652};
    set_raw_order(without_target, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(deck_input_state(&s_input) == DECK_INPUT_SETTLING);
    assert(s_input.settle_target_id == source && !s_input.settle_commit);
    assert(!reachable(target));
    finish_animation();
    assert(s_deck.focus_id == source);

    reset_deck(ids, 3, 0);
    const int expiring_source = current_id();
    const int successor = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(270, 80, 40);
    pointer_release(270, 80);
    const uint32_t stale_generation = deck_input_generation(&s_input);
    const int without_source[] = {652, 651};
    set_raw_order(without_source, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(deck_input_state(&s_input) == DECK_INPUT_IDLE);
    assert(deck_input_generation(&s_input) != stale_generation);
    assert(!reachable(expiring_source));
    step(300); /* Any old animation callback must not select its stale target. */
    assert(s_deck.focus_id == successor);

    reset_deck(ids, 3, 0);
    const int normal_source = current_id();
    const int normal_target = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(270, 80, 40);
    add_notification(659, 1);
    set_normal_focus(659);
    assert(s_deck.focus_id == normal_source);
    pointer_release(270, 80);
    finish_animation();
    assert(s_deck.focus_id == normal_target);
}

static void test_card_counts_and_overflow(void)
{
    reset_deck(NULL, 0, 0);
    assert(s_deck.count == 0 && !s_deck.has_focus);
    assert(lv_obj_has_flag(s_idle, LV_OBJ_FLAG_HIDDEN) == false);
    assert(lv_obj_has_flag(s_viewport, LV_OBJ_FLAG_HIDDEN));

    const int one[] = {701};
    reset_deck(one, 1, 0);
    assert(s_deck.count == 1 && current_id() == 701);
    assert(lv_obj_get_width(s_cards[1].root) == 464);
    pointer_press(320, 80);
    pointer_move(220, 80, 200);
    pointer_release(220, 80);
    finish_animation();
    assert(s_deck.focus_id == 701);

    const int two[] = {801, 802};
    reset_deck(two, 2, 0);
    assert(current_id() == 801);
    assert(neighbor_id(0) == 802 && neighbor_id(2) == 802);
    pointer_press(320, 80);
    pointer_move(420, 80, 200);
    pointer_release(420, 80);
    finish_animation();
    assert(s_deck.focus_id == 802);
    pointer_press(320, 80);
    pointer_move(220, 80, 200);
    pointer_release(220, 80);
    finish_animation();
    assert(s_deck.focus_id == 801);

    int many[STATUS_MAX_NOTIFS];
    for (int i = 0; i < STATUS_MAX_NOTIFS; i++) {
        many[i] = 900 + i;
    }
    reset_deck(many, STATUS_MAX_NOTIFS, 7);
    assert(s_deck.count == STATUS_MAX_NOTIFS);
    assert(s_state.deck_reachable == STATUS_MAX_NOTIFS);
    assert(s_state.deck_next_id == neighbor_id(2));
    assert(strstr(label_storage(s_cards[1].position), "+7 uncached") != NULL);
    for (int i = 0; i < STATUS_MAX_NOTIFS; i++) {
        assert(reachable(many[i]));
    }
    const int reachable_target = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(220, 80, 200);
    pointer_release(220, 80);
    finish_animation();
    assert(s_deck.focus_id == reachable_target);
}

static void test_legacy_stale_and_release_latch(void)
{
    const int ids[] = {1001, 1002, 1003};
    reset_deck(ids, 3, 0);
    const int source = current_id();
    pointer_press(320, 80);
    pointer_move(270, 80, 40);
    ui_deck_show(false);
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    ui_deck_show(true);
    assert(current_id() == source);
    pointer_move(220, 80, 40);
    pointer_release(220, 80);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(deck_input_state(&s_input) == DECK_INPUT_IDLE);
    assert(s_deck.focus_id == source); /* Hidden-view gesture did not leak. */

    const int next = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(220, 80, 200);
    pointer_release(220, 80);
    finish_animation();
    assert(s_deck.focus_id == next);

    const int stale_source = current_id();
    pointer_press(320, 80);
    pointer_move(270, 80, 40);
    s_host_connected = false;
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(deck_input_state(&s_input) == DECK_INPUT_IGNORED);
    assert(lv_obj_has_flag(s_viewport, LV_OBJ_FLAG_HIDDEN));
    s_host_connected = true;
    s_state.last_rx_us = s_now_us;
    pointer_release(270, 80);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(deck_input_state(&s_input) == DECK_INPUT_IDLE);
    assert(s_deck.focus_id == stale_source);
}

int main(void)
{
    memset(&s_state, 0, sizeof(s_state));
    s_state.notifs = s_notifs;
    s_state.notif_capacity = STATUS_MAX_NOTIFS;
    s_state.cache_limit = STATUS_MAX_NOTIFS;
    s_state.got_sync = true;
    s_state.last_rx_us = s_now_us;

    lv_init();
    s_display = lv_display_create(DISPLAY_WIDTH, DISPLAY_HEIGHT);
    assert(s_display != NULL);
    lv_display_set_color_format(s_display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(s_display, s_draw_buffer, NULL,
                           sizeof(s_draw_buffer), LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(s_display, display_flush);
    s_pointer = lv_indev_create();
    assert(s_pointer != NULL);
    lv_indev_set_type(s_pointer, LV_INDEV_TYPE_POINTER);
    lv_indev_set_display(s_pointer, s_display);
    lv_indev_set_read_cb(s_pointer, pointer_read);
    lv_indev_set_mode(s_pointer, LV_INDEV_MODE_EVENT);

    ui_deck_init(lv_display_get_screen_active(s_display),
                 &lv_font_montserrat_12, &lv_font_montserrat_14);
    const int ids[] = {101, 102, 103};
    set_raw_order(ids, 3);
    ui_deck_show(true);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    repaint();

    test_geometry_and_slow_directions();
    test_cancel_flick_body_peek_and_dismiss();
    test_axis_rail_and_peek_drag();
    test_arrivals_replacement_and_sync_order();
    test_destination_and_source_expiry();
    test_post_release_races_and_normal_arrival();
    test_card_counts_and_overflow();
    test_legacy_stale_and_release_latch();

    lv_indev_delete(s_pointer);
    lv_display_delete(s_display);
    lv_deinit();
    puts("native LVGL deck: pointer gestures, reconciliation, and card-count transitions pass");
    return 0;
}
