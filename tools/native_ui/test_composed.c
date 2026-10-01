#define NATIVE_PROTOCOL 1
#define main native_ui_embedded_main
#include "test_ui.c"
#undef main

#include "link.h"
#include "proto.h"

#include <string.h>

#define COMPOSED_OUTBOUND_CAPACITY 32
#define COMPOSED_OUTBOUND_LENGTH 8192

static char s_composed_outbound[COMPOSED_OUTBOUND_CAPACITY][COMPOSED_OUTBOUND_LENGTH];
static int s_composed_outbound_count;

esp_err_t link_send_json(const char *json)
{
    if (s_composed_outbound_count < COMPOSED_OUTBOUND_CAPACITY) {
        snprintf(s_composed_outbound[s_composed_outbound_count],
                 COMPOSED_OUTBOUND_LENGTH, "%s", json);
        s_composed_outbound_count++;
    }
    return ESP_OK;
}

static bool composed_integer(const cJSON *object, const char *name, int *out)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    if (!cJSON_IsNumber(value) || value->valuedouble != (double)value->valueint) {
        return false;
    }
    *out = value->valueint;
    return true;
}

static const char *composed_string(const cJSON *object, const char *name)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    return cJSON_IsString(value) ? value->valuestring : NULL;
}

static const lv_font_t *composed_span_font(lv_span_t *span)
{
    lv_style_value_t value = {0};
    if (lv_style_get_prop(lv_span_get_style(span), LV_STYLE_TEXT_FONT, &value) !=
        LV_STYLE_RES_FOUND) return &status_text_16;
    return value.ptr;
}

static int composed_span_style(lv_span_t *span)
{
    const lv_font_t *font = composed_span_font(span);
    if (font == &status_text_16_bold) return 1;
    if (font == &status_text_16_italic) return 2;
    if (font == &status_text_16_bold_italic) return 3;
    return 0;
}

static const char *composed_cjk_resolved_font(const lv_font_t *font, const char *text)
{
    bool saw_cjk = false;
    const unsigned char *cursor = (const unsigned char *)text;
    while (*cursor) {
        uint32_t codepoint;
        size_t step;
        const size_t remaining = strlen((const char *)cursor);
        if (*cursor < 0x80) {
            codepoint = *cursor; step = 1;
        } else if ((*cursor & 0xE0) == 0xC0) {
            if (remaining < 2 || (cursor[1] & 0xC0) != 0x80) return "invalid";
            codepoint = ((uint32_t)(cursor[0] & 0x1F) << 6) | (cursor[1] & 0x3F); step = 2;
        } else if ((*cursor & 0xF0) == 0xE0) {
            if (remaining < 3 || (cursor[1] & 0xC0) != 0x80 || (cursor[2] & 0xC0) != 0x80) return "invalid";
            codepoint = ((uint32_t)(cursor[0] & 0x0F) << 12) |
                ((uint32_t)(cursor[1] & 0x3F) << 6) | (cursor[2] & 0x3F); step = 3;
        } else if ((*cursor & 0xF8) == 0xF0) {
            if (remaining < 4 || (cursor[1] & 0xC0) != 0x80 ||
                (cursor[2] & 0xC0) != 0x80 || (cursor[3] & 0xC0) != 0x80) return "invalid";
            codepoint = ((uint32_t)(cursor[0] & 0x07) << 18) |
                ((uint32_t)(cursor[1] & 0x3F) << 12) |
                ((uint32_t)(cursor[2] & 0x3F) << 6) | (cursor[3] & 0x3F); step = 4;
        } else {
            cursor++;
            continue;
        }
        if (codepoint == 0x6771 || codepoint == 0x4EAC) {
            lv_font_glyph_dsc_t glyph = {0};
            if (!lv_font_get_glyph_dsc(font, &glyph, codepoint, 0)) return "missing";
            if (glyph.resolved_font != &status_text_16) return "other";
            saw_cjk = true;
        }
        cursor += step;
    }
    return saw_cjk ? "status_text_16" : "none";
}

static void composed_add_rect(cJSON *parent, const char *key, const lv_obj_t *obj)
{
    cJSON *rect = cJSON_AddObjectToObject(parent, key);
    cJSON_AddNumberToObject(rect, "x", lv_obj_get_x(obj));
    cJSON_AddNumberToObject(rect, "y", lv_obj_get_y(obj));
    cJSON_AddNumberToObject(rect, "width", lv_obj_get_width(obj));
    cJSON_AddNumberToObject(rect, "height", lv_obj_get_height(obj));
}

