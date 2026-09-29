#include "state.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static status_state_t s_state;
static status_notif_t s_legacy_notifs[STATUS_LEGACY_NOTIFS];
static status_zone_t s_zone_scratch[STATUS_MAX_ZONES];
static struct {
    status_notif_t *cards;
    status_zone_t zones[STATUS_MAX_ZONES];
    int zone_count;
    status_clock_t clock;
    status_media_t media;
    status_dashboard_t dashboard;
    int tx;
    int count;
    int limit;
    int next_index;
    int overflow;
    int grouped_session;
    bool grouped, history, actions_enabled;
    int64_t started_us;
    bool active;
} s_stage;
static SemaphoreHandle_t s_mutex;
static uint32_t s_dirty;
static const char *TAG = "state";
/* Expired revisions stay suppressed until a genuine newer update. */
static struct { int id, revision; } s_history_expired[STATUS_MAX_NOTIFS];
static int s_history_expired_count;

#define SYNC_TIMEOUT_US (5 * 1000 * 1000)

static bool int_field(const cJSON *obj, const char *name, int min, int max, int *out);

static void copy_str(char *dst, size_t size, const cJSON *item)
{
    const char *src = cJSON_IsString(item) ? item->valuestring : "";
    if (strlcpy(dst, src, size) >= size) {
        /* Keep a truncated UTF-8 string valid for LVGL. */
        size_t end = size - 1;
        size_t lead = end;
        while (lead > 0 && ((unsigned char)dst[lead - 1] & 0xC0) == 0x80) {
            lead--;
        }
        if (lead > 0) {
            const unsigned char first = (unsigned char)dst[lead - 1];
            const size_t expected = first < 0x80 ? 1 : first < 0xE0 ? 2 : first < 0xF0 ? 3 : 4;
            if (end - (lead - 1) < expected) {
                dst[lead - 1] = '\0';
            }
        }
    }
}

static uint32_t parse_color(const cJSON *item)
{
    if (!cJSON_IsString(item) || item->valuestring[0] != '#') {
        return 0;
    }
    unsigned long value = strtoul(item->valuestring + 1, NULL, 16);
    return (uint32_t)value & 0xFFFFFF;
}

static int parse_zones(const cJSON *zones, status_zone_t *out, bool strict)
{
    if (!cJSON_IsArray(zones)) {
        return -1;
    }
    const int supplied = cJSON_GetArraySize(zones);
    if (strict && supplied > STATUS_MAX_ZONES) {
        return -1;
    }
    const int size = supplied < STATUS_MAX_ZONES ? supplied : STATUS_MAX_ZONES;
    for (int count = 0; count < size; count++) {
        const cJSON *item = cJSON_GetArrayItem(zones, count);
        if (!cJSON_IsObject(item)) {
            return -1;
        }
        status_zone_t *zone = &out[count];
        memset(zone, 0, sizeof(*zone));
        copy_str(zone->id, sizeof(zone->id), cJSON_GetObjectItemCaseSensitive(item, "id"));
        copy_str(zone->kind, sizeof(zone->kind), cJSON_GetObjectItemCaseSensitive(item, "kind"));
        copy_str(zone->text, sizeof(zone->text), cJSON_GetObjectItemCaseSensitive(item, "text"));
        copy_str(zone->format, sizeof(zone->format), cJSON_GetObjectItemCaseSensitive(item, "format"));
        copy_str(zone->align, sizeof(zone->align), cJSON_GetObjectItemCaseSensitive(item, "align"));

        const cJSON *w = cJSON_GetObjectItemCaseSensitive(item, "w");
        zone->w = cJSON_IsNumber(w) ? w->valueint : 0;
        const cJSON *value = cJSON_GetObjectItemCaseSensitive(item, "value");
        if (cJSON_IsNumber(value)) {
            zone->value = (float)value->valuedouble;
            zone->has_value = true;
        }
        const cJSON *color = cJSON_GetObjectItemCaseSensitive(item, "color");
        if (cJSON_IsString(color)) {
            zone->color = parse_color(color);
            zone->has_color = true;
        }
    }
    return size;
}

static status_clock_t parse_clock(const cJSON *obj)
{
    status_clock_t clock = {0};
    const cJSON *e = cJSON_GetObjectItemCaseSensitive(obj, "epoch");
    const cJSON *o = cJSON_GetObjectItemCaseSensitive(obj, "offset");
    if (cJSON_IsNumber(e)) {
        clock.valid = true;
        clock.epoch = (int64_t)e->valuedouble;
        clock.offset = cJSON_IsNumber(o) ? o->valueint : 0;
    }
    return clock;
}

static status_media_t parse_media(const cJSON *obj)
{
    status_media_t media = {0};
    if (cJSON_IsObject(obj)) {
        media.valid = true;
        copy_str(media.state, sizeof(media.state), cJSON_GetObjectItemCaseSensitive(obj, "state"));
        copy_str(media.title, sizeof(media.title), cJSON_GetObjectItemCaseSensitive(obj, "title"));
        copy_str(media.artist, sizeof(media.artist), cJSON_GetObjectItemCaseSensitive(obj, "artist"));
        copy_str(media.album, sizeof(media.album), cJSON_GetObjectItemCaseSensitive(obj, "album"));
        const cJSON *pos = cJSON_GetObjectItemCaseSensitive(obj, "pos");
        const cJSON *len = cJSON_GetObjectItemCaseSensitive(obj, "len");
        media.pos = cJSON_IsNumber(pos) ? (float)pos->valuedouble : 0;
        media.len = cJSON_IsNumber(len) ? (float)len->valuedouble : 0;
        media.updated_us = esp_timer_get_time();
    }
    return media;
}

static bool parse_notif(const cJSON *obj, status_notif_t *notif)
{
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(obj, "id");
    if (!cJSON_IsObject(obj) || !cJSON_IsNumber(id) || id->valuedouble != (double)id->valueint) {
        return false;
    }
    memset(notif, 0, sizeof(*notif));
    notif->valid = true;
    notif->id = id->valueint;
    copy_str(notif->app, sizeof(notif->app), cJSON_GetObjectItemCaseSensitive(obj, "app"));
    copy_str(notif->summary, sizeof(notif->summary), cJSON_GetObjectItemCaseSensitive(obj, "summary"));
    const cJSON *history = cJSON_GetObjectItemCaseSensitive(obj, "history");
    if (history != NULL) {
        int age_ms, remaining_ms;
        if (!cJSON_IsObject(history) || cJSON_GetArraySize(history) != 3 ||
            !int_field(history, "rev", 1, INT32_MAX, &notif->history_revision) ||
            !int_field(history, "age_ms", 0, INT32_MAX, &age_ms) ||
            !int_field(history, "remaining_ms", 1, INT32_MAX, &remaining_ms)) return false;
        const int64_t now = esp_timer_get_time();
        notif->history_updated_us = now - (int64_t)age_ms * 1000;
        notif->history_deadline_us = now + (int64_t)remaining_ms * 1000;
    }
    copy_str(notif->body, history ? sizeof(notif->body) : 160,
             cJSON_GetObjectItemCaseSensitive(obj, "body"));
    const cJSON *urgency = cJSON_GetObjectItemCaseSensitive(obj, "urgency");
    notif->urgency = cJSON_IsNumber(urgency) ? urgency->valueint : 1;
    return true;
}

