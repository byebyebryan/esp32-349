/*
 * Native headless replay and SDL viewer for the production LVGL deck. This
 * translation unit includes ui_deck.c so regression checks can inspect its
 * private rendered slots while every gesture enters through lv_indev.
 * state/link/RTC/timer/dismiss transport below are explicit host substitutions.
 */
#include <assert.h>
#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include <SDL.h>

#include "lvgl.h"
#include "state.h"
#include "ui_fonts.h"
#ifdef NATIVE_PROTOCOL
#include "proto.h"
#endif

#include "../../main/ui_deck.c"

#define DISPLAY_WIDTH 640
#define DISPLAY_HEIGHT 172
#define FRAME_PIXELS (DISPLAY_WIDTH * DISPLAY_HEIGHT)

#ifdef NATIVE_PROTOCOL
#define s_state (*state_get())
static status_notif_t *s_notifs;
#else
static status_state_t s_state;
static status_notif_t s_notifs[STATUS_MAX_NOTIFS];
#endif
static int64_t s_now_us = 1000000;
static bool s_host_connected = true;
static bool s_test_clock_valid;
static int s_dismiss_count;
static int s_last_dismiss_id = -1;
#ifndef NATIVE_PROTOCOL
static int s_activate_count;
static int s_last_activate_id = -1;
static int s_last_activate_revision = -1;
#endif
static lv_display_t *s_display;
static lv_indev_t *s_pointer;
static lv_point_t s_pointer_point;
static lv_indev_state_t s_pointer_state = LV_INDEV_STATE_RELEASED;
static uint16_t s_draw_buffer[FRAME_PIXELS];
static uint16_t s_framebuffer[FRAME_PIXELS];
static const char *s_artifact_dir;
static FILE *s_trace_file;
static int s_replay_commands;
static SDL_Window *s_window;
static SDL_Renderer *s_renderer;
static SDL_Texture *s_texture;
static bool s_mouse_down;
static status_ui_fonts_t s_ui_fonts;

static void viewer_present(void);

static void tracef(const char *format, ...)
{
    if (s_trace_file == NULL) {
        return;
    }
    va_list args;
    va_start(args, format);
    vfprintf(s_trace_file, format, args);
    va_end(args);
    fputc('\n', s_trace_file);
    fflush(s_trace_file);
}

static bool make_directory_tree(const char *path)
{
    if (path == NULL || path[0] == '\0' || strlen(path) >= PATH_MAX) {
        return false;
    }
    char copy[PATH_MAX];
    snprintf(copy, sizeof(copy), "%s", path);
    for (char *cursor = copy + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') {
            continue;
        }
        *cursor = '\0';
        if (mkdir(copy, 0775) != 0 && errno != EEXIST) {
            return false;
        }
        *cursor = '/';
    }
    if (mkdir(copy, 0775) != 0 && errno != EEXIST) {
        return false;
    }
    struct stat info;
    return stat(copy, &info) == 0 && S_ISDIR(info.st_mode);
}

static bool artifact_path(char *out, size_t size, const char *name)
{
    if (s_artifact_dir == NULL || name == NULL || name[0] == '\0' ||
        strchr(name, '/') != NULL || strchr(name, '\\') != NULL ||
        strcmp(name, ".") == 0 || strcmp(name, "..") == 0) {
        return false;
    }
    const int written = snprintf(out, size, "%s/%s", s_artifact_dir, name);
    return written >= 0 && (size_t)written < size;
}

#ifndef NATIVE_PROTOCOL
void state_lock(void) {}
void state_unlock(void) {}
status_state_t *state_get(void) { return &s_state; }
#endif

bool link_host_connected(void) { return s_host_connected; }

int64_t esp_timer_get_time(void) { return s_now_us; }

#ifndef NATIVE_PROTOCOL
rtc_source_t rtc_pcf_get_local(struct tm *out)
{
    if (!s_test_clock_valid) {
        return RTC_SOURCE_NONE;
    }
    *out = (struct tm){
        .tm_year = 126,
        .tm_mon = 8,
        .tm_mday = 26,
        .tm_wday = 6,
        .tm_hour = 14,
        .tm_min = 35,
        .tm_sec = 0,
    };
    return RTC_SOURCE_FALLBACK;
}
#endif

