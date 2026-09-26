#include "ui_deck.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "deck.h"
#include "esp_timer.h"
#include "link.h"
#include "proto.h"
#include "rtc.h"
#include "state.h"

LV_FONT_DECLARE(status_text_20);
LV_FONT_DECLARE(status_text_22);
LV_FONT_DECLARE(status_clock_80);

#define BACKGROUND 0x0B1119
#define RAIL 0x111B26
#define SURFACE 0x17232E
#define FOREGROUND 0xEDF3F7
#define SECONDARY 0x9DAFBE
#define ACCENT 0x80C6D2
#define WARNING 0xE9B66A
#define CRITICAL 0xF07C86

static lv_obj_t *s_root, *s_rail_header, *s_rail_clock, *s_rail_footer;
static lv_obj_t *s_metric_names[4], *s_metric_values[4];
static lv_obj_t *s_idle, *s_idle_clock, *s_date, *s_message, *s_idle_transient;
static lv_obj_t *s_primary, *s_accent, *s_app, *s_title, *s_body, *s_position, *s_dismiss;
static lv_obj_t *s_peek, *s_peek_app, *s_peek_title;
static const lv_font_t *s_small, *s_meta;
static deck_t s_deck;
static uint32_t s_seen_focus_seq;
static uint32_t s_seen_critical_seq;
static status_dashboard_t s_previous_dashboard;
static bool s_have_dashboard;
static char s_transient[48];
static int64_t s_transient_until;
static bool s_visible;
/* LVGL's DOT mode modifies its displayed string. Keep the supplied text so
 * an ellipsized card does not get allocated and redrawn every 100 ms. */
static char s_label_text[24][STATUS_NOTIF_BODY_MAX];
static int s_label_count;

typedef struct {
    status_dashboard_t dashboard;
    status_notif_t foreground, next;
    int overflow;
    bool stale;
} snapshot_t;

static void hide(lv_obj_t *obj, bool hidden)
{
    if (hidden) {
        lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
    }
}

static void text(lv_obj_t *label, const char *value)
{
    char *previous = lv_obj_get_user_data(label);
    if (strcmp(previous, value) != 0) {
        strlcpy(previous, value, STATUS_NOTIF_BODY_MAX);
        lv_label_set_text(label, value);
    }
}

static void text_color(lv_obj_t *obj, uint32_t color)
{
    const lv_color_t wanted = lv_color_hex(color);
    if (!lv_color_eq(lv_obj_get_style_text_color(obj, 0), wanted)) {
        lv_obj_set_style_text_color(obj, wanted, 0);
    }
}

static lv_obj_t *box(lv_obj_t *parent, int x, int y, int w, int h, uint32_t color, int radius)
{
    lv_obj_t *obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(obj, radius, 0);
    return obj;
}

static lv_obj_t *label(lv_obj_t *parent, int x, int y, int w, int h,
                       const lv_font_t *font, uint32_t color, const char *value)
{
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_DOT);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    LV_ASSERT(s_label_count < 24);
    char *stored = s_label_text[s_label_count++];
    strlcpy(stored, value, STATUS_NOTIF_BODY_MAX);
    lv_obj_set_user_data(obj, stored);
    lv_label_set_text(obj, value);
    return obj;
}

static bool locally_hidden(const status_state_t *st, int id)
{
    for (int i = 0; i < st->hidden_count; i++) {
        if (st->hidden_ids[i] == id) {
            return true;
        }
    }
    return false;
}

/* The deck keeps IDs; only the foreground and peek text are copied out of
 * PSRAM. Syncs can swap the cache without leaving a dangling card pointer. */