static bool parse_action_open(const cJSON *obj, status_notif_t *notif)
{
    const cJSON *open = cJSON_GetObjectItemCaseSensitive(obj, "open");
    if (!cJSON_IsObject(open) || cJSON_GetArraySize(open) != 2) return false;
    const cJSON *revision = cJSON_GetObjectItemCaseSensitive(open, "rev");
    const cJSON *state = cJSON_GetObjectItemCaseSensitive(open, "state");
    if (
        !cJSON_IsNumber(revision) || revision->valuedouble != (double)revision->valueint ||
        revision->valueint < 1 || revision->valueint > INT32_MAX ||
        !cJSON_IsString(state) ||
        (strcmp(state->valuestring, "ready") != 0 &&
         strcmp(state->valuestring, "unavailable") != 0)) {
        return false;
    }
    notif->open_revision = revision->valueint;
    notif->open_ready = strcmp(state->valuestring, "ready") == 0;
    return true;
}

static void clear_action_runtime_locked(bool clear_cooldown)
{
    s_state.action_pending = false;
    s_state.action_pending_session = 0;
    s_state.action_pending_boot_id = 0;
    s_state.action_pending_id = 0;
    s_state.action_pending_open_rev = 0;
    s_state.action_pending_request = 0;
    s_state.action_pending_deadline_us = 0;
    s_state.action_blocked = false;
    s_state.action_blocked_id = 0;
    s_state.action_blocked_open_rev = 0;
    s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
    s_state.action_feedback_id = 0;
    s_state.action_feedback_open_rev = 0;
    s_state.action_feedback_until_us = 0;
    if (clear_cooldown) s_state.action_cooldown_until_us = 0;
}

static void clear_presentation_locked(void)
{
    memset(&s_state.presentation, 0, sizeof(s_state.presentation));
}

static bool cached_visible_locked(int id)
{
    for (int i = 0; i < s_state.hidden_count; i++) {
        if (s_state.hidden_ids[i] == id) {
            return false;
        }
    }
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == id) {
            return true;
        }
    }
    return false;
}

void state_init(void)
{
    s_history_expired_count = 0;
    memset(&s_state, 0, sizeof(s_state));
    memset(&s_stage, 0, sizeof(s_stage));
    s_state.notifs = heap_caps_calloc(STATUS_MAX_NOTIFS, sizeof(status_notif_t),
                                      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_stage.cards = heap_caps_calloc(STATUS_MAX_NOTIFS, sizeof(status_notif_t),
                                      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_state.notifs == NULL || s_stage.cards == NULL) {
        heap_caps_free(s_state.notifs);
        heap_caps_free(s_stage.cards);
        s_state.notifs = s_legacy_notifs;
        s_stage.cards = NULL;
        s_state.notif_capacity = STATUS_LEGACY_NOTIFS;
        ESP_LOGW(TAG, "PSRAM card cache unavailable; using %d legacy slots", STATUS_LEGACY_NOTIFS);
    } else {
        s_state.notif_capacity = STATUS_MAX_NOTIFS;
        ESP_LOGI(TAG, "card cache ready: %d committed + %d staging slots in PSRAM",
                 STATUS_MAX_NOTIFS, STATUS_MAX_NOTIFS);
    }
    s_state.cache_limit = STATUS_LEGACY_NOTIFS;
    s_mutex = xSemaphoreCreateMutex();
    configASSERT(s_mutex);
}

void state_lock(void)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
}

void state_unlock(void)
{
    xSemaphoreGive(s_mutex);
}

status_state_t *state_get(void)
{
    return &s_state;
}

uint32_t state_take_dirty(void)
{
    state_lock();
    const uint32_t dirty = s_dirty;
    s_dirty = 0;
    state_unlock();
    return dirty;
}

void state_mark_dirty(uint32_t bits)
{
    state_lock();
    s_dirty |= bits;
    state_unlock();
}

void state_note_rx(void)
{
    state_lock();
    s_state.last_rx_us = esp_timer_get_time();
    state_unlock();
}

void state_apply_bar(const cJSON *obj)
{
    const cJSON *zones = cJSON_GetObjectItemCaseSensitive(obj, "zones");
    int count = parse_zones(zones, s_zone_scratch, false);
    if (count < 0) {
        count = 0;
    }

    state_lock();
    memcpy(s_state.zones, s_zone_scratch, sizeof(s_zone_scratch[0]) * (size_t)count);
    s_state.zone_count = count;
    s_dirty |= STATE_DIRTY_BAR;
    state_unlock();
}

bool state_apply_clock(const cJSON *obj, int64_t *epoch, int *offset)
{
    const cJSON *e = cJSON_GetObjectItemCaseSensitive(obj, "epoch");
    const cJSON *o = cJSON_GetObjectItemCaseSensitive(obj, "offset");
    if (!cJSON_IsNumber(e)) {
        return false;
    }

    state_lock();
    s_state.clock.valid = true;
    s_state.clock.epoch = (int64_t)e->valuedouble;
    s_state.clock.offset = cJSON_IsNumber(o) ? o->valueint : 0;
    state_unlock();

    if (epoch) {
        *epoch = (int64_t)e->valuedouble;
    }
    if (offset) {
        *offset = cJSON_IsNumber(o) ? o->valueint : 0;
    }
    return true;
}

void state_apply_media(const cJSON *obj)
{
    status_media_t media = parse_media(obj);
    state_lock();
    s_state.media = media;
    s_dirty |= STATE_DIRTY_BAR;
    state_unlock();
}

bool state_apply_dashboard(const cJSON *obj)
{
    status_dashboard_t dashboard;
    if (!dashboard_parse(obj, &dashboard)) {
        return false;
    }
    state_lock();
    s_state.dashboard = dashboard;
    s_dirty |= STATE_DIRTY_DASHBOARD;
    state_unlock();
    return true;
}