#ifndef NATIVE_PROTOCOL
void proto_send_input_dismiss(int id)
{
    s_dismiss_count++;
    s_last_dismiss_id = id;
}

void proto_send_input_browse(bool home, int generation)
{
    (void)home;
    (void)generation;
}

bool proto_send_input_activate(int id, int open_revision)
{
    status_notif_t *notif = NULL;
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == id) {
            notif = &s_state.notifs[i];
            break;
        }
    }
    if (!notif || notif->open_revision != open_revision ||
        !state_action_open_enabled(&s_state, notif, s_now_us)) return false;
    s_activate_count++;
    s_last_activate_id = id;
    s_last_activate_revision = open_revision;
    s_state.action_next_request++;
    s_state.action_pending = true;
    s_state.action_pending_session = s_state.grouped_session;
    s_state.action_pending_boot_id = 1;
    s_state.action_pending_id = id;
    s_state.action_pending_open_rev = open_revision;
    s_state.action_pending_request = s_state.action_next_request;
    s_state.action_pending_deadline_us = s_now_us + 3000000;
    return true;
}

bool state_action_open_enabled(const status_state_t *state,
                               const status_notif_t *notif, int64_t now_us)
{
    return state && notif && state->actions_enabled && state->grouped_enabled &&
        notif->valid && notif->open_ready && !state->action_pending &&
        !state->action_request_exhausted && now_us >= state->action_cooldown_until_us &&
        !(state->action_blocked && state->action_blocked_id == notif->id &&
          state->action_blocked_open_rev == notif->open_revision);
}

void state_action_tick(int64_t now_us)
{
    if (s_state.action_pending && now_us >= s_state.action_pending_deadline_us) {
        s_state.action_blocked = true;
        s_state.action_blocked_id = s_state.action_pending_id;
        s_state.action_blocked_open_rev = s_state.action_pending_open_rev;
        s_state.action_pending = false;
        s_state.action_feedback = STATUS_ACTION_FEEDBACK_NO_CONFIRMATION;
        s_state.action_feedback_id = s_state.action_pending_id;
        s_state.action_feedback_open_rev = s_state.action_pending_open_rev;
        s_state.action_feedback_until_us = now_us + 2000000;
    }
    if (s_state.action_feedback != STATUS_ACTION_FEEDBACK_NONE &&
        now_us >= s_state.action_feedback_until_us) {
        s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
    }
}

void state_action_disconnect(void)
{
    if (s_state.action_pending) {
        s_state.action_blocked = true;
        s_state.action_blocked_id = s_state.action_pending_id;
        s_state.action_blocked_open_rev = s_state.action_pending_open_rev;
        s_state.action_pending = false;
    }
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
#endif

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
    viewer_present();
}

static void repaint(void);

static void repaint(void)
{
    lv_refr_now(s_display);
    viewer_present();
}

static bool write_ppm(const char *path)
{
    repaint();
    FILE *file = fopen(path, "wb");
    if (file == NULL) {
        return false;
    }
    if (fprintf(file, "P6\n%d %d\n255\n", DISPLAY_WIDTH, DISPLAY_HEIGHT) < 0) {
        fclose(file);
        return false;
    }
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
        if (fwrite(pixel, sizeof(pixel), 1, file) != 1) {
            fclose(file);
            return false;
        }
    }
    return fclose(file) == 0;
}

static bool capture_frame(const char *name)
{
    char path[PATH_MAX];
    if (!artifact_path(path, sizeof(path), name)) {
        return false;
    }
    const size_t name_length = strlen(name);
    if (name_length < 4 || strcmp(name + name_length - 4, ".ppm") != 0) {
        const int written = snprintf(path, sizeof(path), "%s/%s.ppm",
                                     s_artifact_dir, name);
        if (written < 0 || (size_t)written >= sizeof(path)) {
            return false;
        }
    }
    if (!write_ppm(path)) {
        return false;
    }
    tracef("capture path=%s", path);
    return true;
}

