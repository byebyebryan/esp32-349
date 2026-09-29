#include "ui_deck.h"

#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "deck.h"
#include "deck_input.h"
#include "group_input.h"
#include "esp_timer.h"
#include "src/misc/lv_text.h"
#include "link.h"
#include "proto.h"
#include "rtc.h"
#include "state.h"

LV_FONT_DECLARE(status_text_16);
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
#define DISMISS_WIDTH 64
#define DISMISS_HEIGHT 48
#define DISMISS_CROSS_SPAN 28
#define OPEN_X 328
#define CONTROL_GAP 8
#define GROUP_LIFECYCLE_MS 180

static lv_obj_t *s_root, *s_rail_clock, *s_rail_footer;
static lv_obj_t *s_metric_names[4], *s_metric_values[4];
static lv_obj_t *s_idle, *s_idle_clock, *s_date, *s_message, *s_idle_transient;
typedef struct {
    lv_obj_t *root, *accent, *app, *title, *body, *position, *open, *open_label, *dismiss;
    int id;
    bool valid;
} card_view_t;
static lv_obj_t *s_viewport;
static lv_obj_t *s_content, *s_group_cue, *s_group_empty;
static card_view_t s_cards[3];
static bool s_multiple;
static int s_offset;
static const lv_font_t *s_small, *s_meta;
static deck_t s_deck;
static deck_input_t s_input;
static lv_indev_t *s_input_indev;
static bool s_pending_critical;
static int s_pending_critical_id;
static uint32_t s_seen_focus_seq;
static uint32_t s_seen_critical_seq;
static status_dashboard_t s_previous_dashboard;
static bool s_have_dashboard;
static char s_transient[48];
static int64_t s_transient_until;
static bool s_visible;
static int64_t s_last_clock_second = -1;
static group_input_t s_group_input;
static bool s_group_mode, s_group_home = true, s_group_manual, s_group_auto;
static bool s_history_mode;
static int64_t s_history_last_input_us;
static int s_group_session, s_group_generation, s_group_present_id;
static int s_group_offset;
static bool s_group_persistent;
static bool s_group_skip_lifecycle;
static int64_t s_group_deadline;
typedef enum { GROUP_LIFECYCLE_NONE, GROUP_LIFECYCLE_GROUP,
               GROUP_LIFECYCLE_CARD } group_lifecycle_kind_t;
/* Visual motion never delays cache removal or host input. An outgoing widget
 * keeps its last pixels, but cannot accept input or write data back to state. */
static struct {
    group_lifecycle_kind_t kind;
    bool active;
    int to_id;
    uint32_t generation;
} s_group_lifecycle;
/* LVGL's DOT mode modifies its displayed string. Keep the supplied text so
 * an ellipsized card does not get allocated and redrawn every 100 ms. */
static char s_label_text[40][STATUS_NOTIF_BODY_MAX];
static int s_label_count;

typedef struct {
    status_dashboard_t dashboard;
    status_notif_t cards[3]; /* Previous, foreground, next. */
    int overflow;
    bool stale;
    bool actions_enabled, action_pending, open_enabled, open_pending;
    int action_pending_id, action_pending_open_rev;
    status_action_feedback_t action_feedback;
    int action_feedback_id, action_feedback_open_rev;
    int64_t action_feedback_until_us;
} snapshot_t;
/* Persistent copies keep three records off the LVGL task's callback stack. */
static snapshot_t s_view;
static void input_cb(lv_event_t *event);
static void start_snap(deck_input_action_t action);
static void animation_exec(void *var, int32_t value);
static void grouped_tick(uint32_t dirty);
static void grouped_input_cb(lv_event_t *event);
static void grouped_cancel(void);
static void grouped_lifecycle_cancel(void);

static bool reachable(int id)
{
    if (s_group_mode && id == GROUP_EMPTY_NOTIFICATIONS_ID) return true;
    for (int i = 0; i < s_deck.count; i++) {
        if (s_deck.ids[i] == id) {
            return true;
        }
    }
    return false;
}

static int urgency(const status_state_t *st, int id)
{
    for (int i = 0; i < st->notif_count; i++) {
        if (st->notifs[i].id == id) {
            return st->notifs[i].urgency;
        }
    }
    return 1;
}

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

static int title_width(const char *value)
{
    lv_point_t size = {0};
    lv_text_get_size(&size, value, &status_text_22, 0, 0, LV_COORD_MAX,
                     LV_TEXT_FLAG_BREAK_ALL);
    return size.x;
}

static size_t next_utf8_boundary(const char *text_value, size_t byte, size_t length)
{
    const unsigned char lead = (unsigned char)text_value[byte];
    size_t step = lead < 0x80 ? 1 : (lead & 0xE0) == 0xC0 ? 2
        : (lead & 0xF0) == 0xE0 ? 3 : (lead & 0xF8) == 0xF0 ? 4 : 1;
    if (byte + step > length) step = 1;
    return byte + step;
}

static void grouped_title(card_view_t *slot, const char *summary,
                          bool reserve_open, int width)
{
    const lv_label_long_mode_t wanted_mode = reserve_open
        ? LV_LABEL_LONG_MODE_CLIP : LV_LABEL_LONG_MODE_DOTS;
    if (lv_label_get_long_mode(slot->title) != wanted_mode) {
        lv_label_set_long_mode(slot->title, wanted_mode);
    }
    if (!reserve_open || title_width(summary) <= width) {
        text(slot->title, summary);
        return;
    }

    char rendered[STATUS_NOTIF_SUMMARY_MAX];
    const size_t length = strlen(summary);
    size_t prefix = 0;
    rendered[0] = '\0';
    while (prefix < length) {
        const size_t next = next_utf8_boundary(summary, prefix, length);
        if (next + 3 >= sizeof(rendered)) break;
        memcpy(rendered, summary, next);
        memcpy(rendered + next, "...", 4);
        if (title_width(rendered) > width) break;
        prefix = next;
    }
    if (prefix == 0) {
        strlcpy(rendered, "...", sizeof(rendered));
    } else {
        memcpy(rendered, summary, prefix);
        memcpy(rendered + prefix, "...", 4);
    }
    text(slot->title, rendered);
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
    LV_ASSERT(s_label_count < 40);
    char *stored = s_label_text[s_label_count++];
    strlcpy(stored, value, STATUS_NOTIF_BODY_MAX);
    lv_obj_set_user_data(obj, stored);
    lv_label_set_text(obj, value);
    return obj;
}