/* Caller holds the state mutex. Separate critical metadata protects a
 * critical arrival from a later normal in the same LVGL tick. */
static void request_focus(const status_notif_t *notif)
{
    s_state.notif_focus_seq++;
    s_state.notif_focus_id = notif->id;
    s_state.notif_focus_urgency = notif->urgency;
    s_state.notif_focus_us = esp_timer_get_time();
    if (notif->urgency >= 2) {
        s_state.notif_critical_seq++;
        s_state.notif_critical_id = notif->id;
        s_state.notif_critical_us = s_state.notif_focus_us;
    }
}

static void remove_notif_locked(int slot)
{
    if (slot < 0 || slot >= s_state.notif_count) {
        return;
    }
    memmove(&s_state.notifs[slot], &s_state.notifs[slot + 1],
            sizeof(s_state.notifs[0]) * (size_t)(s_state.notif_count - slot - 1));
    s_state.notif_count--;
}

static bool history_suppressed(const status_notif_t *card)
{
    for (int i = 0; i < s_history_expired_count; i++) {
        if (s_history_expired[i].id == card->id &&
            card->history_revision <= s_history_expired[i].revision) return true;
    }
    return false;
}

static void history_remember_expired(const status_notif_t *card)
{
    int slot = -1;
    for (int i = 0; i < s_history_expired_count; i++) {
        if (s_history_expired[i].id == card->id) {
            if (s_history_expired[i].revision >= card->history_revision) return;
            slot = i;
        }
    }
    if (slot < 0) {
        if (s_history_expired_count == STATUS_MAX_NOTIFS) {
            memmove(s_history_expired, s_history_expired + 1,
                    sizeof(s_history_expired[0]) * (STATUS_MAX_NOTIFS - 1));
            s_history_expired_count--;
        }
        slot = s_history_expired_count++;
    }
    s_history_expired[slot].id = card->id;
    s_history_expired[slot].revision = card->history_revision;
}

static bool history_reconcile(status_notif_t *card, const status_notif_t *previous)
{
    if (previous == NULL) return true;
    if (card->history_revision < previous->history_revision) return false;
    if (card->history_revision == previous->history_revision) {
        if (previous->history_deadline_us < card->history_deadline_us)
            card->history_deadline_us = previous->history_deadline_us;
        if (previous->history_updated_us < card->history_updated_us)
            card->history_updated_us = previous->history_updated_us;
    }
    return true;
}

void state_history_tick(int64_t now_us)
{
    state_lock();
    if (s_state.history_enabled) {
        for (int i = s_state.notif_count - 1; i >= 0; i--) {
            const status_notif_t *card = &s_state.notifs[i];
            if (card->history_deadline_us > now_us) continue;
            const int id = card->id;
            history_remember_expired(card);
            if (s_state.action_pending_id == id) clear_action_runtime_locked(false);
            if (s_state.presentation.id == id) {
                s_state.presentation.active = false;
                s_state.presentation.persistent = false;
                s_state.presentation.deadline_us = now_us;
            }
            remove_notif_locked(i);
            s_dirty |= STATE_DIRTY_NOTIF;
        }
    }
    state_unlock();
}

static void update_grouped_overflow_locked(bool has_total, int total)
{
    if (has_total) {
        s_state.notif_overflow = total > s_state.notif_count
            ? total - s_state.notif_count : 0;
    }
}

bool state_grouped_session_matches(const cJSON *obj)
{
    int session = 0;
    const bool valid_session = int_field(obj, "session", 1, INT32_MAX, &session);
    state_lock();
    const bool grouped = s_state.grouped_enabled;
    const bool matches = !grouped || (valid_session && session == s_state.grouped_session);
    state_unlock();
    return matches;
}