static void step(uint32_t elapsed_ms)
{
    s_now_us += (int64_t)elapsed_ms * 1000;
    lv_tick_inc(elapsed_ms);
    lv_timer_handler();
    ui_deck_tick(0);
    tracef("advance ms=%u focus=%d offset=%d input=%d", elapsed_ms,
           s_deck.has_focus ? s_deck.focus_id : 0, s_offset,
           (int)deck_input_state(&s_input));
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
    tracef("pointer x=%d y=%d state=%s focus=%d offset=%d input=%d", x, y,
           pressed ? "down" : "up", s_deck.has_focus ? s_deck.focus_id : 0,
           s_offset, (int)deck_input_state(&s_input));
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
    memset(s_notifs, 0, sizeof(status_notif_t) * STATUS_MAX_NOTIFS);
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
    tracef("state order count=%d", count);
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
    tracef("focus normal id=%d", id);
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
    tracef("focus critical id=%d", id);
}

static void add_notification(int id, int urgency)
{
    assert(s_state.notif_count < STATUS_MAX_NOTIFS);
    set_record(&s_notifs[s_state.notif_count], id, urgency,
               "new arrival", "arrival body");
    s_state.notif_count++;
    s_state.last_rx_us = s_now_us;
    tracef("notification id=%d urgency=%d count=%d", id, urgency,
           s_state.notif_count);
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
    assert(capture_frame("at-rest"));

    const int next = neighbor_id(2);
    pointer_press(320, 80);
    pointer_move(300, 80, 80);
    pointer_move(220, 80, 320);
    assert(s_offset == -100);
    assert(s_cards[2].id == next); /* Binding remains fixed while dragging. */
    assert(capture_frame("mid-drag"));
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

static void fixture_init(bool demo)
{
    s_now_us = 1000000;
    s_host_connected = true;
    s_test_clock_valid = false;
    s_dismiss_count = 0;
    s_last_dismiss_id = -1;
#ifndef NATIVE_PROTOCOL
    s_activate_count = 0;
    s_last_activate_id = -1;
    s_last_activate_revision = -1;
#endif
    s_pointer_state = LV_INDEV_STATE_RELEASED;
    s_mouse_down = false;
#ifdef NATIVE_PROTOCOL
    state_init();
    s_notifs = state_get()->notifs;
#else
    memset(&s_state, 0, sizeof(s_state));
    memset(s_notifs, 0, sizeof(status_notif_t) * STATUS_MAX_NOTIFS);
    s_state.notifs = s_notifs;
    s_state.notif_capacity = STATUS_MAX_NOTIFS;
    s_state.cache_limit = STATUS_MAX_NOTIFS;
    s_state.got_sync = true;
    s_state.last_rx_us = s_now_us;
#endif

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

    status_ui_fonts_init(&s_ui_fonts);
    ui_deck_init(lv_display_get_screen_active(s_display),
                 &s_ui_fonts.small, &s_ui_fonts.body);
    ui_deck_show(true);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    repaint();

    if (demo) {
        set_record(&s_notifs[0], 301, 1, "Meeting follow-up",
                   "Notes are ready in the shared project folder.");
        set_record(&s_notifs[1], 302, 1, "Pull request review",
                   "A teammate requested a review of status349.");
        set_record(&s_notifs[2], 303, 1, "Build finished",
                   "ESP-IDF build completed with no warnings.");
        s_state.notif_count = 3;
        s_state.last_rx_us = s_now_us;
        ui_deck_tick(STATE_DIRTY_NOTIF);
        repaint();
    }
    tracef("fixture initialized demo=%d", demo ? 1 : 0);
}

static void fixture_shutdown(void)
{
    if (s_pointer != NULL) {
        lv_indev_delete(s_pointer);
        s_pointer = NULL;
    }
    if (s_display != NULL) {
        lv_display_delete(s_display);
        s_display = NULL;
    }
    lv_deinit();
}

static void fixture_enable_grouped_demo(void)
{
    s_state.grouped_enabled = true;
    s_state.grouped_session = 1;
    s_state.presentation = (status_presentation_t){
        .generation = 1,
        .id = 303,
        .urgency = 1,
        .deadline_us = s_now_us + 10 * 1000 * 1000,
        .active = true,
    };
    ui_deck_tick(STATE_DIRTY_NOTIF);
    repaint();
    tracef("fixture grouped demo session=1 present=303");
}

static void run_regression_suite(void)
{
    tracef("case geometry_and_slow_directions");
    test_geometry_and_slow_directions();
    tracef("case cancel_flick_body_peek_and_dismiss");
    test_cancel_flick_body_peek_and_dismiss();
    tracef("case axis_rail_and_peek_drag");
    test_axis_rail_and_peek_drag();
    tracef("case arrivals_replacement_and_sync_order");
    test_arrivals_replacement_and_sync_order();
    tracef("case destination_and_source_expiry");
    test_destination_and_source_expiry();
    tracef("case post_release_races_and_normal_arrival");
    test_post_release_races_and_normal_arrival();
    tracef("case card_counts_and_overflow");
    test_card_counts_and_overflow();
    tracef("case legacy_stale_and_release_latch");
    test_legacy_stale_and_release_latch();
}

static bool json_integer(const cJSON *object, const char *name, int *out,
                         bool required)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    if (value == NULL) {
        return !required;
    }
    if (!cJSON_IsNumber(value) || value->valuedouble < (double)INT_MIN ||
        value->valuedouble > (double)INT_MAX ||
        (double)value->valueint != value->valuedouble) {
        return false;
    }
    *out = value->valueint;
    return true;
}