static void composed_add_body_readback(cJSON *out)
{
    cJSON *cards = cJSON_AddArrayToObject(out, "rendered_cards");
    for (int i = 0; i < 3; i++) {
        const card_view_t *slot = &s_cards[i];
        const bool span_visible = !lv_obj_has_flag(slot->body_span, LV_OBJ_FLAG_HIDDEN);
        cJSON *card = cJSON_CreateObject();
        cJSON_AddNumberToObject(card, "slot", i);
        cJSON_AddBoolToObject(card, "valid", slot->valid);
        cJSON_AddNumberToObject(card, "id", slot->valid ? slot->id : 0);
        cJSON_AddBoolToObject(card, "span_visible", span_visible);
        char object_id[32];
        snprintf(object_id, sizeof(object_id), "%p", (const void *)slot->body_span);
        cJSON_AddStringToObject(card, "span_object_id", object_id);
        const char *plain_text = lv_obj_get_user_data(slot->body);
        cJSON_AddStringToObject(card, "body_text",
            span_visible ? slot->styled_body : (plain_text == NULL ? "" : plain_text));
        cJSON_AddNumberToObject(card, "cached_run_count", slot->styled_run_count);
        cJSON_AddNumberToObject(card, "span_overflow",
            lv_spangroup_get_overflow(slot->body_span));
        composed_add_rect(card, "body_rect", slot->body);
        composed_add_rect(card, "span_rect", slot->body_span);
        composed_add_rect(card, "age_bar_rect", slot->age_bar);
        cJSON_AddBoolToObject(card, "age_bar_visible",
            !lv_obj_has_flag(slot->age_bar, LV_OBJ_FLAG_HIDDEN));
        cJSON_AddNumberToObject(card, "age_bar_remaining",
            lv_bar_get_value(slot->age_bar) - lv_bar_get_start_value(slot->age_bar));

        cJSON *runs = cJSON_AddArrayToObject(card, "body_runs");
        cJSON *cached_runs = cJSON_AddArrayToObject(card, "cached_body_runs");
        for (int run_index = 0; run_index < slot->styled_run_count; run_index++) {
            const status_body_run_t *run = &slot->styled_runs[run_index];
            cJSON *item = cJSON_CreateObject();
            cJSON_AddNumberToObject(item, "start", run->start);
            cJSON_AddNumberToObject(item, "end", run->end);
            cJSON_AddNumberToObject(item, "style", run->style);
            cJSON_AddItemToArray(cached_runs, item);
            if (span_visible) {
                cJSON *visible_run = cJSON_CreateObject();
                cJSON_AddNumberToObject(visible_run, "start", run->start);
                cJSON_AddNumberToObject(visible_run, "end", run->end);
                cJSON_AddNumberToObject(visible_run, "style", run->style);
                cJSON_AddItemToArray(runs, visible_run);
            }
        }

        cJSON *segments = cJSON_AddArrayToObject(card, "span_segments");
        const uint32_t span_count = lv_spangroup_get_span_count(slot->body_span);
        for (uint32_t segment_index = 0; segment_index < span_count; segment_index++) {
            lv_span_t *span = lv_spangroup_get_child(slot->body_span, (int32_t)segment_index);
            if (span == NULL) continue;
            cJSON *segment = cJSON_CreateObject();
            const char *segment_text = lv_span_get_text(span);
            snprintf(object_id, sizeof(object_id), "%p", (const void *)span);
            cJSON_AddStringToObject(segment, "id", object_id);
            cJSON_AddStringToObject(segment, "text", segment_text);
            cJSON_AddNumberToObject(segment, "style", composed_span_style(span));
            const char *resolved_font = composed_cjk_resolved_font(
                composed_span_font(span), segment_text);
            cJSON_AddStringToObject(segment, "cjk_resolved_font", resolved_font);
            cJSON_AddBoolToObject(segment, "cjk_fallback_regular",
                strcmp(resolved_font, "status_text_16") == 0);
            cJSON_AddItemToArray(segments, segment);
        }
        cJSON_AddItemToArray(cards, card);
    }
}

static const char *input_state_name_for_grouped(void);