static void apply_notify(const cJSON *obj, bool unhide)
{
    status_notif_t parsed;
    if (!parse_notif(obj, &parsed)) {
        return;
    }
    const int nid = parsed.id;

    state_lock();
    if (s_state.grouped_enabled) {
        int session = 0;
        int strict_id = 0;
        int total_value = 0;
        const bool has_total = cJSON_GetObjectItemCaseSensitive(obj, "total") != NULL;
        const bool has_cached = cJSON_GetObjectItemCaseSensitive(obj, "cached") != NULL;
        const cJSON *cached = cJSON_GetObjectItemCaseSensitive(obj, "cached");
        const cJSON *urgency = cJSON_GetObjectItemCaseSensitive(obj, "urgency");
        if (!int_field(obj, "session", 1, INT32_MAX, &session) ||
            session != s_state.grouped_session ||
            !int_field(obj, "id", 1, INT32_MAX, &strict_id) ||
            strict_id != parsed.id || parsed.urgency < 0 || parsed.urgency > 2 ||
            (urgency != NULL && (!cJSON_IsNumber(urgency) ||
                                  urgency->valuedouble != (double)urgency->valueint)) ||
            (has_cached && !cJSON_IsBool(cached)) ||
            (has_total && !int_field(obj, "total", 0, INT32_MAX, &total_value))) {
            state_unlock();
            return;
        }
        if (s_state.actions_enabled && !parse_action_open(obj, &parsed)) {
            state_unlock();
            return;
        }
        if (s_state.history_enabled &&
            (parsed.history_revision == 0 || history_suppressed(&parsed))) {
            state_unlock();
            return;
        }

        int slot = -1;
        for (int i = 0; i < s_state.notif_count; i++) {
            if (s_state.notifs[i].id == nid) {
                slot = i;
                break;
            }
        }
        if (s_state.actions_enabled && slot >= 0 &&
            (parsed.open_revision < s_state.notifs[slot].open_revision ||
             (parsed.open_revision == s_state.notifs[slot].open_revision &&
              parsed.open_ready != s_state.notifs[slot].open_ready))) {
            state_unlock();
            return;
        }
        if (s_state.history_enabled && slot >= 0 &&
            !history_reconcile(&parsed, &s_state.notifs[slot])) {
            state_unlock();
            return;
        }
        /* A same-ID replacement becomes newest and clears a local hide. */
        for (int i = 0; i < s_state.hidden_count; i++) {
            if (s_state.hidden_ids[i] == nid) {
                memmove(&s_state.hidden_ids[i], &s_state.hidden_ids[i + 1],
                        sizeof(s_state.hidden_ids[0]) * (size_t)(s_state.hidden_count - i - 1));
                s_state.hidden_count--;
                break;
            }
        }
        if (has_cached && cJSON_IsFalse(cached)) {
            remove_notif_locked(slot);
            if (s_state.presentation.id == nid) {
                s_state.presentation.active = false;
                s_state.presentation.persistent = false;
                s_state.presentation.deadline_us = esp_timer_get_time();
            }
            update_grouped_overflow_locked(has_total, total_value);
            if (s_state.action_feedback_id == nid) {
                s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
                s_state.action_feedback_id = 0;
                s_state.action_feedback_open_rev = 0;
                s_state.action_feedback_until_us = 0;
            }
            s_dirty |= STATE_DIRTY_NOTIF;
            state_unlock();
            return;
        }

        if (slot >= 0) {
            if (s_state.action_feedback_id == nid &&
                s_state.action_feedback_open_rev != parsed.open_revision) {
                s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
                s_state.action_feedback_id = 0;
                s_state.action_feedback_open_rev = 0;
                s_state.action_feedback_until_us = 0;
            }
            remove_notif_locked(slot);
        }
        if (s_state.cache_limit > 0) {
            if (s_state.notif_count >= s_state.cache_limit) {
                remove_notif_locked(0);
            }
            s_state.notifs[s_state.notif_count++] = parsed;
        }
        if (s_state.action_blocked && s_state.action_blocked_id == nid &&
            parsed.open_revision > s_state.action_blocked_open_rev) {
            s_state.action_blocked = false;
        }
        if (!cached_visible_locked(s_state.presentation.id) && s_state.presentation.active) {
            s_state.presentation.active = false;
            s_state.presentation.persistent = false;
            s_state.presentation.deadline_us = esp_timer_get_time();
        }
        update_grouped_overflow_locked(has_total, total_value);
        s_dirty |= STATE_DIRTY_NOTIF;
        state_unlock();
        return;
    }
    bool was_hidden = false;
    /* A live notify can replace a locally hidden card. A periodic sync must
     * preserve the local dismiss for unchanged cards. */
    if (unhide) {
        for (int i = 0; i < s_state.hidden_count; i++) {
            if (s_state.hidden_ids[i] == nid) {
                was_hidden = true;
                memmove(&s_state.hidden_ids[i], &s_state.hidden_ids[i + 1],
                        sizeof(s_state.hidden_ids[0]) * (s_state.hidden_count - i - 1));
                s_state.hidden_count--;
                break;
            }
        }
    }
    const cJSON *cached = cJSON_GetObjectItemCaseSensitive(obj, "cached");
    const cJSON *total = cJSON_GetObjectItemCaseSensitive(obj, "total");
    if (unhide && cJSON_IsFalse(cached)) {
        /* A replacement of an older active ID can be outside the host's
         * newest-N selection. It still unhides the ID, but cannot displace a
         * selected card from this bounded cache. */
        for (int i = 0; i < s_state.notif_count; i++) {
            if (s_state.notifs[i].id == nid) {
                memmove(&s_state.notifs[i], &s_state.notifs[i + 1],
                        sizeof(s_state.notifs[0]) * (size_t)(s_state.notif_count - i - 1));
                s_state.notif_count--;
                break;
            }
        }
        if (cJSON_IsNumber(total) && total->valueint >= s_state.notif_count) {
            s_state.notif_overflow = total->valueint - s_state.notif_count;
        }
        s_dirty |= STATE_DIRTY_NOTIF;
        state_unlock();
        return;
    }
    if (s_state.cache_limit == 0) {
        if (cJSON_IsNumber(total) && total->valueint >= 0) {
            s_state.notif_overflow = total->valueint;
        }
        s_dirty |= STATE_DIRTY_NOTIF;
        state_unlock();
        return;
    }
    int slot = -1;
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == nid) {
            slot = i;
            break;
        }
    }
    const bool is_new = slot < 0;
    if (is_new) {
        if (s_state.notif_count < s_state.cache_limit) {
            slot = s_state.notif_count++;
        } else {
            /* Drop the oldest to make room for the newest, but keep counting it
             * so the "+N more" indicator stays truthful between syncs. */
            memmove(&s_state.notifs[0], &s_state.notifs[1],
                    sizeof(s_state.notifs[0]) * (size_t)(s_state.cache_limit - 1));
            slot = s_state.cache_limit - 1;
            if (s_state.notif_overflow < 999) {
                s_state.notif_overflow++;
            }
        }
    }

    s_state.notifs[slot] = parsed;
    if (unhide && (is_new || was_hidden || parsed.urgency >= 2)) {
        request_focus(&parsed);
    }

    if (cJSON_IsNumber(total) && total->valueint >= s_state.notif_count) {
        s_state.notif_overflow = total->valueint - s_state.notif_count;
    }

    s_dirty |= STATE_DIRTY_NOTIF;
    state_unlock();
}

void state_apply_notify(const cJSON *obj)
{
    apply_notify(obj, true);
}

bool state_apply_present(const cJSON *obj)
{
    int session = 0;
    int generation = 0;
    int id = 0;
    int remaining_ms = 0;
    int urgency = 0;
    if (!cJSON_IsObject(obj) ||
        !int_field(obj, "session", 1, INT32_MAX, &session) ||
        !int_field(obj, "generation", 1, INT32_MAX, &generation) ||
        !int_field(obj, "id", 1, INT32_MAX, &id) ||
        !int_field(obj, "remaining_ms", -1, INT32_MAX, &remaining_ms) ||
        !int_field(obj, "urgency", 0, 2, &urgency)) {
        return false;
    }

    const int64_t now = esp_timer_get_time();
    const bool persistent = remaining_ms == -1;
    const int64_t deadline = remaining_ms > 0
        ? now + (int64_t)remaining_ms * 1000 : now;
    state_lock();
    status_presentation_t *current = &s_state.presentation;
    if (!s_state.grouped_enabled || session != s_state.grouped_session ||
        !cached_visible_locked(id) || generation < current->generation) {
        state_unlock();
        return false;
    }

    if (generation == current->generation) {
        if (current->id != id || current->urgency != urgency) {
            state_unlock();
            return false;
        }
        if (!current->active) {
            const bool already_ended = remaining_ms == 0;
            state_unlock();
            return already_ended;
        }
        if (persistent) {
            /* An active persistent visit is unchanged; a finite visit cannot
             * become persistent under the same generation. */
            const bool unchanged = current->persistent;
            state_unlock();
            return unchanged;
        }
        if (remaining_ms > 0 && !current->persistent && deadline > current->deadline_us) {
            state_unlock();
            return false;
        }
        current->active = remaining_ms > 0;
        current->persistent = false;
        current->deadline_us = deadline;
    } else {
        current->generation = generation;
        current->id = id;
        current->urgency = urgency;
        current->active = remaining_ms != 0;
        current->persistent = persistent;
        current->deadline_us = deadline;
    }
    s_dirty |= STATE_DIRTY_NOTIF;
    state_unlock();
    return true;
}