static bool json_boolean(const cJSON *object, const char *name, bool *out)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    if (value == NULL) {
        return true;
    }
    if (!cJSON_IsBool(value)) {
        return false;
    }
    *out = cJSON_IsTrue(value);
    return true;
}

static const char *json_string(const cJSON *object, const char *name,
                               const char *fallback, bool required)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    if (value == NULL) {
        return required ? NULL : fallback;
    }
    return cJSON_IsString(value) ? value->valuestring : NULL;
}

static bool parse_record(const cJSON *value, status_notif_t *out)
{
    int id = 0;
    int urgency_value = 1;
    if (!cJSON_IsObject(value) || !json_integer(value, "id", &id, true) ||
        !json_integer(value, "urgency", &urgency_value, false) ||
        urgency_value < 0 || urgency_value > 2) {
        return false;
    }
    const char *app = json_string(value, "app", "Replay", false);
    const char *summary = json_string(value, "summary", "Replay notification", false);
    const char *body = json_string(value, "body", "Replay body", false);
    if (app == NULL || summary == NULL || body == NULL) {
        return false;
    }
    memset(out, 0, sizeof(*out));
    out->valid = true;
    out->id = id;
    out->urgency = urgency_value;
    snprintf(out->app, sizeof(out->app), "%s", app);
    snprintf(out->summary, sizeof(out->summary), "%s", summary);
    snprintf(out->body, sizeof(out->body), "%s", body);
    return true;
}

/* Replay arrays are newest first, matching the deck's visible order. */
static bool load_json_records(const cJSON *records)
{
    if (!cJSON_IsArray(records) || cJSON_GetArraySize(records) > STATUS_MAX_NOTIFS) {
        return false;
    }
    memset(s_notifs, 0, sizeof(status_notif_t) * STATUS_MAX_NOTIFS);
    const int count = cJSON_GetArraySize(records);
    for (int i = 0; i < count; i++) {
        status_notif_t parsed;
        if (!parse_record(cJSON_GetArrayItem(records, i), &parsed)) {
            return false;
        }
        s_notifs[count - 1 - i] = parsed;
    }
    s_state.notifs = s_notifs;
    s_state.notif_capacity = STATUS_MAX_NOTIFS;
    s_state.cache_limit = STATUS_MAX_NOTIFS;
    s_state.notif_count = count;
    s_state.got_sync = true;
    s_state.last_rx_us = s_now_us;
    tracef("state records count=%d", count);
    return true;
}

