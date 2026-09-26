#define NATIVE_PROTOCOL 1
#define main native_ui_embedded_main
#include "test_ui.c"
#undef main

#include "link.h"
#include "proto.h"

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
    cJSON_AddStringToObject(out, "input_state", enabled
        ? input_state_name_for_grouped() : input_state_name());
    cJSON_AddNumberToObject(out, "frame_width", DISPLAY_WIDTH);
    cJSON_AddNumberToObject(out, "frame_height", DISPLAY_HEIGHT);
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