void state_hide_notif(int id)
{
    state_lock();
    bool known = false;
    for (int i = 0; i < s_state.hidden_count; i++) {
        if (s_state.hidden_ids[i] == id) {
            known = true;
            break;
        }
    }
    if (!known) {
        if (s_state.hidden_count == STATUS_MAX_NOTIFS) {
            /* Preserve the newest local dismiss when active cards exceed our
             * bounded hidden-ID capacity. */
            memmove(&s_state.hidden_ids[0], &s_state.hidden_ids[1],
                    sizeof(s_state.hidden_ids[0]) * (STATUS_MAX_NOTIFS - 1));
            s_state.hidden_count--;
        }
        s_state.hidden_ids[s_state.hidden_count++] = id;
    }
    s_dirty |= STATE_DIRTY_NOTIF;
    state_unlock();
}

void state_apply_close(const cJSON *obj)
{
    const cJSON *id_item = cJSON_GetObjectItemCaseSensitive(obj, "id");
    const cJSON *total_item = cJSON_GetObjectItemCaseSensitive(obj, "total");
    if (!cJSON_IsNumber(id_item)) {
        return;
    }
    const int legacy_id = id_item->valueint;
    const int legacy_total = cJSON_IsNumber(total_item) ? total_item->valueint : -1;

    state_lock();
    int id = legacy_id;
    int total = legacy_total;
    if (s_state.grouped_enabled) {
        int session = 0;
        if (!int_field(obj, "session", 1, INT32_MAX, &session) ||
            session != s_state.grouped_session ||
            !int_field(obj, "id", 1, INT32_MAX, &id) ||
            (total_item != NULL && !int_field(obj, "total", 0, INT32_MAX, &total))) {
            state_unlock();
            return;
        }
    }
    for (int i = 0; i < s_state.hidden_count; i++) {
        if (s_state.hidden_ids[i] == id) {
            memmove(&s_state.hidden_ids[i], &s_state.hidden_ids[i + 1],
                    sizeof(s_state.hidden_ids[0]) * (s_state.hidden_count - i - 1));
            s_state.hidden_count--;
            break;
        }
    }
    bool found = false;
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == id) {
            remove_notif_locked(i);
            found = true;
            break;
        }
    }
    if (s_state.grouped_enabled && s_state.presentation.id == id) {
        s_state.presentation.active = false;
        s_state.presentation.persistent = false;
        s_state.presentation.deadline_us = esp_timer_get_time();
    }
    if (total >= s_state.notif_count) {
        s_state.notif_overflow = total - s_state.notif_count;
    } else if (!found && s_state.notif_overflow > 0) {
        /* Legacy close of a card that was already dropped. */
        s_state.notif_overflow--;
    }
    s_dirty |= STATE_DIRTY_NOTIF;
    state_unlock();
}

void state_apply_sync(const cJSON *obj)
{
    /* Legacy hosts omit this field and keep their configured bar layout. */
    state_apply_dashboard(cJSON_GetObjectItemCaseSensitive(obj, "dashboard"));
    const cJSON *bar = cJSON_GetObjectItemCaseSensitive(obj, "bar");
    state_apply_bar(bar);

    const cJSON *media = cJSON_GetObjectItemCaseSensitive(obj, "media");
    state_apply_media(cJSON_IsObject(media) ? media : NULL);

    const cJSON *notifs = cJSON_GetObjectItemCaseSensitive(obj, "notifs");
    state_lock();
    s_state.grouped_enabled = false;
    s_state.history_enabled = false;
    s_history_expired_count = 0;
    s_state.grouped_session = 0;
    s_state.actions_enabled = false;
    clear_action_runtime_locked(false);
    clear_presentation_locked();
    s_state.cache_limit = STATUS_LEGACY_NOTIFS;
    s_state.notif_count = 0;
    state_unlock();
    if (cJSON_IsArray(notifs)) {
        const cJSON *item = NULL;
        cJSON_ArrayForEach(item, notifs) {
            apply_notify(item, false);
        }
    }

    const cJSON *overflow = cJSON_GetObjectItemCaseSensitive(obj, "notifs_overflow");
    state_lock();
    s_state.notif_overflow = cJSON_IsNumber(overflow) ? overflow->valueint : 0;
    /* Only a complete notification snapshot can prove a hidden ID is gone.
     * A capped snapshot may omit a still-active, locally dismissed card. */
    if (cJSON_IsArray(notifs) && cJSON_IsNumber(overflow)
            && overflow->valueint == 0 && cJSON_GetArraySize(notifs) <= s_state.cache_limit) {
        int retained = 0;
        for (int h = 0; h < s_state.hidden_count; h++) {
            for (int i = 0; i < s_state.notif_count; i++) {
                if (s_state.hidden_ids[h] == s_state.notifs[i].id) {
                    s_state.hidden_ids[retained++] = s_state.hidden_ids[h];
                    break;
                }
            }
        }
        s_state.hidden_count = retained;
    }
    s_state.got_sync = true;
    s_dirty |= STATE_DIRTY_BAR | STATE_DIRTY_NOTIF;
    state_unlock();
}

static bool int_field(const cJSON *obj, const char *name, int min, int max, int *out)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, name);
    if (!cJSON_IsNumber(item) || item->valuedouble != (double)item->valueint
            || item->valueint < min || item->valueint > max) {
        return false;
    }
    *out = item->valueint;
    return true;
}

static status_notif_t *find_notif_locked(int id)
{
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == id) return &s_state.notifs[i];
    }
    return NULL;
}

static bool action_feedback_card_locked(int id, int open_revision)
{
    const status_notif_t *notif = find_notif_locked(id);
    return notif != NULL && notif->open_revision == open_revision &&
           cached_visible_locked(id);
}

static void action_terminal_locked(int id, int open_revision,
                                   status_action_feedback_t feedback,
                                   int64_t now_us)
{
    s_state.action_cooldown_until_us = now_us + 500000;
    if (feedback != STATUS_ACTION_FEEDBACK_SENT) {
        s_state.action_blocked = true;
        s_state.action_blocked_id = id;
        s_state.action_blocked_open_rev = open_revision;
    }
    if (action_feedback_card_locked(id, open_revision)) {
        s_state.action_feedback = feedback;
        s_state.action_feedback_id = id;
        s_state.action_feedback_open_rev = open_revision;
        s_state.action_feedback_until_us = now_us + 2000000;
    } else {
        s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
        s_state.action_feedback_id = 0;
        s_state.action_feedback_open_rev = 0;
        s_state.action_feedback_until_us = 0;
    }
    s_dirty |= STATE_DIRTY_NOTIF;
}