static bool apply_json_state(const cJSON *state)
{
    if (!cJSON_IsObject(state)) {
        return false;
    }
    const cJSON *records = cJSON_GetObjectItemCaseSensitive(state, "notifications");
    if (records != NULL && !load_json_records(records)) {
        return false;
    }
    bool connected = s_host_connected;
    if (!json_boolean(state, "connected", &connected)) {
        return false;
    }
    int overflow = s_state.notif_overflow;
    int normal_focus = 0;
    int critical_focus = 0;
    if (!json_integer(state, "overflow", &overflow, false) || overflow < 0 ||
        !json_integer(state, "focus_id", &normal_focus, false) ||
        !json_integer(state, "critical_id", &critical_focus, false)) {
        return false;
    }
    s_host_connected = connected;
    s_state.notif_overflow = overflow;
    if (cJSON_GetObjectItemCaseSensitive(state, "focus_id") != NULL) {
        s_state.notif_focus_seq++;
        s_state.notif_focus_id = normal_focus;
        s_state.notif_focus_urgency = 1;
        s_state.notif_focus_us = s_now_us;
    }
    if (cJSON_GetObjectItemCaseSensitive(state, "critical_id") != NULL) {
        s_state.notif_critical_seq++;
        s_state.notif_critical_id = critical_focus;
        s_state.notif_critical_us = s_now_us;
        for (int i = 0; i < s_state.notif_count; i++) {
            if (s_state.notifs[i].id == critical_focus) {
                s_state.notifs[i].urgency = 2;
            }
        }
    }
    if (connected) {
        s_state.last_rx_us = s_now_us;
    }
    ui_deck_tick(STATE_DIRTY_NOTIF);
    repaint();
    tracef("state update connected=%d overflow=%d focus=%d critical=%d", connected,
           overflow, normal_focus, critical_focus);
    return true;
}

static bool apply_json_notification(const cJSON *command)
{
    status_notif_t parsed;
    if (!parse_record(command, &parsed)) {
        return false;
    }
    int slot = -1;
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_notifs[i].id == parsed.id) {
            slot = i;
            break;
        }
    }
    if (slot < 0) {
        if (s_state.notif_count >= STATUS_MAX_NOTIFS) {
            return false;
        }
        slot = s_state.notif_count++;
    }
    s_notifs[slot] = parsed;
    s_state.last_rx_us = s_now_us;
    if (parsed.urgency >= 2) {
        set_critical_focus(parsed.id);
    } else {
        set_normal_focus(parsed.id);
    }
    tracef("notification update id=%d urgency=%d", parsed.id, parsed.urgency);
    return true;
}

static const char *input_state_name(void)
{
    switch (deck_input_state(&s_input)) {
    case DECK_INPUT_IDLE: return "idle";
    case DECK_INPUT_PRESSED: return "pressed";
    case DECK_INPUT_DRAGGING: return "dragging";
    case DECK_INPUT_SETTLING: return "settling";
    case DECK_INPUT_BUTTON_DISMISS: return "dismiss";
    case DECK_INPUT_BUTTON_OPEN: return "open";
    case DECK_INPUT_BUTTON_PEEK: return "peek";
    case DECK_INPUT_IGNORED: return "ignored";
    }
    return "unknown";
}

static bool assert_replay_value(const cJSON *command)
{
    const char *field = json_string(command, "field", NULL, true);
    const cJSON *expected = cJSON_GetObjectItemCaseSensitive(command, "equals");
    if (field == NULL || expected == NULL) {
        return false;
    }
    if (strcmp(field, "input_state") == 0) {
        return cJSON_IsString(expected) &&
               strcmp(input_state_name(), expected->valuestring) == 0;
    }
    if (strcmp(field, "visible") == 0 || strcmp(field, "connected") == 0) {
        const bool actual = strcmp(field, "visible") == 0 ? s_visible : s_host_connected;
        return cJSON_IsBool(expected) && actual == cJSON_IsTrue(expected);
    }
    int actual = 0;
    if (strcmp(field, "focus_id") == 0) {
        actual = s_deck.has_focus ? s_deck.focus_id : 0;
    } else if (strcmp(field, "count") == 0) {
        actual = s_deck.count;
    } else if (strcmp(field, "offset") == 0) {
        actual = s_offset;
    } else if (strcmp(field, "dismiss_count") == 0) {
        actual = s_dismiss_count;
    } else if (strcmp(field, "last_dismiss_id") == 0) {
        actual = s_last_dismiss_id;
    } else if (strcmp(field, "stale") == 0) {
        return cJSON_IsBool(expected) && s_state.deck_stale == cJSON_IsTrue(expected);
    } else {
        return false;
    }
    return cJSON_IsNumber(expected) && (double)actual == expected->valuedouble;
}