static void snapshot(snapshot_t *out, bool focus_events)
{
    memset(out, 0, sizeof(*out));
    int ids[DECK_CAPACITY];
    int count = 0;
    state_lock();
    const status_state_t *st = state_get();
    for (int i = st->notif_count - 1; i >= 0; i--) {
        if (!locally_hidden(st, st->notifs[i].id)) {
            ids[count++] = st->notifs[i].id;
        }
    }
    deck_reconcile(&s_deck, ids, count);
    int focused_urgency = 1;
    for (int i = 0; i < st->notif_count; i++) {
        if (s_deck.has_focus && st->notifs[i].id == s_deck.focus_id) {
            focused_urgency = st->notifs[i].urgency;
            break;
        }
    }
    if (st->notif_critical_seq != s_seen_critical_seq) {
        s_seen_critical_seq = st->notif_critical_seq;
        if (focus_events) {
            deck_request_focus(&s_deck, st->notif_critical_id, 2, focused_urgency, st->notif_critical_us);
            for (int i = 0; i < st->notif_count; i++) {
                if (s_deck.has_focus && st->notifs[i].id == s_deck.focus_id) {
                    focused_urgency = st->notifs[i].urgency;
                    break;
                }
            }
        }
    }
    if (st->notif_focus_seq != s_seen_focus_seq) {
        s_seen_focus_seq = st->notif_focus_seq;
        if (focus_events) {
            deck_request_focus(&s_deck, st->notif_focus_id, st->notif_focus_urgency,
                               focused_urgency, st->notif_focus_us);
        }
    }
    /* A peek tap acts on the displayed foreground. Let deck_advance cancel
     * any queued arrival before a timer can move focus under that tap. */
    if (focus_events) {
        deck_tick(&s_deck, esp_timer_get_time());
    }
    const int next_id = deck_next_id(&s_deck);
    for (int i = 0; i < st->notif_count; i++) {
        if (s_deck.has_focus && st->notifs[i].id == s_deck.focus_id) {
            out->foreground = st->notifs[i];
        }
        if (s_deck.count > 1 && st->notifs[i].id == next_id) {
            out->next = st->notifs[i];
        }
    }
    out->dashboard = st->dashboard;
    out->overflow = st->notif_overflow;
    out->stale = !st->got_sync || !link_host_connected() || st->last_rx_us == 0
        || esp_timer_get_time() - st->last_rx_us > STATUS_STALE_TIMEOUT_US;
    status_state_t *live = state_get();
    live->deck_enabled = s_visible;
    live->deck_stale = out->stale;
    live->deck_reachable = s_deck.count;
    live->deck_position = deck_position(&s_deck) + 1;
    live->deck_focus_id = s_deck.focus_id;
    live->deck_next_id = next_id;
    state_unlock();
}

static void dismiss_cb(lv_event_t *event)
{
    if (s_deck.has_focus) {
        const int id = s_deck.focus_id;
        deck_cancel_pending(&s_deck);
        state_hide_notif(id);
        proto_send_input_dismiss(id);
        ui_deck_tick(STATE_DIRTY_NOTIF);
    }
}

static void peek_cb(lv_event_t *event)
{
    snapshot_t current;
    snapshot(&current, false);
    deck_advance(&s_deck);
    ui_deck_tick(STATE_DIRTY_NOTIF);
}

static void transient(const status_dashboard_t *now)
{
    if (s_have_dashboard) {
        const status_dashboard_t *old = &s_previous_dashboard;
        if (now->volume_present && old->volume_present
                && (now->volume_level != old->volume_level || now->mute != old->mute
                    || now->mute_known != old->mute_known)) {
            if (now->mute_known && now->mute) {
                strlcpy(s_transient, "Volume muted", sizeof(s_transient));
            } else {
                snprintf(s_transient, sizeof(s_transient), "Volume %d%%", (int)(now->volume_level * 100 + .5f));
            }
            s_transient_until = esp_timer_get_time() + 4000000;
        } else if (now->bluetooth_valid && old->bluetooth_valid
                && now->bluetooth != old->bluetooth) {
            snprintf(s_transient, sizeof(s_transient), "Bluetooth %d connected", now->bluetooth);
            s_transient_until = esp_timer_get_time() + 4000000;
        }
    }
    s_previous_dashboard = *now;
    s_have_dashboard = true;
}