bool state_action_open_enabled(const status_state_t *state,
                               const status_notif_t *notif, int64_t now_us)
{
    return state != NULL && notif != NULL && state->actions_enabled &&
           state->grouped_enabled && state->grouped_session > 0 &&
           notif->valid && notif->id > 0 && notif->open_ready &&
           notif->open_revision > 0 && !state->action_pending &&
           (!state->history_enabled ||
            (notif->history_revision > 0 && notif->history_deadline_us > now_us)) &&
           !state->action_request_exhausted &&
           now_us >= state->action_cooldown_until_us &&
           !(state->action_blocked && state->action_blocked_id == notif->id &&
             state->action_blocked_open_rev == notif->open_revision);
}

bool state_action_begin(uint32_t boot_id, int id, int open_revision,
                        int64_t now_us, int *session, int *request)
{
    bool accepted = false;
    if (boot_id == 0 || id < 1 || id > INT32_MAX ||
        open_revision < 1 || open_revision > INT32_MAX ||
        session == NULL || request == NULL) {
        return false;
    }
    state_lock();
    status_state_t *st = &s_state;
    status_notif_t *notif = find_notif_locked(id);
    if (notif != NULL && cached_visible_locked(id) &&
        state_action_open_enabled(st, notif, now_us)) {
        if (st->action_next_request >= INT32_MAX) {
            st->action_request_exhausted = true;
        } else if (notif->open_revision == open_revision) {
            st->action_next_request++;
            st->action_pending = true;
            st->action_pending_session = st->grouped_session;
            st->action_pending_boot_id = boot_id;
            st->action_pending_id = id;
            st->action_pending_open_rev = open_revision;
            st->action_pending_request = st->action_next_request;
            st->action_pending_deadline_us = now_us + 3000000;
            st->action_feedback = STATUS_ACTION_FEEDBACK_NONE;
            st->action_feedback_until_us = 0;
            *session = st->action_pending_session;
            *request = st->action_pending_request;
            s_dirty |= STATE_DIRTY_NOTIF;
            accepted = true;
        }
    }
    state_unlock();
    return accepted;
}

bool state_apply_card_action(const cJSON *obj)
{
    int session = 0, id = 0;
    status_notif_t parsed = {0};
    if (!cJSON_IsObject(obj) ||
        !int_field(obj, "session", 1, INT32_MAX, &session) ||
        !int_field(obj, "id", 1, INT32_MAX, &id) ||
        !parse_action_open(obj, &parsed)) {
        return false;
    }

    state_lock();
    status_notif_t *notif = find_notif_locked(id);
    if (!s_state.grouped_enabled || !s_state.actions_enabled ||
        session != s_state.grouped_session || notif == NULL ||
        parsed.open_revision < notif->open_revision ||
        (parsed.open_revision == notif->open_revision &&
         parsed.open_ready != notif->open_ready)) {
        state_unlock();
        return false;
    }
    if (parsed.open_revision != notif->open_revision ||
        parsed.open_ready != notif->open_ready) {
        notif->open_revision = parsed.open_revision;
        notif->open_ready = parsed.open_ready;
        if (s_state.action_blocked && s_state.action_blocked_id == id &&
            parsed.open_revision > s_state.action_blocked_open_rev) {
            s_state.action_blocked = false;
        }
        s_dirty |= STATE_DIRTY_NOTIF;
    }
    state_unlock();
    return true;
}

bool state_apply_action_result(const cJSON *obj, uint32_t boot_id)
{
    int session = 0, id = 0, open_revision = 0, request = 0;
    uint32_t result_boot = 0;
    const cJSON *boot = cJSON_GetObjectItemCaseSensitive(obj, "boot_id");
    const cJSON *status = cJSON_GetObjectItemCaseSensitive(obj, "status");
    if (!cJSON_IsObject(obj) ||
        !int_field(obj, "session", 1, INT32_MAX, &session) ||
        !cJSON_IsNumber(boot) || !isfinite(boot->valuedouble) ||
        boot->valuedouble < 1 ||
        boot->valuedouble > UINT32_MAX ||
        trunc(boot->valuedouble) != boot->valuedouble ||
        !int_field(obj, "id", 1, INT32_MAX, &id) ||
        !int_field(obj, "open_rev", 1, INT32_MAX, &open_revision) ||
        !int_field(obj, "request", 1, INT32_MAX, &request) ||
        !cJSON_IsString(status) ||
        (strcmp(status->valuestring, "dispatched") != 0 &&
         strcmp(status->valuestring, "unavailable") != 0 &&
         strcmp(status->valuestring, "stale") != 0 &&
         strcmp(status->valuestring, "failed") != 0 &&
         strcmp(status->valuestring, "unknown") != 0)) {
        return false;
    }
    result_boot = (uint32_t)boot->valuedouble;

    state_lock();
    status_state_t *st = &s_state;
    const bool matches = boot_id != 0 && result_boot == boot_id &&
        st->actions_enabled && st->grouped_enabled && st->action_pending &&
        session == st->grouped_session && session == st->action_pending_session &&
        result_boot == st->action_pending_boot_id &&
        id == st->action_pending_id && open_revision == st->action_pending_open_rev &&
        request == st->action_pending_request;
    if (!matches) {
        state_unlock();
        return false;
    }

    status_action_feedback_t feedback = STATUS_ACTION_FEEDBACK_TRY_AGAIN;
    if (strcmp(status->valuestring, "dispatched") == 0) {
        feedback = STATUS_ACTION_FEEDBACK_SENT;
    } else if (strcmp(status->valuestring, "unavailable") == 0) {
        feedback = STATUS_ACTION_FEEDBACK_UNAVAILABLE;
    } else if (strcmp(status->valuestring, "unknown") == 0) {
        feedback = STATUS_ACTION_FEEDBACK_NO_CONFIRMATION;
    }
    st->action_pending = false;
    st->action_pending_deadline_us = 0;
    action_terminal_locked(id, open_revision, feedback, esp_timer_get_time());
    state_unlock();
    return true;
}