static bool replay_command(const cJSON *command)
{
    if (!cJSON_IsObject(command)) {
        return false;
    }
    const char *type = json_string(command, "type", NULL, true);
    if (type == NULL) {
        return false;
    }
    s_replay_commands++;
    tracef("command %d type=%s", s_replay_commands, type);
    if (strcmp(type, "press") == 0 || strcmp(type, "move") == 0 ||
        strcmp(type, "release") == 0) {
        int x = 0;
        int y = 0;
        int elapsed = 1;
        if (!json_integer(command, "x", &x, true) ||
            !json_integer(command, "y", &y, true) ||
            !json_integer(command, "ms", &elapsed, false) || elapsed < 0) {
            return false;
        }
        if (strcmp(type, "press") == 0) {
            if (s_pointer_state != LV_INDEV_STATE_RELEASED) {
                return false;
            }
            pointer_sample(x, y, true, (uint32_t)elapsed);
        } else if (strcmp(type, "move") == 0) {
            if (s_pointer_state != LV_INDEV_STATE_PRESSED) {
                return false;
            }
            pointer_sample(x, y, true, (uint32_t)elapsed);
        } else {
            if (s_pointer_state != LV_INDEV_STATE_PRESSED) {
                return false;
            }
            pointer_sample(x, y, false, (uint32_t)elapsed);
        }
        return true;
    }
    if (strcmp(type, "advance") == 0) {
        int elapsed = 0;
        if (!json_integer(command, "ms", &elapsed, true) || elapsed < 0) {
            return false;
        }
        step((uint32_t)elapsed);
        return true;
    }
    if (strcmp(type, "notification") == 0) {
        return apply_json_notification(command);
    }
    if (strcmp(type, "state") == 0) {
        return apply_json_state(command);
    }
    if (strcmp(type, "screenshot") == 0) {
        const char *name = json_string(command, "name", NULL, true);
        return name != NULL && capture_frame(name);
    }
    if (strcmp(type, "assert") == 0) {
        const bool passed = assert_replay_value(command);
        if (!passed) {
            const char *field = json_string(command, "field", "?", false);
            tracef("assertion failed field=%s", field);
        }
        return passed;
    }
    return false;
}

static bool run_replay(const char *path)
{
    FILE *input = fopen(path, "rb");
    if (input == NULL || fseek(input, 0, SEEK_END) != 0) {
        if (input != NULL) {
            fclose(input);
        }
        return false;
    }
    const long size = ftell(input);
    if (size < 0 || size > 1024 * 1024 || fseek(input, 0, SEEK_SET) != 0) {
        fclose(input);
        return false;
    }
    char *contents = malloc((size_t)size + 1);
    if (contents == NULL) {
        fclose(input);
        return false;
    }
    const size_t read_count = fread(contents, 1, (size_t)size, input);
    const bool read_ok = read_count == (size_t)size && fclose(input) == 0;
    if (!read_ok) {
        free(contents);
        return false;
    }
    contents[size] = '\0';
    cJSON *root = cJSON_Parse(contents);
    free(contents);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        tracef("replay parse failed path=%s", path);
        return false;
    }
    const cJSON *initial = cJSON_GetObjectItemCaseSensitive(root, "initial");
    if (initial != NULL && !apply_json_state(initial)) {
        cJSON_Delete(root);
        tracef("replay initial state invalid");
        return false;
    }
    const cJSON *commands = cJSON_GetObjectItemCaseSensitive(root, "commands");
    bool passed = cJSON_IsArray(commands);
    const int count = passed ? cJSON_GetArraySize(commands) : 0;
    for (int i = 0; passed && i < count; i++) {
        passed = replay_command(cJSON_GetArrayItem(commands, i));
        if (!passed) {
            tracef("command failed index=%d", i);
        }
    }
    cJSON_Delete(root);
    return passed;
}

static void viewer_present(void)
{
    if (s_renderer == NULL || s_texture == NULL) {
        return;
    }
    SDL_UpdateTexture(s_texture, NULL, s_framebuffer,
                      DISPLAY_WIDTH * (int)sizeof(uint16_t));
    SDL_RenderClear(s_renderer);
    SDL_RenderCopy(s_renderer, s_texture, NULL, NULL);
    SDL_RenderPresent(s_renderer);
}

