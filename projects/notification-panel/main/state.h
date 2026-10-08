#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "cJSON.h"
#include "dashboard.h"
#include "backlight_policy.h"

#define STATUS_MAX_ZONES        8
#define STATUS_ZONE_ID_MAX      16
#define STATUS_ZONE_KIND_MAX    12
#define STATUS_ZONE_TEXT_MAX    96
#define STATUS_ZONE_FORMAT_MAX  16
#define STATUS_LEGACY_NOTIFS    8
#define STATUS_MAX_NOTIFS       32
#define STATUS_NOTIF_APP_MAX    32
#define STATUS_NOTIF_SUMMARY_MAX 64
#define STATUS_NOTIF_BODY_MAX   512
#define STATUS_STALE_TIMEOUT_US (10 * 1000 * 1000)

typedef struct {
    char id[STATUS_ZONE_ID_MAX];
    char kind[STATUS_ZONE_KIND_MAX];
    int w;
    char text[STATUS_ZONE_TEXT_MAX];
    float value;
    bool has_value;
    char format[STATUS_ZONE_FORMAT_MAX];
    char align[8];
    uint32_t color;
    bool has_color;
} status_zone_t;

#define STATUS_NOTIF_BODY_RUNS_MAX 16
typedef struct {
    uint16_t start, end;
    uint32_t style; /* 1 bold, 2 italic, 3 both; UTF-8 byte offsets. */
} status_body_run_t;

typedef struct {
    bool valid;
    int64_t epoch; /* UTC seconds */
    int offset;    /* seconds east of UTC */
} status_clock_t;

typedef struct {
    bool valid;
    char state[12];
    char title[64];
    char artist[64];
    char album[64];
    float pos;
    float len;
    int64_t updated_us;
} status_media_t;

typedef struct {
    bool valid;
    int id;
    char app[STATUS_NOTIF_APP_MAX];
    char summary[STATUS_NOTIF_SUMMARY_MAX];
    char body[STATUS_NOTIF_BODY_MAX];
    status_body_run_t body_runs[STATUS_NOTIF_BODY_RUNS_MAX];
    uint8_t body_run_count;
    int urgency;
    int open_revision;
    bool open_ready;
    int history_revision;
    int64_t history_updated_us, history_deadline_us;
} status_notif_t;

typedef enum {
    STATUS_ACTION_FEEDBACK_NONE,
    STATUS_ACTION_FEEDBACK_SENT,
    STATUS_ACTION_FEEDBACK_UNAVAILABLE,
    STATUS_ACTION_FEEDBACK_TRY_AGAIN,
    STATUS_ACTION_FEEDBACK_NO_CONFIRMATION,
} status_action_feedback_t;

typedef struct {
    int generation, id, urgency;
    int64_t deadline_us;
    bool active, persistent;
} status_presentation_t;

typedef struct {
    status_zone_t zones[STATUS_MAX_ZONES];
    int zone_count;
    status_clock_t clock;
    status_media_t media;
    status_dashboard_t dashboard;
    status_notif_t *notifs;
    int notif_capacity;
    int cache_limit;
    int notif_count;
    int notif_overflow;
    int hidden_ids[STATUS_MAX_NOTIFS];
    int hidden_count;
    uint32_t notif_focus_seq;
    int notif_focus_id;
    int notif_focus_urgency;
    int64_t notif_focus_us;
    uint32_t notif_critical_seq;
    int notif_critical_id;
    int64_t notif_critical_us;
    /* Published by the LVGL timer for read-only acceptance diagnostics. */
    bool deck_enabled, deck_stale;
    int deck_reachable, deck_position, deck_focus_id, deck_next_id;
    bool grouped_enabled;
    bool history_enabled;
    int grouped_session;
    bool actions_enabled;
    bool action_pending;
    int action_pending_session;
    uint32_t action_pending_boot_id;
    int action_pending_id, action_pending_open_rev, action_pending_request;
    int64_t action_pending_deadline_us, action_cooldown_until_us;
    bool action_request_exhausted;
    int action_next_request;
    bool action_blocked;
    int action_blocked_id, action_blocked_open_rev;
    status_action_feedback_t action_feedback;
    int action_feedback_id, action_feedback_open_rev;
    int64_t action_feedback_until_us;
    status_presentation_t presentation;
    /* UI-published state for acceptance readback. */
    bool grouped_home, grouped_manual, grouped_presenting;
    int grouped_generation, grouped_present_id;
    int64_t grouped_deadline_us;
    bool grouped_persistent;
    int64_t last_rx_us;
    bool got_sync;
    backlight_policy_t backlight;
    uint8_t backlight_applied_percent;
    uint32_t brightness_clicks, power_clicks;
    bool brightness_pressed, power_pressed;
} status_state_t;

#define STATE_DIRTY_BAR   0x1
#define STATE_DIRTY_NOTIF 0x2
#define STATE_DIRTY_DASHBOARD 0x4

void state_init(void);

/* The state mutex guards every access; state_get() returns the live struct and
 * the caller must hold the lock. */
void state_lock(void);
void state_unlock(void);
status_state_t *state_get(void);

/* Dirty flags: set by any change, returned and cleared by the UI. */
uint32_t state_take_dirty(void);
void state_mark_dirty(uint32_t bits);

/* Any recognized host message counts as liveness. */
void state_note_rx(void);
/* Display settings are validated and applied atomically; queries are inert. */
bool state_apply_backlight(const cJSON *obj);
/* Local controls never renew host liveness or change the host screen decision. */
void state_apply_buttons(bool brightness_click, bool power_click,
                         bool brightness_pressed, bool power_pressed);

void state_apply_bar(const cJSON *obj);
bool state_apply_clock(const cJSON *obj, int64_t *epoch, int *offset);
void state_apply_media(const cJSON *obj);
bool state_apply_dashboard(const cJSON *obj);
void state_apply_notify(const cJSON *obj);
bool state_apply_present(const cJSON *obj);
bool state_grouped_session_matches(const cJSON *obj);
bool state_apply_card_action(const cJSON *obj);
bool state_apply_action_result(const cJSON *obj, uint32_t boot_id);
void state_action_tick(int64_t now_us);
void state_history_tick(int64_t now_us);
void state_action_disconnect(void);
bool state_action_open_enabled(const status_state_t *state,
                               const status_notif_t *notif, int64_t now_us);
bool state_action_begin(uint32_t boot_id, int id, int open_revision,
                        int64_t now_us, int *session, int *request);
void state_apply_close(const cJSON *obj);
void state_apply_sync(const cJSON *obj);

/* Chunked full sync is available only when both PSRAM card buffers exist. */
int state_card_sync_capacity(void);
bool state_sync_begin(const cJSON *obj);
bool state_sync_cards(const cJSON *obj);
bool state_sync_commit(const cJSON *obj, int64_t *epoch, int *offset, bool *has_clock);
void state_sync_abort(void);
bool state_sync_pending(void);
bool state_sync_timeout(void);

/* Read-only acceptance diagnostics; IDs remain in cache order, oldest first. */
void state_cards_status(int *ids, int *count, int *overflow, int *capacity);

/* Locally hidden after a device dismiss; unhidden by a replace. */
void state_hide_notif(int id);