void state_action_tick(int64_t now_us)
{
    state_lock();
    if (s_state.action_pending && now_us >= s_state.action_pending_deadline_us) {
        const int id = s_state.action_pending_id;
        const int open_revision = s_state.action_pending_open_rev;
        s_state.action_pending = false;
        s_state.action_pending_deadline_us = 0;
        action_terminal_locked(id, open_revision,
                               STATUS_ACTION_FEEDBACK_NO_CONFIRMATION, now_us);
    }
    if (s_state.action_feedback != STATUS_ACTION_FEEDBACK_NONE &&
        now_us >= s_state.action_feedback_until_us) {
        s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
        s_state.action_feedback_id = 0;
        s_state.action_feedback_open_rev = 0;
        s_state.action_feedback_until_us = 0;
        s_dirty |= STATE_DIRTY_NOTIF;
    }
    state_unlock();
}

void state_action_disconnect(void)
{
    state_lock();
    if (s_state.action_pending) {
        s_state.action_blocked = true;
        s_state.action_blocked_id = s_state.action_pending_id;
        s_state.action_blocked_open_rev = s_state.action_pending_open_rev;
        s_state.action_pending = false;
        s_state.action_pending_session = 0;
        s_state.action_pending_boot_id = 0;
        s_state.action_pending_id = 0;
        s_state.action_pending_open_rev = 0;
        s_state.action_pending_request = 0;
        s_state.action_pending_deadline_us = 0;
        s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
        s_state.action_feedback_id = 0;
        s_state.action_feedback_open_rev = 0;
        s_state.action_feedback_until_us = 0;
        s_dirty |= STATE_DIRTY_NOTIF;
    }
    state_unlock();
}

int state_card_sync_capacity(void)
{
    return s_stage.cards != NULL ? STATUS_MAX_NOTIFS : 0;
}

void state_sync_abort(void)
{
    s_stage.active = false;
}

bool state_sync_pending(void)
{
    return s_stage.active;
}

bool state_sync_timeout(void)
{
    if (s_stage.active && esp_timer_get_time() - s_stage.started_us > SYNC_TIMEOUT_US) {
        state_sync_abort();
        return true;
    }
    return false;
}

bool state_sync_begin(const cJSON *obj)
{
    int tx, count, limit, overflow;
    const cJSON *bar = cJSON_GetObjectItemCaseSensitive(obj, "bar");
    const cJSON *grouped = cJSON_GetObjectItemCaseSensitive(obj, "grouped");
    const cJSON *dashboard = cJSON_GetObjectItemCaseSensitive(obj, "dashboard");
    const cJSON *actions = cJSON_GetObjectItemCaseSensitive(obj, "actions");
    bool grouped_enabled = false;
    bool history_enabled = false;
    bool actions_enabled = false;
    int grouped_session = 0;
    if (grouped != NULL) {
        const cJSON *history = cJSON_GetObjectItemCaseSensitive(grouped, "history");
        if (!cJSON_IsObject(grouped) ||
            cJSON_GetArraySize(grouped) != (history ? 2 : 1) ||
            (history && !cJSON_IsTrue(history)) ||
            !int_field(grouped, "session", 1, INT32_MAX, &grouped_session) ||
            !cJSON_IsObject(dashboard)) {
            return false;
        }
        grouped_enabled = true;
        history_enabled = history != NULL;
    }
    if (actions != NULL) {
        if (!grouped_enabled || !cJSON_IsObject(actions) ||
            cJSON_GetArraySize(actions) != 1) {
            return false;
        }
        const cJSON *enabled = cJSON_GetObjectItemCaseSensitive(actions, "enabled");
        if (!cJSON_IsTrue(enabled)) return false;
        actions_enabled = true;
    }
    if (s_stage.cards == NULL || s_stage.active
            || !int_field(obj, "tx", 0, INT32_MAX, &tx)
            || !int_field(obj, "count", 0, STATUS_MAX_NOTIFS, &count)
            || !int_field(obj, "limit", 0, STATUS_MAX_NOTIFS, &limit)
            || !int_field(obj, "overflow", 0, INT32_MAX, &overflow)
            || count > limit || limit > s_state.notif_capacity || !cJSON_IsObject(bar)) {
        return false;
    }
    const int zone_count = parse_zones(cJSON_GetObjectItemCaseSensitive(bar, "zones"), s_stage.zones, true);
    if (zone_count < 0) {
        return false;
    }
    s_stage.zone_count = zone_count;
    s_stage.clock = parse_clock(cJSON_GetObjectItemCaseSensitive(obj, "clock"));
    s_stage.media = parse_media(cJSON_GetObjectItemCaseSensitive(obj, "media"));
    if (!dashboard_parse(dashboard, &s_stage.dashboard)) {
        return false;
    }
    if (grouped_enabled && !s_stage.dashboard.valid) {
        return false;
    }
    s_stage.tx = tx;
    s_stage.count = count;
    s_stage.limit = limit;
    s_stage.next_index = 0;
    s_stage.overflow = overflow;
    s_stage.grouped = grouped_enabled;
    s_stage.history = history_enabled;
    s_stage.grouped_session = grouped_session;
    s_stage.actions_enabled = actions_enabled;
    s_stage.started_us = esp_timer_get_time();
    s_stage.active = true;
    return true;
}

bool state_sync_cards(const cJSON *obj)
{
    int tx, start;
    const cJSON *notifs = cJSON_GetObjectItemCaseSensitive(obj, "notifs");
    if (!s_stage.active || !int_field(obj, "tx", 0, INT32_MAX, &tx)
            || !int_field(obj, "start", 0, STATUS_MAX_NOTIFS, &start)
            || tx != s_stage.tx || start != s_stage.next_index || !cJSON_IsArray(notifs)) {
        return false;
    }
    const int size = cJSON_GetArraySize(notifs);
    if (size <= 0 || size > s_stage.count - s_stage.next_index) {
        return false;
    }
    for (int index = 0; index < size; index++) {
        const cJSON *item = cJSON_GetArrayItem(notifs, index);
        status_notif_t parsed;
        if (!parse_notif(item, &parsed)) {
            return false;
        }
        if (s_stage.history && parsed.history_revision == 0) return false;
        if (s_stage.actions_enabled && !parse_action_open(item, &parsed)) {
            return false;
        }
        if (s_stage.grouped &&
            (parsed.id < 1 || parsed.id > INT32_MAX || parsed.urgency < 0 ||
             parsed.urgency > 2)) {
            return false;
        }
        for (int previous = 0; previous < s_stage.next_index; previous++) {
            if (s_stage.cards[previous].id == parsed.id) {
                return false;
            }
        }
        s_stage.cards[s_stage.next_index++] = parsed;
    }
    s_stage.started_us = esp_timer_get_time();
    return true;
}