static bool viewer_start(void)
{
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return false;
    }
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "0");
    s_window = SDL_CreateWindow("349 Status native LVGL",
                                SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                DISPLAY_WIDTH * 2, DISPLAY_HEIGHT * 2, 0);
    if (s_window == NULL) {
        fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
        return false;
    }
    s_renderer = SDL_CreateRenderer(s_window, -1, SDL_RENDERER_SOFTWARE);
    if (s_renderer == NULL) {
        s_renderer = SDL_CreateRenderer(s_window, -1, 0);
    }
    if (s_renderer == NULL) {
        fprintf(stderr, "SDL_CreateRenderer: %s\n", SDL_GetError());
        return false;
    }
    s_texture = SDL_CreateTexture(s_renderer, SDL_PIXELFORMAT_RGB565,
                                  SDL_TEXTUREACCESS_STREAMING,
                                  DISPLAY_WIDTH, DISPLAY_HEIGHT);
    if (s_texture == NULL) {
        fprintf(stderr, "SDL_CreateTexture: %s\n", SDL_GetError());
        return false;
    }
    SDL_SetTextureBlendMode(s_texture, SDL_BLENDMODE_NONE);
    return true;
}

static void viewer_stop(void)
{
    if (s_texture != NULL) {
        SDL_DestroyTexture(s_texture);
        s_texture = NULL;
    }
    if (s_renderer != NULL) {
        SDL_DestroyRenderer(s_renderer);
        s_renderer = NULL;
    }
    if (s_window != NULL) {
        SDL_DestroyWindow(s_window);
        s_window = NULL;
    }
    SDL_Quit();
}

static void viewer_coordinates(int window_x, int window_y, int *x, int *y)
{
    int width = DISPLAY_WIDTH;
    int height = DISPLAY_HEIGHT;
    SDL_GetWindowSize(s_window, &width, &height);
    *x = width > 0 ? window_x * DISPLAY_WIDTH / width : window_x;
    *y = height > 0 ? window_y * DISPLAY_HEIGHT / height : window_y;
    if (*x < 0) *x = 0;
    if (*x >= DISPLAY_WIDTH) *x = DISPLAY_WIDTH - 1;
    if (*y < 0) *y = 0;
    if (*y >= DISPLAY_HEIGHT) *y = DISPLAY_HEIGHT - 1;
}

static void viewer_loop(uint32_t duration_ms)
{
    const uint64_t start = SDL_GetTicks64();
    uint64_t previous = start;
    bool running = true;
    while (running) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT) {
                running = false;
            } else if (event.type == SDL_MOUSEBUTTONDOWN &&
                       event.button.button == SDL_BUTTON_LEFT && !s_mouse_down) {
                int x;
                int y;
                viewer_coordinates(event.button.x, event.button.y, &x, &y);
                s_mouse_down = true;
                pointer_sample(x, y, true, 0);
            } else if (event.type == SDL_MOUSEMOTION && s_mouse_down) {
                int x;
                int y;
                viewer_coordinates(event.motion.x, event.motion.y, &x, &y);
                pointer_sample(x, y, true, 0);
            } else if (event.type == SDL_MOUSEBUTTONUP &&
                       event.button.button == SDL_BUTTON_LEFT && s_mouse_down) {
                int x;
                int y;
                viewer_coordinates(event.button.x, event.button.y, &x, &y);
                s_mouse_down = false;
                pointer_sample(x, y, false, 0);
            }
        }
        const uint64_t now = SDL_GetTicks64();
        const uint64_t elapsed = now - previous;
        previous = now;
        if (elapsed > 0) {
            step(elapsed > UINT32_MAX ? UINT32_MAX : (uint32_t)elapsed);
        } else {
            lv_timer_handler();
        }
        if (duration_ms > 0 && now - start >= duration_ms) {
            running = false;
        }
        SDL_Delay(16);
    }
}

static bool write_result(const char *mode, bool passed)
{
    char path[PATH_MAX];
    if (!artifact_path(path, sizeof(path), "result.json")) {
        return false;
    }
    FILE *output = fopen(path, "wb");
    if (output == NULL) {
        return false;
    }
    const int written = fprintf(output,
                                "{\"mode\":\"%s\",\"status\":\"%s\",\"commands\":%d}\n",
                                mode, passed ? "passed" : "failed", s_replay_commands);
    const int close_result = fclose(output);
    return written > 0 && close_result == 0;
}

typedef enum {
    RUN_SELF_TEST,
    RUN_REPLAY,
    RUN_VIEWER,
} run_mode_t;

typedef struct {
    run_mode_t mode;
    const char *replay_path;
    const char *artifacts;
    const char *capture_name;
    uint32_t viewer_duration_ms;
    bool grouped_demo;
} run_options_t;