static cJSON *composed_readback(void)
{
    state_lock();
    const status_state_t *st = state_get();
    const bool enabled = st->grouped_enabled;
    const bool home = !enabled || st->grouped_home;
    const bool manual = enabled && !home && st->grouped_manual;
    const bool presenting = enabled && !manual && st->grouped_presenting;
    const int session = enabled ? st->grouped_session : 0;
    const int generation = enabled ? st->grouped_generation : 0;
    const int present_id = st->grouped_present_id;
    const int64_t deadline = st->grouped_deadline_us;
    const bool persistent = st->grouped_persistent;
    const int count = st->notif_count;
    const int overflow = st->notif_overflow;
    const int focus_id = st->deck_focus_id;
    const int position = st->deck_position;
    const int reachable_count = st->deck_reachable;
    const bool stale = st->deck_stale;
    const bool actions_enabled = st->actions_enabled;
    const bool action_pending = st->action_pending;
    const int pending_id = st->action_pending_id;
    const int pending_revision = st->action_pending_open_rev;
    const int pending_request = st->action_pending_request;
    struct { int id, revision; bool ready; } opens[STATUS_MAX_NOTIFS];
    int open_count = 0;
    if (actions_enabled) {
        for (int i = 0; i < count && i < STATUS_MAX_NOTIFS; i++) {
            opens[open_count].id = st->notifs[i].id;
            opens[open_count].revision = st->notifs[i].open_revision;
            opens[open_count].ready = st->notifs[i].open_ready;
            open_count++;
        }
    }
    state_unlock();

    cJSON *out = cJSON_CreateObject();
    cJSON_AddBoolToObject(out, "enabled", enabled);
    cJSON_AddNumberToObject(out, "session", session);
    cJSON_AddStringToObject(out, "group", home ? "home" : "notifications");
    cJSON_AddBoolToObject(out, "manual", manual);
    cJSON_AddNumberToObject(out, "generation", generation);
    if (presenting && present_id > 0) {
        cJSON_AddNumberToObject(out, "present_id", present_id);
        const int remaining = persistent ? -1 : deadline > s_now_us
            ? (int)((deadline - s_now_us + 999) / 1000) : 0;
        cJSON_AddNumberToObject(out, "remaining_ms", remaining);
    } else {
        cJSON_AddNullToObject(out, "present_id");
        cJSON_AddNumberToObject(out, "remaining_ms", 0);
    }
    cJSON_AddNumberToObject(out, "count", count);
    cJSON_AddNumberToObject(out, "overflow", overflow);
    cJSON_AddNumberToObject(out, "focus_id", focus_id);
    cJSON_AddNumberToObject(out, "position", position);
    cJSON_AddNumberToObject(out, "reachable", reachable_count);
    cJSON_AddBoolToObject(out, "stale", stale);
    cJSON *actions = cJSON_AddObjectToObject(out, "actions");
    cJSON_AddBoolToObject(actions, "enabled", actions_enabled);
    cJSON *open = cJSON_AddArrayToObject(actions, "open");
    for (int i = 0; i < open_count; i++) {
        cJSON *item = cJSON_CreateObject();
        cJSON_AddNumberToObject(item, "id", opens[i].id);
        cJSON_AddNumberToObject(item, "rev", opens[i].revision);
        cJSON_AddStringToObject(item, "state", opens[i].ready ? "ready" : "unavailable");
        cJSON_AddItemToArray(open, item);
    }
    if (action_pending) {
        cJSON *pending = cJSON_AddObjectToObject(actions, "pending");
        cJSON_AddNumberToObject(pending, "id", pending_id);
        cJSON_AddNumberToObject(pending, "open_rev", pending_revision);
        cJSON_AddNumberToObject(pending, "request", pending_request);
    } else {
        cJSON_AddNullToObject(actions, "pending");
    }
    cJSON_AddStringToObject(out, "input_state", enabled
        ? input_state_name_for_grouped() : input_state_name());
    cJSON_AddNumberToObject(out, "frame_width", DISPLAY_WIDTH);
    cJSON_AddNumberToObject(out, "frame_height", DISPLAY_HEIGHT);
    composed_add_body_readback(out);
    return out;
}