bool state_sync_commit(const cJSON *obj, int64_t *epoch, int *offset, bool *has_clock)
{
    int tx;
    if (!s_stage.active || !int_field(obj, "tx", 0, INT32_MAX, &tx)
            || tx != s_stage.tx || s_stage.next_index != s_stage.count
            || state_sync_timeout()) {
        return false;
    }

    state_lock();
    const bool new_group_session = s_stage.grouped &&
        (!s_state.grouped_enabled || s_state.grouped_session != s_stage.grouped_session);
    const bool leaving_grouped = !s_stage.grouped && s_state.grouped_enabled;
    const bool same_history_session = !new_group_session && s_stage.history &&
                                      s_state.history_enabled;
    if (same_history_session) {
        for (int i = 0; i < s_stage.count; i++) {
            const status_notif_t *current = find_notif_locked(s_stage.cards[i].id);
            if (!history_reconcile(&s_stage.cards[i], current)) {
                state_unlock();
                return false;
            }
        }
    }
    const bool same_action_session = !new_group_session && s_state.actions_enabled &&
        s_stage.actions_enabled && s_state.grouped_enabled && s_stage.grouped &&
        s_state.grouped_session == s_stage.grouped_session;
    if (same_action_session) {
        for (int i = 0; i < s_stage.count; i++) {
            const status_notif_t *current = find_notif_locked(s_stage.cards[i].id);
            if (current != NULL &&
                (s_stage.cards[i].open_revision < current->open_revision ||
                 (s_stage.cards[i].open_revision == current->open_revision &&
                  s_stage.cards[i].open_ready != current->open_ready))) {
                state_unlock();
                return false;
            }
        }
    }
    if (new_group_session) {
        s_state.hidden_count = 0;
        s_history_expired_count = 0;
        clear_presentation_locked();
        s_state.notif_focus_id = 0;
        s_state.notif_focus_urgency = 1;
        s_state.notif_critical_id = 0;
    } else if (leaving_grouped) {
        clear_presentation_locked();
    }
    if (new_group_session || !s_stage.actions_enabled) {
        clear_action_runtime_locked(false);
    }
    s_state.grouped_enabled = s_stage.grouped;
    s_state.history_enabled = s_stage.history;
    s_state.grouped_session = s_stage.grouped ? s_stage.grouped_session : 0;
    s_state.actions_enabled = s_stage.actions_enabled;

    if (!s_stage.grouped) {
        /* Legacy reconnects may introduce arrivals missed while the link was
         * down. Refill of older overflow cards must not steal normal focus. */
        int newest_known = -1;
        for (int i = 0; i < s_stage.count; i++) {
            for (int old = 0; old < s_state.notif_count; old++) {
                if (s_stage.cards[i].id == s_state.notifs[old].id) {
                    newest_known = i;
                    break;
                }
            }
        }
        for (int i = 0; i < s_stage.count; i++) {
            const status_notif_t *card = &s_stage.cards[i];
            bool hidden = false;
            for (int h = 0; h < s_state.hidden_count; h++) {
                hidden |= s_state.hidden_ids[h] == card->id;
            }
            int old_urgency = -1;
            for (int old = 0; old < s_state.notif_count; old++) {
                if (card->id == s_state.notifs[old].id) {
                    old_urgency = s_state.notifs[old].urgency;
                    break;
                }
            }
            if (!hidden && ((old_urgency < 0 && i > newest_known)
                    || (card->urgency >= 2 && old_urgency < 2))) {
                request_focus(card);
            }
        }
    }
    int kept = 0;
    for (int i = 0; i < s_stage.count; i++) {
        status_notif_t *card = &s_stage.cards[i];
        if (s_stage.history && (history_suppressed(card) ||
            card->history_deadline_us <= esp_timer_get_time())) {
            history_remember_expired(card);
            continue;
        }
        s_stage.cards[kept++] = *card;
    }
    s_stage.count = kept;
    status_notif_t *old_cards = s_state.notifs;
    s_state.notifs = s_stage.cards;
    s_stage.cards = old_cards;
    memcpy(s_state.zones, s_stage.zones, sizeof(s_state.zones[0]) * (size_t)s_stage.zone_count);
    s_state.zone_count = s_stage.zone_count;
    s_state.clock = s_stage.clock;
    s_state.media = s_stage.media;
    s_state.dashboard = s_stage.dashboard;
    s_state.notif_count = s_stage.count;
    s_state.cache_limit = s_stage.limit;
    s_state.notif_overflow = s_stage.overflow;
    if (s_stage.overflow == 0) {
        int retained = 0;
        for (int h = 0; h < s_state.hidden_count; h++) {
            for (int i = 0; i < s_state.notif_count; i++) {
                if (s_state.hidden_ids[h] == s_state.notifs[i].id) {
                    s_state.hidden_ids[retained++] = s_state.hidden_ids[h];
                    break;
                }
            }
        }
        s_state.hidden_count = retained;
    }
    if (s_state.action_blocked) {
        const status_notif_t *current = find_notif_locked(s_state.action_blocked_id);
        if (current == NULL || current->open_revision > s_state.action_blocked_open_rev) {
            s_state.action_blocked = false;
        }
    }
    if (s_state.action_feedback != STATUS_ACTION_FEEDBACK_NONE &&
        !action_feedback_card_locked(s_state.action_feedback_id,
                                     s_state.action_feedback_open_rev)) {
        s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
        s_state.action_feedback_id = 0;
        s_state.action_feedback_open_rev = 0;
        s_state.action_feedback_until_us = 0;
    }
    if (s_state.grouped_enabled && s_state.presentation.active &&
        !cached_visible_locked(s_state.presentation.id)) {
        s_state.presentation.active = false;
        s_state.presentation.persistent = false;
        s_state.presentation.deadline_us = esp_timer_get_time();
    }
    s_state.got_sync = true;
    s_dirty |= STATE_DIRTY_BAR | STATE_DIRTY_NOTIF | STATE_DIRTY_DASHBOARD;
    state_unlock();

    if (epoch != NULL) {
        *epoch = s_stage.clock.epoch;
    }
    if (offset != NULL) {
        *offset = s_stage.clock.offset;
    }
    if (has_clock != NULL) {
        *has_clock = s_stage.clock.valid;
    }
    s_stage.active = false;
    ESP_LOGI(TAG, "card sync committed: tx=%d cached=%d overflow=%d",
             tx, s_stage.count, s_stage.overflow);
    return true;
}

void state_cards_status(int *ids, int *count, int *overflow, int *capacity)
{
    state_lock();
    *count = s_state.notif_count;
    *overflow = s_state.notif_overflow;
    *capacity = s_state.notif_capacity;
    for (int i = 0; i < s_state.notif_count; i++) {
        ids[i] = s_state.notifs[i].id;
    }
    state_unlock();
}