static lv_obj_t *dismiss_button(lv_obj_t *parent, int x)
{
    lv_obj_t *button = box(parent, x, 0, DISMISS_WIDTH, DISMISS_HEIGHT, 0x243544, 8);
    lv_obj_add_flag(button, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK | LV_OBJ_FLAG_EVENT_BUBBLE);
    /* Draw the cross at its intended size rather than relying on a small
     * text glyph. Non-clickable strokes keep the whole button as the target. */
    static const lv_point_precise_t points[2][2] = {
        {{0, 0}, {DISMISS_CROSS_SPAN, DISMISS_CROSS_SPAN}},
        {{0, DISMISS_CROSS_SPAN}, {DISMISS_CROSS_SPAN, 0}},
    };
    for (int i = 0; i < 2; i++) {
        lv_obj_t *stroke = lv_line_create(button);
        lv_obj_remove_style_all(stroke);
        lv_obj_remove_flag(stroke, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
        lv_line_set_points(stroke, points[i], 2);
        lv_obj_set_pos(stroke, (DISMISS_WIDTH - DISMISS_CROSS_SPAN) / 2,
                       (DISMISS_HEIGHT - DISMISS_CROSS_SPAN) / 2);
        lv_obj_set_style_line_width(stroke, 3, 0);
        lv_obj_set_style_line_color(stroke, lv_color_hex(FOREGROUND), 0);
        lv_obj_set_style_line_rounded(stroke, true, 0);
    }
    return button;
}

static void open_button(lv_obj_t *parent, lv_obj_t **button_out,
                        lv_obj_t **label_out)
{
    lv_obj_t *button = box(parent, OPEN_X, 0, DISMISS_WIDTH, DISMISS_HEIGHT,
                           0x213641, 8);
    lv_obj_add_flag(button, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK |
                            LV_OBJ_FLAG_EVENT_BUBBLE);
    lv_obj_set_style_border_width(button, 1, 0);
    lv_obj_set_style_border_color(button, lv_color_hex(ACCENT), 0);
    lv_obj_set_style_border_opa(button, LV_OPA_COVER, 0);
    lv_obj_t *caption = label(button, 2, 12, DISMISS_WIDTH - 4, 24,
                              &status_text_20, ACCENT, "Open");
    lv_obj_set_style_text_align(caption, LV_TEXT_ALIGN_CENTER, 0);
    *button_out = button;
    *label_out = caption;
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

/* The deck keeps IDs; only the foreground and its neighbors are copied out of
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
    const bool automatic = focus_events && !deck_input_busy(&s_input);
    if (s_pending_critical && automatic) {
        if (reachable(s_pending_critical_id) && urgency(st, s_pending_critical_id) >= 2) {
            deck_request_focus(&s_deck, s_pending_critical_id, 2, 1, esp_timer_get_time());
        }
        s_pending_critical = false;
    }
    int focused_urgency = s_deck.has_focus ? urgency(st, s_deck.focus_id) : 1;
    if (st->notif_critical_seq != s_seen_critical_seq) {
        s_seen_critical_seq = st->notif_critical_seq;
        if (automatic && reachable(st->notif_critical_id) && urgency(st, st->notif_critical_id) >= 2) {
            deck_request_focus(&s_deck, st->notif_critical_id, 2, focused_urgency, st->notif_critical_us);
            focused_urgency = s_deck.has_focus ? urgency(st, s_deck.focus_id) : 1;
        } else if (reachable(st->notif_critical_id) && urgency(st, st->notif_critical_id) >= 2) {
            /* Normal events are consumed during touch; critical events survive
             * until release/settlement and are revalidated against the cache. */
            s_pending_critical = true;
            s_pending_critical_id = st->notif_critical_id;
        }
    }
    if (st->notif_focus_seq != s_seen_focus_seq) {
        s_seen_focus_seq = st->notif_focus_seq;
        if (automatic) {
            deck_request_focus(&s_deck, st->notif_focus_id, st->notif_focus_urgency,
                               focused_urgency, st->notif_focus_us);
        }
    }
    if (automatic) {
        deck_tick(&s_deck, esp_timer_get_time());
    } else {
        deck_cancel_pending(&s_deck);
    }
    const int next_id = deck_next_id(&s_deck);
    int previous_id = 0;
    const bool has_previous = deck_neighbor_id(&s_deck, -1, &previous_id);
    for (int i = 0; i < st->notif_count; i++) {
        if (s_deck.has_focus && st->notifs[i].id == s_deck.focus_id) {
            out->cards[1] = st->notifs[i];
        }
        if (s_deck.count > 1 && st->notifs[i].id == next_id) {
            out->cards[2] = st->notifs[i];
        }
        if (has_previous && st->notifs[i].id == previous_id) {
            out->cards[0] = st->notifs[i];
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

static void format_rate(char *out, size_t size, bool valid, double rate)
{
    static const char *const units[] = {"B/s", "KB/s", "MB/s", "GB/s"};
    if (!valid || !isfinite(rate) || rate < 0 || rate > 1000000000000.0) {
        strlcpy(out, "--", size);
        return;
    }
    if (rate == 0) {
        strlcpy(out, "0 B/s", size);
        return;
    }

    unsigned unit = 0;
    double shown = rate;
    while (shown >= 1000.0 && unit < 3) {
        shown /= 1000.0;
        unit++;
    }
    for (;;) {
        const bool decimal = shown < 10.0;
        const double rounded = decimal
            ? floor(shown * 10.0 + 0.5) / 10.0
            : floor(shown + 0.5);
        if (rounded >= 1000.0 && unit < 3) {
            shown /= 1000.0;
            unit++;
            continue;
        }
        if (unit == 3 && rounded >= 1000.0) {
            strlcpy(out, ">999GB/s", size);
        } else if (unit == 0 && rounded == 0.0) {
            strlcpy(out, "<1 B/s", size);
        } else if (decimal) {
            snprintf(out, size, "%.1f %s", rounded, units[unit]);
        } else {
            snprintf(out, size, "%.0f %s", rounded, units[unit]);
        }
        return;
    }
}

static void metrics(const snapshot_t *view, bool active)
{
    const status_dashboard_t *d = &view->dashboard;
    char value[32];
    const int top = 58;
    const int step = 24;
    for (int i = 0; i < 4; i++) {
        lv_obj_set_y(s_metric_names[i], top + i * step + 3);
        lv_obj_set_y(s_metric_values[i], top + i * step);
        uint32_t color = view->stale ? SECONDARY : FOREGROUND;
        text_color(s_metric_values[i], color);
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
    format_rate(value, sizeof(value), d->rx_bytes_per_s_valid, d->rx_bytes_per_s);
    text(s_metric_values[2], value);
    format_rate(value, sizeof(value), d->tx_bytes_per_s_valid, d->tx_bytes_per_s);
    text(s_metric_values[3], value);
    hide(s_rail_clock, false);
    const bool showing_transient = !view->stale && esp_timer_get_time() < s_transient_until;
    const char *footer = view->stale ? "Readings stale"
        : d->network_valid && !d->network ? "Uplink offline"
        : active && showing_transient ? s_transient : "";
    text(s_rail_footer, footer);
    text(s_idle_transient, !active && !view->stale && view->overflow == 0 && showing_transient
        ? s_transient : "");
}

static void clocks(void)
{
    const int64_t second = esp_timer_get_time() / 1000000;
    if (second == s_last_clock_second) return;
    s_last_clock_second = second;
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

static void position_text(card_view_t *slot, int overflow, const snapshot_t *view)
{
    int index = -1;
    for (int i = 0; i < s_deck.count; i++) {
        if (slot->valid && s_deck.ids[i] == slot->id) {
            index = i;
            break;
        }
    }
    char value[64] = "";
    if (index >= 0 && overflow > 0) {
        snprintf(value, sizeof(value), "%d / %d   +%d uncached", index + 1, s_deck.count, overflow);
    } else if (index >= 0 && s_deck.count > 1) {
        snprintf(value, sizeof(value), "%d / %d", index + 1, s_deck.count);
    }
    if (slot == &s_cards[1] && view->actions_enabled &&
        view->action_feedback != STATUS_ACTION_FEEDBACK_NONE &&
        view->action_feedback_id == slot->id &&
        view->action_feedback_open_rev > 0 &&
        view->action_feedback_open_rev == view->cards[1].open_revision &&
        esp_timer_get_time() < view->action_feedback_until_us) {
        const char *message = "";
        switch (view->action_feedback) {
        case STATUS_ACTION_FEEDBACK_SENT: message = "Sent"; break;
        case STATUS_ACTION_FEEDBACK_UNAVAILABLE: message = "Unavailable"; break;
        case STATUS_ACTION_FEEDBACK_TRY_AGAIN: message = "Try again"; break;
        case STATUS_ACTION_FEEDBACK_NO_CONFIRMATION: message = "No confirmation"; break;
        case STATUS_ACTION_FEEDBACK_NONE: break;
        }
        if (message[0]) {
            const size_t used = strlen(value);
            snprintf(value + used, sizeof(value) - used, "%s%s",
                     used ? "   " : "", message);
        }
    }
    text(slot->position, value);
}

static void position_cards(int offset)
{
    s_offset = offset;
    for (int i = 0; i < 3; i++) {
        lv_obj_set_x(s_cards[i].root, 8 + (i - 1) * 400 + offset);
    }
}

static void bind_cards(const snapshot_t *view)
{
    s_multiple = s_deck.count > 1;
    const int width = s_multiple ? 392 : 464;
    for (int i = 0; i < 3; i++) {
        card_view_t *slot = &s_cards[i];
        const status_notif_t *n = &view->cards[i];
        slot->valid = n->valid && (i == 1 || s_multiple);
        slot->id = n->id;
        hide(slot->root, !slot->valid);
        hide(slot->dismiss, false);
        hide(slot->open, true);
        lv_obj_set_height(slot->root, 156);
        lv_obj_set_y(slot->root, 8);
        lv_obj_set_height(slot->accent, 136);
        lv_obj_set_y(slot->app, 6);
        lv_obj_set_y(slot->title, 28);
        lv_obj_set_y(slot->body, 58);
        lv_obj_set_height(slot->body, 78);
        lv_obj_set_y(slot->position, 140);
        lv_obj_set_width(slot->root, width);
        lv_obj_set_width(slot->app, width - DISMISS_WIDTH - 24);
        lv_obj_set_width(slot->title, width - DISMISS_WIDTH - 24);
        lv_obj_set_width(slot->body, width - 24);
        lv_obj_set_width(slot->position, width - 24);
        lv_obj_set_x(slot->dismiss, width - DISMISS_WIDTH);
        if (!slot->valid) {
            continue;
        }
        const lv_color_t accent = lv_color_hex(n->urgency >= 2 ? CRITICAL : ACCENT);
        if (!lv_color_eq(lv_obj_get_style_bg_color(slot->accent, 0), accent)) {
            lv_obj_set_style_bg_color(slot->accent, accent, 0);
        }
        text(slot->app, n->app);
        text(slot->title, n->summary[0] ? n->summary : "Notification");
        text(slot->body, n->body);
        position_text(slot, view->overflow, view);
    }
    position_cards(0);
}

static void cancel_interaction(void)
{
    lv_anim_delete(&s_input, animation_exec);
    deck_input_cancel(&s_input);
    position_cards(0);
    if (s_input_indev && deck_input_pointer_down(&s_input)) {
        /* Hidden/stale views must not turn the same held press into a click
         * on the replacement UI. Poll the release latch on later UI ticks. */
        lv_indev_wait_release(s_input_indev);
    }
}

static deck_input_action_t validate_input(void)
{
    return deck_input_validate(&s_input,
        s_input.has_source && reachable(s_input.source_id),
        s_input.captured_previous && reachable(s_input.previous_id),
        s_input.captured_next && reachable(s_input.next_id));
}

static void animation_exec(void *var, int32_t value)
{
    (void)var;
    position_cards(value);
}

static void animation_completed(lv_anim_t *animation)
{
    const uint32_t generation = (uint32_t)(uintptr_t)lv_anim_get_user_data(animation);
    if (generation != deck_input_generation(&s_input)) {
        return;
    }
    snapshot(&s_view, false);
    const deck_input_action_t action = deck_input_complete(&s_input, generation,
        s_input.has_source && reachable(s_input.source_id),
        s_input.captured_previous && reachable(s_input.previous_id),
        s_input.captured_next && reachable(s_input.next_id));
    if (action.kind == DECK_INPUT_ACTION_SNAP) {
        start_snap(action);
        return;
    }
    if (action.kind == DECK_INPUT_ACTION_SETTLED && action.commit_target && action.has_target) {
        deck_select_id(&s_deck, action.target_id);
    }
    if (deck_input_state(&s_input) == DECK_INPUT_IGNORED) {
        /* A destination-expiry rollback can finish while the finger is down. */
        bind_cards(&s_view);
    }
    ui_deck_tick(STATE_DIRTY_NOTIF);
}

static void start_snap(deck_input_action_t action)
{
    lv_anim_delete(&s_input, animation_exec);
    lv_anim_t animation;
    lv_anim_init(&animation);
    lv_anim_set_var(&animation, &s_input);
    lv_anim_set_exec_cb(&animation, animation_exec);
    lv_anim_set_values(&animation, s_offset, action.offset_px);
    lv_anim_set_duration(&animation, 180);
    lv_anim_set_path_cb(&animation, lv_anim_path_ease_out);
    lv_anim_set_user_data(&animation, (void *)(uintptr_t)action.generation);
    lv_anim_set_completed_cb(&animation, animation_completed);
    if (!lv_anim_start(&animation)) {
        cancel_interaction();
        ui_deck_tick(STATE_DIRTY_NOTIF);
    }
}

static void input_cb(lv_event_t *event)
{
    if (s_group_mode) {
        grouped_input_cb(event);
        return;
    }
    const lv_event_code_t code = lv_event_get_code(event);
    if (code != LV_EVENT_PRESSED && code != LV_EVENT_PRESSING && code != LV_EVENT_RELEASED
            && code != LV_EVENT_PRESS_LOST && code != LV_EVENT_INDEV_RESET) {
        return;
    }
    lv_indev_t *indev = lv_event_get_indev(event);
    if (!indev) {
        return;
    }
    if (code == LV_EVENT_PRESS_LOST || code == LV_EVENT_INDEV_RESET) {
        if (deck_input_busy(&s_input)) {
            cancel_interaction();
            ui_deck_tick(STATE_DIRTY_NOTIF);
        }
        return;
    }
    lv_point_t point;
    lv_indev_get_point(indev, &point);
    if (code == LV_EVENT_PRESSED) {
        if (!s_visible || deck_input_busy(&s_input)) {
            return;
        }
        s_input_indev = indev;
        snapshot(&s_view, false);
        if (s_view.stale || !s_cards[1].valid || !reachable(s_cards[1].id)) {
            lv_indev_wait_release(indev);
            ui_deck_tick(STATE_DIRTY_NOTIF);
            return;
        }
        /* Capture the displayed slots. A pending arrival must not change the
         * preview or focus before this press has chosen its destination. */
        deck_select_id(&s_deck, s_cards[1].id);
        const lv_obj_t *target = lv_event_get_target_obj(event);
        deck_input_press(&s_input, point.x, point.y, esp_timer_get_time(),
            s_cards[1].id, s_cards[0].id, s_cards[2].id,
            s_multiple ? s_deck.count : 1,
            target == s_cards[1].dismiss,
            s_multiple && point.x >= 568 && point.x < 632);
    } else if (indev == s_input_indev && deck_input_busy(&s_input)) {
        snapshot(&s_view, false);
        if (s_view.stale) {
            cancel_interaction();
            ui_deck_tick(STATE_DIRTY_NOTIF);
            return;
        }
        const uint32_t old_generation = deck_input_generation(&s_input);
        const deck_input_action_t invalidation = validate_input();
        if (invalidation.kind == DECK_INPUT_ACTION_SNAP) {
            start_snap(invalidation);
        } else if (old_generation != deck_input_generation(&s_input)) {
            lv_anim_delete(&s_input, animation_exec);
            position_cards(0);
            bind_cards(&s_view);
        }
        if (code == LV_EVENT_PRESSING) {
            const int offset = deck_input_move(&s_input, point.x, point.y, esp_timer_get_time());
            if (deck_input_state(&s_input) == DECK_INPUT_DRAGGING) {
                position_cards(offset);
            }
        } else if (code == LV_EVENT_RELEASED) {
            const deck_input_action_t action = deck_input_release(&s_input, esp_timer_get_time());
            if (action.kind == DECK_INPUT_ACTION_SNAP || action.kind == DECK_INPUT_ACTION_NEXT_TAP) {
                start_snap(action);
            } else if (action.kind == DECK_INPUT_ACTION_DISMISS && action.has_target && reachable(action.target_id)) {
                state_hide_notif(action.target_id);
                proto_send_input_dismiss(action.target_id);
                ui_deck_tick(STATE_DIRTY_NOTIF);
            } else if (!deck_input_busy(&s_input)) {
                ui_deck_tick(STATE_DIRTY_NOTIF);
            }
        }
    }
}

/* Group mode shares the accepted rail, fonts, and three reusable card views.
 * Only IDs live in the deck; these copied views stay frozen during motion. */
static void grouped_position(int offset)
{
    s_group_offset = offset;
    const bool horizontal = s_group_lifecycle.active
        ? s_group_lifecycle.kind == GROUP_LIFECYCLE_GROUP
        : s_group_input.axis == GROUP_AXIS_HORIZONTAL;
    const int dx = horizontal ? offset : 0;
    lv_obj_set_x(s_idle, (s_group_home ? 0 : -480) + dx);
    lv_obj_set_x(s_viewport, (s_group_home ? 480 : 0) + dx);
    for (int i = 0; i < 3; i++) {
        lv_obj_set_x(s_cards[i].root, 8);
        const int pitch = s_history_mode ? HISTORY_CARD_PITCH_PX : GROUP_CARD_PITCH_PX;
        lv_obj_set_y(s_cards[i].root, (i - 1) * pitch + (horizontal ? 0 : offset));
    }
}

static void grouped_animation_exec(void *var, int32_t offset)
{
    (void)var;
    grouped_position(offset);
}

static void grouped_cancel(void)
{
    grouped_lifecycle_cancel();
    lv_anim_delete(&s_group_input.motion, grouped_animation_exec);
    group_input_cancel(&s_group_input);
    if (s_input_indev && s_group_input.motion.pointer_down) {
        lv_indev_wait_release(s_input_indev);
    }
    if (s_idle && s_viewport) grouped_position(0);
}

static bool group_busy(void)
{
    return s_group_lifecycle.active || deck_input_busy(&s_group_input.motion);
}

static deck_input_action_t grouped_validate(void)
{
    const group_input_t *g = &s_group_input;
    return group_input_validate_action(&s_group_input, reachable(g->source),
        reachable(g->newer), reachable(g->older), reachable(g->selected),
        s_view.actions_enabled, s_view.cards[1].id,
        s_view.cards[1].open_revision, s_view.open_enabled);
}

static bool button_hit(lv_obj_t *button, int x, int y, lv_area_t *bounds)
{
    lv_obj_get_coords(button, bounds);
    return x >= bounds->x1 && x <= bounds->x2 &&
           y >= bounds->y1 && y <= bounds->y2;
}

static void grouped_snapshot(snapshot_t *out, bool allow_attention)
{
    memset(out, 0, sizeof(*out));
    int ids[DECK_CAPACITY], count = 0;
    state_lock();
    status_state_t *st = state_get();
    if (st->grouped_session != s_group_session || st->history_enabled != s_history_mode) {
        state_unlock();
        grouped_cancel();
        memset(&s_deck, 0, sizeof(s_deck));
        s_group_home = true; s_group_manual = false; s_group_auto = false;
        s_group_generation = 0;
        state_lock();
        st = state_get();
        s_group_session = st->grouped_session;
        s_history_mode = st->history_enabled;
        if (s_history_mode) s_group_home = false;
        s_history_last_input_us = esp_timer_get_time();
    }
    for (int i = st->notif_count - 1; i >= 0; i--) {
        if (!locally_hidden(st, st->notifs[i].id)) ids[count++] = st->notifs[i].id;
    }
    const int previous_count = s_deck.count;
    deck_reconcile(&s_deck, ids, count);
    const int64_t now = esp_timer_get_time();
    bool became_idle = false;
    if (s_history_mode && allow_attention && s_group_manual && !group_busy() &&
        now - s_history_last_input_us >= 30000000) {
        s_group_manual = false;
        became_idle = true;
    }
    const status_presentation_t *p = &st->presentation;
    const bool lease_valid = p->active && (p->persistent || p->deadline_us > now);
    if (allow_attention && p->generation > s_group_generation &&
        (!group_busy() || (s_group_manual && (s_history_mode || p->urgency < 2)))) {
        s_group_generation = p->generation;
        if (!group_busy() && lease_valid && reachable(p->id) &&
            (!s_group_manual || (!s_history_mode && p->urgency >= 2))) {
            deck_select_id(&s_deck, p->id);
            s_group_home = false; s_group_manual = false; s_group_auto = true;
            s_group_present_id = p->id;
            s_group_deadline = p->deadline_us; s_group_persistent = p->persistent;
        }
    }
    if (s_group_auto && p->generation == s_group_generation) {
        /* A renewed sync or same-generation message may never renew a lease. */
        if (!p->persistent && (s_group_persistent || p->deadline_us < s_group_deadline)) {
            s_group_deadline = p->deadline_us; s_group_persistent = false;
        }
        if (allow_attention && !group_busy() && (!p->active || !reachable(s_group_present_id) ||
            (!s_group_persistent && now >= s_group_deadline))) {
            s_group_auto = false; s_group_home = !s_history_mode;
        }
    }
    if (count == 0) {
        if (previous_count > 0 || s_group_auto) s_group_home = !s_history_mode;
        s_group_auto = false;
        if (s_group_home) s_group_manual = false;
    }
    if (s_history_mode) {
        s_group_home = false;
        if (count == 0) s_group_manual = false;
    }
    const int position = deck_position(&s_deck);
    int neighbors[3] = {0, s_deck.has_focus ? s_deck.focus_id : 0, 0};
    if (position > 0) neighbors[0] = s_deck.ids[position - 1];
    if (position >= 0 && position + 1 < count) neighbors[2] = s_deck.ids[position + 1];
    for (int i = 0; i < st->notif_count; i++) {
        for (int slot = 0; slot < 3; slot++) {
            if (neighbors[slot] && st->notifs[i].id == neighbors[slot]) out->cards[slot] = st->notifs[i];
        }
    }
    out->dashboard = st->dashboard; out->overflow = st->notif_overflow;
    out->stale = !st->got_sync || !link_host_connected() || !st->last_rx_us ||
                  now - st->last_rx_us > STATUS_STALE_TIMEOUT_US;
    out->actions_enabled = st->actions_enabled;
    out->action_pending = st->action_pending;
    out->action_pending_id = st->action_pending_id;
    out->action_pending_open_rev = st->action_pending_open_rev;
    out->action_feedback = st->action_feedback;
    out->action_feedback_id = st->action_feedback_id;
    out->action_feedback_open_rev = st->action_feedback_open_rev;
    out->action_feedback_until_us = st->action_feedback_until_us;
    out->open_enabled = !out->stale && !s_group_home &&
        state_action_open_enabled(st, &out->cards[1], now);
    out->open_pending = st->action_pending &&
        st->action_pending_id == out->cards[1].id &&
        st->action_pending_open_rev == out->cards[1].open_revision;
    st->deck_enabled = s_visible; st->deck_stale = out->stale;
    st->deck_reachable = count; st->deck_position = position + 1;
    st->deck_focus_id = s_deck.focus_id; st->deck_next_id = neighbors[2];
    st->grouped_home = s_group_home; st->grouped_manual = s_group_manual;
    st->grouped_generation = s_group_generation;
    st->grouped_presenting = s_group_auto;
    st->grouped_present_id = s_group_present_id;
    st->grouped_deadline_us = s_group_deadline;
    st->grouped_persistent = s_group_persistent;
    state_unlock();
    if (became_idle) proto_send_input_history_idle(s_group_generation);
}

static void grouped_bind_except(const snapshot_t *view, int frozen_slot)
{
    const bool multiple = s_deck.count > 1;
    for (int i = 0; i < 3; i++) {
        if (i == frozen_slot) continue;
        card_view_t *slot = &s_cards[i];
        const status_notif_t *n = &view->cards[i];
        slot->valid = n->valid; slot->id = n->id;
        hide(slot->root, !slot->valid);
        hide(slot->dismiss, i != 1);
        hide(slot->open, i != 1 || !view->actions_enabled);
        const int height = s_history_mode ? (multiple ? 140 : 164) : (multiple ? 120 : 144);
        lv_obj_set_size(slot->root, 464, height);
        lv_obj_set_size(slot->accent, 3, height - 20);
        const int header_width = i == 1
            ? (view->actions_enabled ? OPEN_X - 24 : 464 - DISMISS_WIDTH - 24)
            : 440;
        lv_obj_set_pos(slot->app, 12, 4); lv_obj_set_width(slot->app, header_width);
        lv_obj_set_pos(slot->title, 12, 24); lv_obj_set_width(slot->title, header_width);
        lv_obj_set_style_text_font(slot->body,
            s_history_mode ? &status_text_16 : &status_text_20, 0);
        lv_obj_set_pos(slot->body, 12, s_history_mode ? 56 : 52);
        lv_obj_set_size(slot->body, 440, s_history_mode
            ? (multiple ? 66 : view->overflow ? 82 : 90)
            : (multiple ? 52 : view->overflow ? 72 : 84));
        lv_obj_set_pos(slot->position, 12,
            s_history_mode ? (multiple ? 124 : 148) : (multiple ? 104 : 128));
        lv_obj_set_width(slot->position, 440);
        lv_obj_set_x(slot->dismiss, OPEN_X + DISMISS_WIDTH + CONTROL_GAP);
        lv_obj_set_pos(slot->open, OPEN_X, 0);
        if (!slot->valid) continue;
        const lv_color_t accent = lv_color_hex(n->urgency >= 2 ? CRITICAL : ACCENT);
        if (!lv_color_eq(lv_obj_get_style_bg_color(slot->accent, 0), accent)) {
            lv_obj_set_style_bg_color(slot->accent, accent, 0);
        }
        char metadata[80];
        if (s_history_mode && i == 1) {
            int64_t age_s = (esp_timer_get_time() - n->history_updated_us) / 1000000;
            if (age_s < 0) age_s = 0;
            if (age_s < 60) snprintf(metadata, sizeof(metadata), "%s · now", n->app);
            else if (age_s < 3600) snprintf(metadata, sizeof(metadata), "%s · %lldm ago",
                n->app, (long long)(age_s / 60));
            else snprintf(metadata, sizeof(metadata), "%s · %lldh ago",
                n->app, (long long)(age_s / 3600));
            text(slot->app, metadata);
        } else text(slot->app, i == 1 ? n->app : n->summary);
        grouped_title(slot, n->summary[0] ? n->summary : "Notification",
                      i == 1 && view->actions_enabled, header_width);
        text(slot->body, n->body);
        if (i == 1 && view->actions_enabled) {
            const bool ready = view->open_enabled;
            const bool pending = view->open_pending;
            const uint32_t color = ready ? ACCENT : SECONDARY;
            lv_obj_set_style_bg_color(slot->open,
                lv_color_hex(ready ? 0x213641 : 0x202A33), 0);
            lv_obj_set_style_border_color(slot->open, lv_color_hex(color), 0);
            text_color(slot->open_label, color);
            text(slot->open_label, pending ? "…" : "Open");
        }
        position_text(slot, view->overflow, view);
    }
    grouped_position(0);
}

static void grouped_bind(const snapshot_t *view)
{
    grouped_bind_except(view, -1);
}

static void grouped_lifecycle_exec(void *var, int32_t offset)
{
    (void)var;
    grouped_position(offset);
}

static void grouped_lifecycle_cancel(void)
{
    lv_anim_delete(&s_group_lifecycle, grouped_lifecycle_exec);
    s_group_lifecycle.active = false;
    s_group_lifecycle.kind = GROUP_LIFECYCLE_NONE;
    s_group_lifecycle.generation++;
}

static void grouped_lifecycle_completed(lv_anim_t *animation)
{
    const uint32_t generation = (uint32_t)(uintptr_t)lv_anim_get_user_data(animation);
    if (!s_group_lifecycle.active || generation != s_group_lifecycle.generation) return;
    s_group_lifecycle.active = false;
    s_group_lifecycle.kind = GROUP_LIFECYCLE_NONE;
    /* Reconcile the latest cache/lease rather than restoring the captured
     * outgoing record. Arrivals while moving coalesce into the current state. */
    grouped_tick(STATE_DIRTY_NOTIF);
}

static void grouped_lifecycle_start(group_lifecycle_kind_t kind, int offset,
                                    int to_id)
{
    s_group_lifecycle.kind = kind;
    s_group_lifecycle.active = true;
    s_group_lifecycle.to_id = to_id;
    s_group_lifecycle.generation++;
    grouped_position(offset);
    lv_anim_t animation;
    lv_anim_init(&animation);
    lv_anim_set_var(&animation, &s_group_lifecycle);
    lv_anim_set_exec_cb(&animation, grouped_lifecycle_exec);
    lv_anim_set_values(&animation, offset, 0);
    lv_anim_set_duration(&animation, GROUP_LIFECYCLE_MS);
    lv_anim_set_path_cb(&animation, lv_anim_path_ease_out);
    lv_anim_set_user_data(&animation, (void *)(uintptr_t)s_group_lifecycle.generation);
    lv_anim_set_completed_cb(&animation, grouped_lifecycle_completed);
    if (!lv_anim_start(&animation)) grouped_lifecycle_cancel();
}

static void grouped_lifecycle_change(bool previous_home, int previous_id)
{
    const int current_id = s_view.cards[1].valid ? s_view.cards[1].id : 0;
    if (previous_home != s_group_home) {
        if (!s_group_home) grouped_bind(&s_view);
        /* Keep the outgoing Notifications widgets for the return to Home. */
        grouped_lifecycle_start(GROUP_LIFECYCLE_GROUP,
            s_group_home ? -480 : 480, s_group_home ? 0 : current_id);
    } else if (!s_group_home && (current_id > 0 || s_history_mode) && previous_id != current_id) {
        int previous_position = -1;
        for (int i = 0; i < s_deck.count; i++) {
            if (s_deck.ids[i] == previous_id) previous_position = i;
        }
        const bool from_above = s_history_mode && previous_id == 0
            ? true : previous_position >= 0 && deck_position(&s_deck) < previous_position;
        const int outgoing_slot = from_above ? 2 : 0;
        /* Reuse the offscreen neighbor as the outgoing full card, retaining
         * its text and geometry. This adds no widgets or rendering buffers. */
        const card_view_t outgoing = s_cards[1];
        s_cards[1] = s_cards[outgoing_slot];
        s_cards[outgoing_slot] = outgoing;
        grouped_bind_except(&s_view, outgoing_slot);
        const int pitch = s_history_mode ? HISTORY_CARD_PITCH_PX : GROUP_CARD_PITCH_PX;
        grouped_lifecycle_start(GROUP_LIFECYCLE_CARD, from_above ? -pitch : pitch, current_id);
    }
}

static void grouped_start_snap(deck_input_action_t action);

static void grouped_animation_completed(lv_anim_t *animation)
{
    uint32_t generation = (uint32_t)(uintptr_t)lv_anim_get_user_data(animation);
    if (generation != s_group_input.motion.generation) return;
    grouped_snapshot(&s_view, true);
    const group_input_t *g = &s_group_input;
    deck_input_action_t action = group_input_complete(&s_group_input, generation,
        reachable(g->source), reachable(g->newer), reachable(g->older), reachable(g->selected));
    if (action.kind == DECK_INPUT_ACTION_SNAP) { grouped_start_snap(action); return; }
    if (action.kind == DECK_INPUT_ACTION_SETTLED) {
        if (action.commit_target) {
            if (g->axis == GROUP_AXIS_HORIZONTAL) s_group_home = action.target_id == 0;
            if (action.target_id > 0) deck_select_id(&s_deck, action.target_id);
        }
        s_group_manual = !s_group_home;
        /* A presentation sent during capture may have advanced the generation
         * since takeover. Publish the settled group even after a short cancel. */
        proto_send_input_browse(s_group_home, s_group_generation);
    }
    /* The user's settle has already moved between these views. Bind its
     * destination before reconciling deferred attention, without replaying it. */
    grouped_snapshot(&s_view, false);
    grouped_bind(&s_view);
    grouped_tick(STATE_DIRTY_NOTIF);
}

static void grouped_start_snap(deck_input_action_t action)
{
    lv_anim_delete(&s_group_input.motion, grouped_animation_exec);
    lv_anim_t animation;
    lv_anim_init(&animation);
    lv_anim_set_var(&animation, &s_group_input.motion);
    lv_anim_set_exec_cb(&animation, grouped_animation_exec);
    lv_anim_set_values(&animation, s_group_offset, action.offset_px);
    lv_anim_set_duration(&animation, 180);
    lv_anim_set_path_cb(&animation, lv_anim_path_ease_out);
    lv_anim_set_user_data(&animation, (void *)(uintptr_t)action.generation);
    lv_anim_set_completed_cb(&animation, grouped_animation_completed);
    if (!lv_anim_start(&animation)) { grouped_cancel(); grouped_tick(STATE_DIRTY_NOTIF); }
}

static void grouped_takeover(void)
{
    s_history_last_input_us = esp_timer_get_time();
    s_group_auto = false; s_group_manual = !s_group_home;
    /* Normal attention may have been sent during a button hold or before
     * axis acquisition. Consume its generation without changing selection;
     * critical attention remains deferred until the gesture settles. */
    state_lock();
    status_state_t *st = state_get();
    if (st->presentation.urgency < 2 && st->presentation.generation > s_group_generation) {
        s_group_generation = st->presentation.generation;
        st->grouped_generation = s_group_generation;
    }
    state_unlock();
    proto_send_input_browse(s_group_home, s_group_generation);
}

static void grouped_input_cb(lv_event_t *event)
{
    const lv_event_code_t code = lv_event_get_code(event);
    if (code != LV_EVENT_PRESSED && code != LV_EVENT_PRESSING && code != LV_EVENT_RELEASED &&
        code != LV_EVENT_PRESS_LOST && code != LV_EVENT_INDEV_RESET) return;
    lv_indev_t *indev = lv_event_get_indev(event);
    if (!indev) return;
    if (code == LV_EVENT_PRESS_LOST || code == LV_EVENT_INDEV_RESET) {
        if (deck_input_busy(&s_group_input.motion)) {
            grouped_cancel(); s_group_skip_lifecycle = true;
            grouped_tick(STATE_DIRTY_NOTIF);
        }
        return;
    }
    lv_point_t point;
    lv_indev_get_point(indev, &point);
    if (code == LV_EVENT_PRESSED) {
        if (!s_visible || group_busy()) {
            if (s_group_lifecycle.active) lv_indev_wait_release(indev);
            return;
        }
        s_input_indev = indev;
        grouped_snapshot(&s_view, true);
        if (s_view.stale && !s_history_mode) { lv_indev_wait_release(indev); return; }
        s_history_last_input_us = esp_timer_get_time();
        s_group_input.vertical_only = s_history_mode;
        s_group_input.card_pitch_px = s_history_mode ? HISTORY_CARD_PITCH_PX : GROUP_CARD_PITCH_PX;
        const bool has_foreground = !s_group_home && s_cards[1].valid;
        lv_area_t open_bounds = {0}, dismiss_bounds = {0};
        const bool hit_open = has_foreground && s_view.actions_enabled &&
                              button_hit(s_cards[1].open, point.x, point.y, &open_bounds);
        const bool hit_dismiss = has_foreground &&
                                 button_hit(s_cards[1].dismiss, point.x, point.y, &dismiss_bounds);
        group_input_press(&s_group_input, point.x, point.y, esp_timer_get_time(),
            s_group_home, s_deck.count > 0 ? s_cards[1].id : GROUP_EMPTY_NOTIFICATIONS_ID,
            s_deck.count > 0,
            s_cards[0].id, s_cards[0].valid, s_cards[2].id, s_cards[2].valid,
            hit_open || hit_dismiss,
            !s_group_home && point.y >= 148 && s_cards[2].valid);
        if (hit_open) {
            group_input_capture_control(&s_group_input, GROUP_CONTROL_OPEN,
                open_bounds.x1, open_bounds.y1,
                open_bounds.x2 - open_bounds.x1 + 1,
                open_bounds.y2 - open_bounds.y1 + 1,
                s_cards[1].id, s_view.cards[1].open_revision,
                s_view.open_enabled);
        } else if (hit_dismiss) {
            group_input_capture_control(&s_group_input, GROUP_CONTROL_DISMISS,
                dismiss_bounds.x1, dismiss_bounds.y1,
                dismiss_bounds.x2 - dismiss_bounds.x1 + 1,
                dismiss_bounds.y2 - dismiss_bounds.y1 + 1,
                s_cards[1].id, 0, true);
        }
    } else if (indev == s_input_indev && deck_input_busy(&s_group_input.motion)) {
        grouped_snapshot(&s_view, true);
        if (s_view.stale && !s_history_mode) { grouped_cancel(); grouped_tick(STATE_DIRTY_NOTIF); return; }
        s_history_last_input_us = esp_timer_get_time();
        const uint32_t generation = s_group_input.motion.generation;
        deck_input_action_t invalid = grouped_validate();
        if (invalid.kind == DECK_INPUT_ACTION_SNAP) grouped_start_snap(invalid);
        else if (generation != s_group_input.motion.generation) {
            lv_anim_delete(&s_group_input.motion, grouped_animation_exec);
            grouped_bind(&s_view);
        }
        if (code == LV_EVENT_PRESSING) {
            group_axis_t old_axis = s_group_input.axis;
            int offset = group_input_move(&s_group_input, point.x, point.y, esp_timer_get_time());
            if (old_axis == GROUP_AXIS_NONE && s_group_input.axis != GROUP_AXIS_NONE) grouped_takeover();
            if (s_group_input.motion.state == DECK_INPUT_DRAGGING) grouped_position(offset);
        } else if (code == LV_EVENT_RELEASED) {
            deck_input_action_t action = group_input_release_at(
                &s_group_input, point.x, point.y, esp_timer_get_time());
            if (action.kind == DECK_INPUT_ACTION_SNAP || action.kind == DECK_INPUT_ACTION_NEXT_TAP) {
                if (action.kind == DECK_INPUT_ACTION_NEXT_TAP) grouped_takeover();
                grouped_start_snap(action);
            } else if (action.kind == DECK_INPUT_ACTION_OPEN && reachable(action.target_id)) {
                grouped_takeover();
                (void)proto_send_input_activate(action.target_id, action.open_revision);
                grouped_tick(STATE_DIRTY_NOTIF);
            } else if (action.kind == DECK_INPUT_ACTION_DISMISS && reachable(action.target_id)) {
                grouped_takeover();
                state_hide_notif(action.target_id);
                proto_send_input_dismiss(action.target_id);
                grouped_tick(STATE_DIRTY_NOTIF);
            } else if (!group_busy()) grouped_tick(STATE_DIRTY_NOTIF);
        }
    }
}

static void grouped_tick(uint32_t dirty)
{
    state_history_tick(esp_timer_get_time());
    state_action_tick(esp_timer_get_time());
    if (!link_host_connected()) state_action_disconnect();
    if (s_group_input.motion.state == DECK_INPUT_IGNORED && s_input_indev &&
        lv_indev_get_state(s_input_indev) == LV_INDEV_STATE_RELEASED) {
        group_input_release(&s_group_input, esp_timer_get_time());
    }
    const bool previous_home = s_group_home;
    const int previous_id = s_cards[1].valid ? s_cards[1].id : 0;
    const int previous_session = s_group_session;
    const bool previous_stale = s_view.stale;
    const bool gesture_busy = deck_input_busy(&s_group_input.motion);
    bool skip_lifecycle = s_group_skip_lifecycle;
    s_group_skip_lifecycle = false;
    grouped_snapshot(&s_view, true);
    if (s_group_lifecycle.active && ((s_view.stale && !s_history_mode) ||
        (s_group_lifecycle.to_id > 0 && !reachable(s_group_lifecycle.to_id)))) {
        grouped_lifecycle_cancel();
        skip_lifecycle = true;
    }
    if (deck_input_busy(&s_group_input.motion)) {
        if (s_view.stale && !s_history_mode) grouped_cancel();
        else {
            uint32_t generation = s_group_input.motion.generation;
            deck_input_action_t action = grouped_validate();
            if (action.kind == DECK_INPUT_ACTION_SNAP) grouped_start_snap(action);
            else if (generation != s_group_input.motion.generation) {
                lv_anim_delete(&s_group_input.motion, grouped_animation_exec);
                grouped_bind(&s_view);
            }
        }
    }
    if (s_view.stale && !s_history_mode) { s_group_home = true; s_group_auto = false; }
    if (!skip_lifecycle && !s_group_lifecycle.active && !gesture_busy &&
        (!s_view.stale || s_history_mode) && (!previous_stale || s_history_mode) &&
        previous_session == s_group_session) {
        grouped_lifecycle_change(previous_home, previous_id);
    }
    lv_obj_set_y(s_viewport, s_history_mode ? 0 : 20);
    lv_obj_set_height(s_viewport, s_history_mode ? 172 : 152);
    lv_obj_set_width(s_viewport, 480);
    hide(s_idle, s_history_mode); hide(s_viewport, s_view.stale && !s_history_mode);
    if (!group_busy() || (s_view.stale && !s_history_mode)) {
        hide(s_group_empty, s_deck.count > 0 || (s_view.stale && !s_history_mode));
        char empty[64];
        strlcpy(empty, s_history_mode ? "No recent notifications" : "No notifications", sizeof(empty));
        if (s_view.overflow > 0) snprintf(empty, sizeof(empty), "%d uncached notifications", s_view.overflow);
        text(s_group_empty, empty);
    }
    hide(s_group_cue, s_history_mode);
    if (!group_busy()) grouped_bind(&s_view);
    char cue[80];
    snprintf(cue, sizeof(cue), s_group_home ? "HOME   /   NOTIFICATIONS %d >" : "< HOME   /   NOTIFICATIONS %d",
             s_deck.count + s_view.overflow);
    text(s_group_cue, cue);
    char message[64] = "";
    if (s_view.stale) strlcpy(message, "Host disconnected", sizeof(message));
    text(s_message, message);
    if (dirty & STATE_DIRTY_DASHBOARD) transient(&s_view.dashboard);
    metrics(&s_view, !s_group_home); clocks();
}

void ui_deck_tick(uint32_t dirty)
{
    if (!s_visible) {
        return;
    }
    state_lock();
    const bool grouped = state_get()->grouped_enabled;
    state_unlock();
    if (grouped != s_group_mode) {
        cancel_interaction();
        grouped_cancel();
        s_group_mode = grouped;
        s_group_session = 0;
        s_group_home = true;
    }
    if (s_group_mode) {
        grouped_tick(dirty);
        return;
    }
    hide(s_group_cue, true);
    hide(s_group_empty, true);
    lv_obj_set_y(s_viewport, 0);
    lv_obj_set_height(s_viewport, 172);
    lv_obj_set_width(s_viewport, 472);
    lv_obj_set_x(s_idle, 0);
    lv_obj_set_x(s_viewport, 0);
    if (deck_input_state(&s_input) == DECK_INPUT_IGNORED && s_input_indev
            && lv_indev_get_state(s_input_indev) == LV_INDEV_STATE_RELEASED) {
        deck_input_release(&s_input, esp_timer_get_time());
    }
    snapshot_t *view = &s_view;
    snapshot(view, true);
    if (deck_input_busy(&s_input)) {
        if (view->stale) {
            cancel_interaction();
        } else {
            const uint32_t generation = deck_input_generation(&s_input);
            const deck_input_action_t action = validate_input();
            if (action.kind == DECK_INPUT_ACTION_SNAP) {
                start_snap(action);
            } else if (generation != deck_input_generation(&s_input)) {
                lv_anim_delete(&s_input, animation_exec);
                position_cards(0);
                bind_cards(view);
            }
        }
    }
    if (dirty & STATE_DIRTY_DASHBOARD) {
        transient(&view->dashboard);
    }
    const bool active = s_deck.has_focus && !view->stale;
    hide(s_viewport, !active);
    hide(s_idle, active);
    if (active) {
        if (!deck_input_busy(&s_input)) {
            bind_cards(view);
        } else {
            for (int i = 0; i < 3; i++) {
                position_text(&s_cards[i], view->overflow, view);
            }
        }
    } else {
        if (view->stale) {
            text(s_message, "Host disconnected");
        } else if (view->overflow > 0) {
            char message[64];
            snprintf(message, sizeof(message), "%d uncached notifications", view->overflow);
            text(s_message, message);
        } else {
            text(s_message, "");
        }
    }
    metrics(view, active);
    clocks();
}

void ui_deck_show(bool visible)
{
    if (!visible && s_visible) {
        cancel_interaction();
        grouped_cancel();
    }
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
    s_last_clock_second = -1;
    deck_input_init(&s_input);
    group_input_init(&s_group_input);
    s_small = small;
    s_meta = meta;
    s_root = box(parent, 0, 0, 640, 172, BACKGROUND, 0);
    lv_obj_t *rail = box(s_root, 0, 0, 160, 172, RAIL, 0);
    /* A press starting on the rail stays owned there even if it moves right. */
    lv_obj_add_flag(rail, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK);
    box(s_root, 159, 0, 1, 172, 0x2C3B49, 0);
    s_rail_clock = label(rail, 10, 7, 144, 48, &lv_font_montserrat_40, FOREGROUND, "--:--");
    s_rail_footer = label(rail, 12, 156, 136, 16, s_small, SECONDARY, "");
    const char *names[] = {"CPU", "MEM", "DN", "UP"};
    for (int i = 0; i < 4; i++) {
        s_metric_names[i] = label(rail, 12, 61 + 24 * i, 36, 21, s_meta, SECONDARY, names[i]);
        s_metric_values[i] = label(rail, 52, 58 + 24 * i, 96, 26,
                                   &status_text_20, FOREGROUND, "--");
        lv_obj_set_style_text_align(s_metric_values[i], LV_TEXT_ALIGN_RIGHT, 0);
    }
    s_content = box(s_root, 160, 0, 480, 172, BACKGROUND, 0);
    s_idle = box(s_content, 0, 0, 480, 172, BACKGROUND, 0);
    lv_obj_add_flag(s_idle, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK);
    lv_obj_add_event_cb(s_idle, input_cb, LV_EVENT_ALL, NULL);
    s_idle_clock = label(s_idle, 12, 35, 456, 64, &status_clock_80, FOREGROUND, "--:--");
    lv_obj_set_style_text_align(s_idle_clock, LV_TEXT_ALIGN_CENTER, 0);
    s_date = label(s_idle, 12, 112, 456, 21, s_meta, SECONDARY, "");
    lv_obj_set_style_text_align(s_date, LV_TEXT_ALIGN_CENTER, 0);
    s_message = label(s_idle, 12, 138, 456, 26, &status_text_20, WARNING, "");
    lv_obj_set_style_text_align(s_message, LV_TEXT_ALIGN_CENTER, 0);
    s_idle_transient = label(s_idle, 12, 141, 456, 21, s_meta, SECONDARY, "");
    lv_obj_set_style_text_align(s_idle_transient, LV_TEXT_ALIGN_CENTER, 0);

    /* The viewport ends at x=632, keeping the 8 px outer margin. */
    s_viewport = box(s_content, 0, 0, 472, 172, BACKGROUND, 0);
    lv_obj_add_flag(s_viewport, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK);
    lv_obj_add_event_cb(s_viewport, input_cb, LV_EVENT_ALL, NULL);
    s_group_empty = label(s_viewport, 12, 50, 456, 30, &status_text_22, SECONDARY, "");
    lv_obj_set_style_text_align(s_group_empty, LV_TEXT_ALIGN_CENTER, 0);
    hide(s_group_empty, true);
    for (int i = 0; i < 3; i++) {
        card_view_t *slot = &s_cards[i];
        slot->root = box(s_viewport, 8 + (i - 1) * 400, 8, 392, 156, SURFACE, 8);
        lv_obj_add_flag(slot->root, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK | LV_OBJ_FLAG_EVENT_BUBBLE);
        slot->accent = box(slot->root, 0, 10, 3, 136, ACCENT, 2);
        slot->app = label(slot->root, 12, 6, 322, 20, s_meta, ACCENT, "");
        slot->title = label(slot->root, 12, 28, 328, 28, &status_text_22, FOREGROUND, "");
        slot->body = label(slot->root, 12, 58, 368, 78, &status_text_20, FOREGROUND, "");
        lv_obj_set_style_text_line_space(slot->body, 0, 0);
        slot->position = label(slot->root, 12, 140, 368, 16, s_small, SECONDARY, "");
        slot->dismiss = dismiss_button(slot->root, 392 - DISMISS_WIDTH);
        open_button(slot->root, &slot->open, &slot->open_label);
    }
    s_group_cue = label(s_root, 172, 1, 456, 18, s_small, SECONDARY, "");
    ui_deck_show(false);
}