static void metrics(const snapshot_t *view, bool active)
{
    const status_dashboard_t *d = &view->dashboard;
    char value[32];
    const int top = active ? 58 : 43;
    const int step = active ? 24 : 26;
    for (int i = 0; i < 4; i++) {
        lv_obj_set_y(s_metric_names[i], top + i * step + 3);
        lv_obj_set_y(s_metric_values[i], top + i * step);
        uint32_t color = view->stale ? SECONDARY : FOREGROUND;
        if (!view->stale && ((i == 2 && d->network_valid && !d->network)
                || (i == 3 && d->battery_present && d->battery_level <= .2f
                    && !(d->charging_known && d->charging)))) {
            color = WARNING;
        }
        text_color(s_metric_values[i], color);
        hide(s_metric_names[i], i == 3 && !d->battery_present);
        hide(s_metric_values[i], i == 3 && !d->battery_present);
    }
    if (d->cpu_valid) {
        snprintf(value, sizeof(value), "%d%%", (int)(d->cpu * 100 + .5f));
    } else {
        strlcpy(value, "--", sizeof(value));
    }
    text(s_metric_values[0], value);
    if (d->mem_valid) {
        snprintf(value, sizeof(value), "%d%%", (int)(d->mem * 100 + .5f));
    } else {
        strlcpy(value, "--", sizeof(value));
    }
    text(s_metric_values[1], value);
    text(s_metric_values[2], !d->network_valid ? "--" : d->network ? "LINKED" : "OFFLINE");
    snprintf(value, sizeof(value), "%d%%%s", (int)(d->battery_level * 100 + .5f),
             d->charging_known && d->charging ? "+" : "");
    text(s_metric_values[3], value);
    hide(s_rail_clock, !active);
    hide(s_rail_header, active);
    text(s_rail_header, view->stale ? "HOST / STALE" : "HOST");
    const bool showing_transient = !view->stale && esp_timer_get_time() < s_transient_until;
    text(s_rail_footer, view->stale ? "Readings stale" : active && showing_transient ? s_transient : "");
    text(s_idle_transient, !active && view->overflow == 0 && showing_transient ? s_transient : "");
}

static void clocks(void)
{
    struct tm tm;
    char clock[16] = "--:--";
    char date[48] = "Waiting for clock";
    if (rtc_pcf_get_local(&tm) != RTC_SOURCE_NONE) {
        strftime(clock, sizeof(clock), "%H:%M", &tm);
        strftime(date, sizeof(date), "%a, %d %b %Y", &tm);
    }
    text(s_rail_clock, clock);
    text(s_idle_clock, clock);
    text(s_date, date);
}

static void card(const snapshot_t *view)
{
    const status_notif_t *n = &view->foreground;
    const bool multiple = s_deck.count > 1;
    const int width = multiple ? 392 : 464;
    lv_obj_set_width(s_primary, width);
    lv_obj_set_width(s_app, width - 70);
    lv_obj_set_width(s_title, width - 64);
    lv_obj_set_width(s_body, width - 24);
    lv_obj_set_width(s_position, width - 24);
    lv_obj_set_x(s_dismiss, width - 48);
    const lv_color_t accent = lv_color_hex(n->urgency >= 2 ? CRITICAL : ACCENT);
    if (!lv_color_eq(lv_obj_get_style_bg_color(s_accent, 0), accent)) {
        lv_obj_set_style_bg_color(s_accent, accent, 0);
    }
    text(s_app, n->app);
    text(s_title, n->summary[0] ? n->summary : "Notification");
    text(s_body, n->body);
    char position[64];
    if (view->overflow > 0) {
        snprintf(position, sizeof(position), "%d / %d   +%d uncached", deck_position(&s_deck) + 1,
                 s_deck.count, view->overflow);
    } else if (multiple) {
        snprintf(position, sizeof(position), "%d / %d", deck_position(&s_deck) + 1, s_deck.count);
    } else {
        position[0] = '\0';
    }
    text(s_position, position);
    hide(s_peek, !multiple);
    text(s_peek_app, view->next.app[0] ? view->next.app : "NEXT");
    text(s_peek_title, view->next.summary);
}

void ui_deck_tick(uint32_t dirty)
{
    if (!s_visible) {
        return;
    }
    snapshot_t view;
    snapshot(&view, true);
    if (dirty & STATE_DIRTY_DASHBOARD) {
        transient(&view.dashboard);
    }
    const bool active = s_deck.has_focus && !view.stale;
    hide(s_primary, !active);
    hide(s_idle, active);
    if (active) {
        card(&view);
    } else {
        hide(s_peek, true);
        if (view.stale) {
            text(s_message, "Host disconnected");
        } else if (view.overflow > 0) {
            char message[64];
            snprintf(message, sizeof(message), "%d uncached notifications", view.overflow);
            text(s_message, message);
        } else {
            text(s_message, "");
        }
    }
    metrics(&view, active);
    clocks();
}