static void usage(const char *program)
{
    fprintf(stderr,
            "Usage: %s (--self-test | --replay FILE | --viewer) --artifacts DIR "
            "[--viewer-duration-ms N] [--capture NAME] [--grouped]\n", program);
}

static bool parse_options(int argc, char **argv, run_options_t *options)
{
    memset(options, 0, sizeof(*options));
    options->mode = RUN_SELF_TEST;
    options->capture_name = "viewer-frame";
    bool mode_selected = false;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return false;
        } else if (strcmp(argv[i], "--self-test") == 0) {
            options->mode = RUN_SELF_TEST;
            mode_selected = true;
        } else if (strcmp(argv[i], "--viewer") == 0) {
            options->mode = RUN_VIEWER;
            mode_selected = true;
        } else if (strcmp(argv[i], "--grouped") == 0) {
            options->grouped_demo = true;
        } else if (strcmp(argv[i], "--replay") == 0 && i + 1 < argc) {
            options->mode = RUN_REPLAY;
            options->replay_path = argv[++i];
            mode_selected = true;
        } else if (strcmp(argv[i], "--artifacts") == 0 && i + 1 < argc) {
            options->artifacts = argv[++i];
        } else if (strcmp(argv[i], "--capture") == 0 && i + 1 < argc) {
            options->capture_name = argv[++i];
        } else if (strcmp(argv[i], "--viewer-duration-ms") == 0 && i + 1 < argc) {
            char *end = NULL;
            const unsigned long value = strtoul(argv[++i], &end, 10);
            if (end == argv[i] || *end != '\0' || value > UINT32_MAX) {
                return false;
            }
            options->viewer_duration_ms = (uint32_t)value;
        } else {
            return false;
        }
    }
    if (!mode_selected || options->artifacts == NULL ||
        (options->mode == RUN_REPLAY && options->replay_path == NULL) ||
        (options->grouped_demo && options->mode != RUN_VIEWER)) {
        return false;
    }
    return true;
}

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        usage(argv[0]);
        return 0;
    }
    run_options_t options;
    if (!parse_options(argc, argv, &options)) {
        usage(argv[0]);
        return 2;
    }
    if (!make_directory_tree(options.artifacts)) {
        fprintf(stderr, "Cannot create artifact directory: %s\n", options.artifacts);
        return 2;
    }
    s_artifact_dir = options.artifacts;
    char trace_path[PATH_MAX];
    if (!artifact_path(trace_path, sizeof(trace_path), "native-ui.trace") ||
        (s_trace_file = fopen(trace_path, "wb")) == NULL) {
        fprintf(stderr, "Cannot open trace under %s\n", options.artifacts);
        return 2;
    }
    const char *mode_name = options.mode == RUN_SELF_TEST ? "self-test" :
                            options.mode == RUN_REPLAY ? "replay" : "viewer";
    tracef("run mode=%s", mode_name);

    bool passed = true;
    if (options.mode == RUN_VIEWER) {
        passed = viewer_start();
    }
    if (passed) {
        fixture_init(options.mode == RUN_VIEWER);
        if (options.mode == RUN_VIEWER && options.grouped_demo) {
            fixture_enable_grouped_demo();
        }
        if (options.mode == RUN_SELF_TEST) {
            run_regression_suite();
            puts("native LVGL deck regression: pass");
        } else if (options.mode == RUN_REPLAY) {
            passed = run_replay(options.replay_path);
            printf("native LVGL replay %s: %s (%d commands)\n",
                   options.replay_path, passed ? "pass" : "fail", s_replay_commands);
        } else {
            passed = capture_frame(options.capture_name);
            if (passed) {
                viewer_loop(options.viewer_duration_ms);
            }
            puts("native LVGL SDL viewer stopped");
        }
        fixture_shutdown();
    }
    if (options.mode == RUN_VIEWER) {
        viewer_stop();
    }
    tracef("result status=%s", passed ? "passed" : "failed");
    const bool result_written = write_result(mode_name, passed);
    fclose(s_trace_file);
    s_trace_file = NULL;
    if (!result_written) {
        fprintf(stderr, "Cannot write result.json under %s\n", options.artifacts);
        return 2;
    }
    return passed ? 0 : 1;
}