/* Keep readback's input state consistent with the mode selected by production UI. */
static const char *input_state_name_for_grouped(void)
{
    switch (deck_input_state(&s_group_input.motion)) {
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

static cJSON *parse_line(const char *line)
{
    return cJSON_Parse(line);
}

static bool run_command(const cJSON *command)
{
    if (!cJSON_IsObject(command)) {
        return false;
    }
    const char *type = composed_string(command, "type");
    if (type == NULL) {
        return false;
    }
    if (strcmp(type, "wire") == 0) {
        const cJSON *message = cJSON_GetObjectItemCaseSensitive(command, "message");
        if (!cJSON_IsObject(message)) {
            return false;
        }
        char *encoded = cJSON_PrintUnformatted(message);
        if (encoded == NULL) {
            return false;
        }
        proto_handle_line(encoded);
        cJSON_free(encoded);
        ui_deck_tick(state_take_dirty());
        repaint();
        return true;
    }
    if (strcmp(type, "advance") == 0) {
        int elapsed = 0;
        if (!composed_integer(command, "ms", &elapsed) || elapsed < 0 || elapsed > 600000) {
            return false;
        }
        step((uint32_t)elapsed);
        repaint();
        return true;
    }
    if (strcmp(type, "pointer") == 0) {
        const char *action = composed_string(command, "action");
        int x = 0, y = 0, elapsed = 0;
        if (action == NULL || !composed_integer(command, "x", &x) ||
            !composed_integer(command, "y", &y) ||
            !composed_integer(command, "ms", &elapsed) || elapsed < 0 || elapsed > 600000) {
            return false;
        }
        if (strcmp(action, "press") == 0) {
            if (s_pointer_state != LV_INDEV_STATE_RELEASED) return false;
            pointer_sample(x, y, true, (uint32_t)elapsed);
        } else if (strcmp(action, "move") == 0) {
            if (s_pointer_state != LV_INDEV_STATE_PRESSED) return false;
            pointer_sample(x, y, true, (uint32_t)elapsed);
        } else if (strcmp(action, "release") == 0) {
            if (s_pointer_state != LV_INDEV_STATE_PRESSED) return false;
            pointer_sample(x, y, false, (uint32_t)elapsed);
        } else {
            return false;
        }
        repaint();
        return true;
    }
    if (strcmp(type, "capture") == 0) {
        const char *name = composed_string(command, "name");
        return name != NULL && capture_frame(name);
    }
    if (strcmp(type, "readback") == 0) {
        ui_deck_tick(state_take_dirty());
        repaint();
        proto_handle_line("{\"t\":\"cards_query\"}");
        return true;
    }
    return false;
}

static void emit_command_result(bool ok)
{
    cJSON *result = cJSON_CreateObject();
    cJSON_AddBoolToObject(result, "ok", ok);
    cJSON *messages = cJSON_AddArrayToObject(result, "outbound");
    for (int i = 0; i < s_composed_outbound_count; i++) {
        cJSON *message = cJSON_Parse(s_composed_outbound[i]);
        if (message != NULL) {
            cJSON_AddItemToArray(messages, message);
        }
    }
    cJSON_AddItemToObject(result, "readback", composed_readback());
    char *encoded = cJSON_PrintUnformatted(result);
    if (encoded != NULL) {
        puts(encoded);
        cJSON_free(encoded);
    }
    cJSON_Delete(result);
}

static int run_jsonl(void)
{
    char line[16384];
    bool all_ok = true;
    while (fgets(line, sizeof(line), stdin) != NULL) {
        line[strcspn(line, "\r\n")] = '\0';
        s_composed_outbound_count = 0;
        cJSON *command = parse_line(line);
        const bool ok = command != NULL && run_command(command);
        all_ok = all_ok && ok;
        emit_command_result(ok);
        fflush(stdout);
        cJSON_Delete(command);
    }
    return ferror(stdin) || !all_ok ? 1 : 0;
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc != 3 || strcmp(argv[1], "--jsonl") != 0) {
        fprintf(stderr, "usage: %s --jsonl ARTIFACT_DIR\n", argv[0]);
        return 2;
    }
    if (!make_directory_tree(argv[2])) {
        fprintf(stderr, "Cannot create artifact directory: %s\n", argv[2]);
        return 2;
    }
    s_artifact_dir = argv[2];
    char trace_path[PATH_MAX];
    if (!artifact_path(trace_path, sizeof(trace_path), "native-ui-composed.trace") ||
        (s_trace_file = fopen(trace_path, "wb")) == NULL) {
        fprintf(stderr, "Cannot open trace under %s\n", argv[2]);
        return 2;
    }
    fixture_init(false);
    const int result = run_jsonl();
    fixture_shutdown();
    fclose(s_trace_file);
    s_trace_file = NULL;
    return result;
}