void ui_deck_show(bool visible)
{
    s_visible = visible;
    hide(s_root, !visible);
    if (!visible) {
        state_lock();
        status_state_t *st = state_get();
        st->deck_enabled = false;
        st->deck_reachable = 0;
        st->deck_position = 0;
        state_unlock();
    }
    if (visible) {
        ui_deck_tick(STATE_DIRTY_DASHBOARD | STATE_DIRTY_NOTIF);
    }
}

void ui_deck_init(lv_obj_t *parent, const lv_font_t *small, const lv_font_t *meta)
{
    s_small = small;
    s_meta = meta;
    s_root = box(parent, 0, 0, 640, 172, BACKGROUND, 0);
    lv_obj_t *rail = box(s_root, 0, 0, 160, 172, RAIL, 0);
    box(s_root, 159, 0, 1, 172, 0x2C3B49, 0);
    s_rail_header = label(rail, 12, 12, 136, 20, s_meta, SECONDARY, "HOST");
    s_rail_clock = label(rail, 10, 7, 144, 48, &lv_font_montserrat_40, FOREGROUND, "--:--");
    s_rail_footer = label(rail, 12, 156, 136, 16, s_small, SECONDARY, "");
    const char *names[] = {"CPU", "MEM", "NET", "BAT"};
    for (int i = 0; i < 4; i++) {
        s_metric_names[i] = label(rail, 12, 43 + 26 * i, 42, 21, s_meta, SECONDARY, names[i]);
        s_metric_values[i] = label(rail, 56, 43 + 26 * i, 92, 26,
                                   i == 2 ? s_meta : &status_text_20, FOREGROUND, "--");
        lv_obj_set_style_text_align(s_metric_values[i], LV_TEXT_ALIGN_RIGHT, 0);
    }
    s_idle = box(s_root, 160, 0, 480, 172, BACKGROUND, 0);
    s_idle_clock = label(s_idle, 12, 35, 456, 64, &status_clock_80, FOREGROUND, "--:--");
    lv_obj_set_style_text_align(s_idle_clock, LV_TEXT_ALIGN_CENTER, 0);
    s_date = label(s_idle, 12, 112, 456, 21, s_meta, SECONDARY, "");
    lv_obj_set_style_text_align(s_date, LV_TEXT_ALIGN_CENTER, 0);
    s_message = label(s_idle, 12, 138, 456, 26, &status_text_20, WARNING, "");
    lv_obj_set_style_text_align(s_message, LV_TEXT_ALIGN_CENTER, 0);
    s_idle_transient = label(s_idle, 12, 141, 456, 21, s_meta, SECONDARY, "");
    lv_obj_set_style_text_align(s_idle_transient, LV_TEXT_ALIGN_CENTER, 0);

    s_primary = box(s_root, 168, 8, 464, 156, SURFACE, 8);
    s_accent = box(s_primary, 0, 10, 3, 136, ACCENT, 2);
    s_app = label(s_primary, 12, 6, 394, 20, s_meta, ACCENT, "");
    s_title = label(s_primary, 12, 28, 440, 28, &status_text_22, FOREGROUND, "");
    s_body = label(s_primary, 12, 58, 440, 78, &status_text_20, FOREGROUND, "");
    lv_obj_set_style_text_line_space(s_body, 0, 0);
    s_position = label(s_primary, 12, 140, 440, 16, s_small, SECONDARY, "");
    s_dismiss = box(s_primary, 416, 0, 48, 36, SURFACE, 8);
    lv_obj_add_flag(s_dismiss, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_dismiss, dismiss_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *cross = label(s_dismiss, 0, 3, 48, 28, &status_text_22, SECONDARY, "×");
    lv_obj_set_style_text_align(cross, LV_TEXT_ALIGN_CENTER, 0);
    s_peek = box(s_root, 568, 16, 64, 140, 0x1A2A36, 8);
    lv_obj_add_flag(s_peek, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_peek, peek_cb, LV_EVENT_CLICKED, NULL);
    s_peek_app = label(s_peek, 8, 10, 48, 20, s_small, ACCENT, "NEXT");
    s_peek_title = label(s_peek, 8, 34, 48, 66, s_meta, SECONDARY, "");
    label(s_peek, 8, 109, 48, 26, &status_text_20, ACCENT, "→");
    ui_deck_show(false);
}
