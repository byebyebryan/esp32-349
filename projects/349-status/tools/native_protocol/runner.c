#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_timer.h"
#include "link.h"
#include "native_platform.h"
#include "proto.h"
#include "state.h"

#define OUTBOUND_CAPACITY 32
#define OUTBOUND_LENGTH 8192

static int64_t s_now_us = 1000000;
static bool s_connected = true;
static char s_outbound[OUTBOUND_CAPACITY][OUTBOUND_LENGTH];
static int s_outbound_count;
static int s_checks;

#define CHECK(condition) do { \
    s_checks++; \
    if (!(condition)) { \
        fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        exit(1); \
    } \
} while (0)

int64_t esp_timer_get_time(void)
{
    return s_now_us;
}

void native_protocol_set_time(int64_t now_us)
{
    s_now_us = now_us;
}

void native_protocol_advance_time(int64_t delta_us)
{
    s_now_us += delta_us;
}

void native_protocol_set_connected(bool connected)
{
    s_connected = connected;
}

esp_err_t link_send_json(const char *json)
{
    if (s_outbound_count < OUTBOUND_CAPACITY) {
        snprintf(s_outbound[s_outbound_count], OUTBOUND_LENGTH, "%s", json);
        s_outbound_count++;
    }
    return ESP_OK;
}

bool link_host_connected(void)
{
    return s_connected;
}

static void clear_outbound(void)
{
    s_outbound_count = 0;
}

static cJSON *parse_json(const char *json)
{
    cJSON *value = cJSON_Parse(json);
    if (value == NULL) {
        fprintf(stderr, "invalid test JSON: %s\n", json);
        exit(1);
    }
    return value;
}

static cJSON *send_wire(const char *json)
{
    clear_outbound();
    proto_handle_line(json);
    return s_outbound_count > 0 ? parse_json(s_outbound[0]) : NULL;
}

static const cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_GetObjectItemCaseSensitive(object, name);
}

static int number(const cJSON *object, const char *name)
{
    const cJSON *value = field(object, name);
    CHECK(cJSON_IsNumber(value));
    return value->valueint;
}

static const char *string(const cJSON *object, const char *name)
{
    const cJSON *value = field(object, name);
    CHECK(cJSON_IsString(value));
    return value->valuestring;
}

static void sync_grouped(int session, const char *records)
{
    cJSON *cards = parse_json(records);
    const int count = cJSON_GetArraySize(cards);
    char message[1024];
    snprintf(message, sizeof(message),
             "{\"t\":\"sync_begin\",\"tx\":17,\"count\":%d,\"limit\":%d,"
             "\"overflow\":0,\"bar\":{\"zones\":[]},\"dashboard\":{},"
             "\"grouped\":{\"session\":%d}}", count, count, session);
    CHECK(send_wire(message) == NULL);
    if (count > 0) {
        char chunk[4096];
        snprintf(chunk, sizeof(chunk), "{\"t\":\"sync_cards\",\"tx\":17,\"start\":0,\"notifs\":%s}", records);
        CHECK(send_wire(chunk) == NULL);
    }
    CHECK(send_wire("{\"t\":\"sync_commit\",\"tx\":17}") == NULL);
    cJSON_Delete(cards);
}

static void test_staged_validation_and_deltas(void)
{
    state_init();
    sync_grouped(77,
        "[{\"id\":10,\"app\":\"A\",\"summary\":\"older\",\"body\":\"a\",\"urgency\":1},"
        "{\"id\":11,\"app\":\"B\",\"summary\":\"newer\",\"body\":\"b\",\"urgency\":1}]");

    state_lock();
    status_state_t *st = state_get();
    CHECK(st->grouped_enabled && st->grouped_session == 77);
    CHECK(st->notif_count == 2 && st->notifs[0].id == 10 && st->notifs[1].id == 11);
    CHECK(st->notif_focus_seq == 0 && st->notif_critical_seq == 0);
    state_unlock();

    /* Staged metadata is rejected before it can replace the committed cache. */
    cJSON *response = send_wire(
        "{\"t\":\"sync_begin\",\"tx\":18,\"count\":0,\"limit\":0,\"overflow\":0,"
        "\"bar\":{\"zones\":[]},\"grouped\":{\"session\":78}}" );
    CHECK(response != NULL && strcmp(string(response, "t"), "resync") == 0);
    cJSON_Delete(response);
    state_lock();
    CHECK(state_get()->grouped_session == 77 && state_get()->notif_count == 2);
    state_unlock();

    response = send_wire(
        "{\"t\":\"sync_begin\",\"tx\":19,\"count\":0,\"limit\":0,\"overflow\":0,"
        "\"bar\":{\"zones\":[]},\"dashboard\":{\"battery\":{\"level\":2}},"
        "\"grouped\":{\"session\":78}}" );
    CHECK(response != NULL && strcmp(string(response, "t"), "resync") == 0);
    cJSON_Delete(response);

    CHECK(send_wire("{\"t\":\"notify\",\"id\":12,\"session\":76,\"summary\":\"stale\"}") == NULL);
    CHECK(send_wire("{\"t\":\"notify\",\"id\":12,\"summary\":\"missing session\"}") == NULL);
    state_lock();
    CHECK(state_get()->notif_count == 2);
    state_unlock();

    CHECK(send_wire(
        "{\"t\":\"notify\",\"session\":77,\"id\":10,\"app\":\"A\","
        "\"summary\":\"replacement\",\"body\":\"changed\",\"urgency\":2,"
        "\"cached\":true,\"total\":2}") == NULL);
    state_lock();
    st = state_get();
    CHECK(st->notif_count == 2 && st->notifs[0].id == 11 && st->notifs[1].id == 10);
    CHECK(strcmp(st->notifs[1].summary, "replacement") == 0);
    CHECK(st->notif_focus_seq == 0 && st->notif_critical_seq == 0);
    state_unlock();

    CHECK(send_wire("{\"t\":\"close\",\"id\":11,\"session\":76}") == NULL);
    CHECK(send_wire("{\"t\":\"close\",\"id\":11}") == NULL);
    state_lock();
    CHECK(state_get()->notif_count == 2);
    state_unlock();

    CHECK(send_wire("{\"t\":\"close\",\"id\":11,\"session\":77,\"total\":1}") == NULL);
    state_lock();
    CHECK(state_get()->notif_count == 1 && state_get()->notifs[0].id == 10);
    CHECK(state_get()->notif_overflow == 0);
    state_unlock();
}

static void test_presentation_and_session_reset(void)
{
    state_lock();
    status_state_t *st = state_get();
    CHECK(st->notif_count == 1 && st->notifs[0].id == 10);
    state_unlock();

    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":1,\"id\":10,"
                    "\"remaining_ms\":1000,\"urgency\":1}") == NULL);
    state_lock();
    const int64_t first_deadline = state_get()->presentation.deadline_us;
    CHECK(state_get()->presentation.active && first_deadline == s_now_us + 1000000);
    state_unlock();

    native_protocol_advance_time(500000);
    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":1,\"id\":10,"
                    "\"remaining_ms\":2000,\"urgency\":1}") == NULL);
    state_lock();
    CHECK(state_get()->presentation.deadline_us == first_deadline);
    state_unlock();

    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":1,\"id\":10,"
                    "\"remaining_ms\":400,\"urgency\":1}") == NULL);
    state_lock();
    CHECK(state_get()->presentation.deadline_us == s_now_us + 400000);
    state_unlock();

    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":1,\"id\":999,"
                    "\"remaining_ms\":200,\"urgency\":1}") == NULL);
    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":2,\"id\":10,"
                    "\"remaining_ms\":-1,\"urgency\":1}") == NULL);
    state_lock();
    CHECK(state_get()->presentation.generation == 2 && state_get()->presentation.persistent);
    state_unlock();

    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":2,\"id\":10,"
                    "\"remaining_ms\":300,\"urgency\":1}") == NULL);
    state_lock();
    CHECK(!state_get()->presentation.persistent && state_get()->presentation.active);
    const int64_t shortened_deadline = state_get()->presentation.deadline_us;
    state_unlock();

    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":3,\"id\":10,"
                    "\"remaining_ms\":0,\"urgency\":1}") == NULL);
    CHECK(send_wire("{\"t\":\"present\",\"session\":77,\"generation\":2,\"id\":10,"
                    "\"remaining_ms\":-1,\"urgency\":1}") == NULL);
    state_lock();
    CHECK(state_get()->presentation.generation == 3 && !state_get()->presentation.active);
    CHECK(shortened_deadline > 0);
    state_unlock();

    const uint32_t focus_seq = st->notif_focus_seq;
    state_hide_notif(10);
    sync_grouped(78,
        "[{\"id\":10,\"app\":\"A\",\"summary\":\"again\",\"body\":\"a\",\"urgency\":1}]");
    state_lock();
    st = state_get();
    CHECK(st->grouped_session == 78 && st->hidden_count == 0);
    CHECK(st->presentation.generation == 0 && !st->presentation.active);
    CHECK(st->notif_focus_seq == focus_seq && st->notif_critical_seq == 0);
    state_unlock();
}

static void test_status_and_outbound_actions(void)
{
    state_lock();
    status_state_t *st = state_get();
    st->grouped_home = false;
    st->grouped_manual = false;
    st->grouped_presenting = true;
    st->grouped_generation = 7;
    st->grouped_present_id = 10;
    st->grouped_deadline_us = s_now_us + 1500001;
    st->grouped_persistent = false;
    state_unlock();

    cJSON *response = send_wire("{\"t\":\"cards_query\"}");
    CHECK(response != NULL && strcmp(string(response, "t"), "cards_status") == 0);
    const cJSON *grouped = field(response, "grouped");
    CHECK(cJSON_IsObject(grouped));
    CHECK(cJSON_IsTrue(field(grouped, "enabled")));
    CHECK(number(grouped, "session") == 78);
    CHECK(strcmp(string(grouped, "group"), "notifications") == 0);
    CHECK(number(grouped, "generation") == 7 && number(grouped, "present_id") == 10);
    CHECK(number(grouped, "remaining_ms") == 1501);
    cJSON_Delete(response);

    clear_outbound();
    proto_send_input_dismiss(10);
    CHECK(s_outbound_count == 1);
    response = parse_json(s_outbound[0]);
    CHECK(strcmp(string(response, "action"), "dismiss") == 0);
    CHECK(number(response, "id") == 10 && number(response, "session") == 78);
    CHECK(number(response, "generation") == 7);
    cJSON_Delete(response);

    clear_outbound();
    proto_send_input_browse(true, 7);
    CHECK(s_outbound_count == 1);
    response = parse_json(s_outbound[0]);
    CHECK(strcmp(string(response, "action"), "browse") == 0);
    CHECK(strcmp(string(response, "group"), "home") == 0);
    CHECK(number(response, "session") == 78 && number(response, "generation") == 7);
    cJSON_Delete(response);

    clear_outbound();
    proto_send_input_browse(false, -1);
    CHECK(s_outbound_count == 0);

    clear_outbound();
    proto_handle_line("{\"t\":\"hello\"}");
    CHECK(s_outbound_count == 1);
    response = parse_json(s_outbound[0]);
    bool found_grouped = false;
    const cJSON *cap = field(response, "cap");
    const cJSON *item = NULL;
    cJSON_ArrayForEach(item, cap) {
        found_grouped |= cJSON_IsString(item) && strcmp(item->valuestring, "grouped-ui-v1") == 0;
    }
    CHECK(found_grouped);
    cJSON_Delete(response);
}

static void test_cards_status_pending_until_ui_publication(void)
{
    state_init();
    sync_grouped(79,
        "[{\"id\":10,\"app\":\"A\",\"summary\":\"older\",\"body\":\"a\",\"urgency\":1},"
        "{\"id\":11,\"app\":\"B\",\"summary\":\"newer\",\"body\":\"b\",\"urgency\":1}]");

    state_lock();
    status_state_t *st = state_get();
    st->deck_enabled = true;
    st->deck_reachable = 2;
    st->deck_position = 2;
    st->deck_focus_id = 10;
    st->deck_next_id = 11;
    st->grouped_home = false;
    st->grouped_manual = false;
    st->grouped_presenting = true;
    st->grouped_generation = 8;
    st->grouped_present_id = 11;
    st->grouped_deadline_us = s_now_us + 1000000;
    st->grouped_persistent = false;
    state_unlock();

    CHECK(send_wire("{\"t\":\"close\",\"id\":11,\"session\":79,\"total\":1}") == NULL);
    cJSON *response = send_wire("{\"t\":\"cards_query\"}");
    CHECK(response != NULL && number(response, "count") == 1);
    CHECK(cJSON_IsTrue(field(response, "view_pending")));
    CHECK(field(response, "deck") == NULL && field(response, "grouped") == NULL);
    const cJSON *ids = field(response, "ids");
    CHECK(cJSON_IsArray(ids) && cJSON_GetArraySize(ids) == 1);
    CHECK(cJSON_GetArrayItem(ids, 0)->valueint == 10);
    cJSON_Delete(response);

    /* The UI timer publishes its reconciled view after the cache close. */
    state_lock();
    st = state_get();
    st->deck_reachable = 1;
    st->deck_position = 1;
    st->deck_focus_id = 10;
    st->deck_next_id = 0;
    st->grouped_home = true;
    st->grouped_manual = false;
    st->grouped_presenting = false;
    st->grouped_present_id = 0;
    state_unlock();

    response = send_wire("{\"t\":\"cards_query\"}");
    CHECK(response != NULL && field(response, "view_pending") == NULL);
    CHECK(cJSON_IsObject(field(response, "deck")));
    CHECK(number(field(response, "deck"), "reachable") == 1);
    CHECK(number(field(response, "deck"), "focus_id") == 10);
    CHECK(cJSON_IsObject(field(response, "grouped")));
    CHECK(strcmp(string(field(response, "grouped"), "group"), "home") == 0);
    cJSON_Delete(response);

    /* ui_deck_show(false) hides retained IDs, so they are not live references. */
    state_lock();
    st = state_get();
    st->deck_enabled = false;
    st->deck_reachable = 0;
    st->deck_position = 0;
    st->deck_focus_id = 11;
    st->deck_next_id = 11;
    state_unlock();
    response = send_wire("{\"t\":\"cards_query\"}");
    CHECK(response != NULL && field(response, "view_pending") == NULL);
    CHECK(cJSON_IsNull(field(field(response, "deck"), "focus_id")));
    CHECK(cJSON_IsNull(field(field(response, "deck"), "next_id")));
    cJSON_Delete(response);
}

static void test_legacy_compatibility(void)
{
    CHECK(send_wire(
        "{\"t\":\"sync\",\"bar\":{\"zones\":[]},\"notifs\":["
        "{\"id\":4,\"summary\":\"legacy\",\"body\":\"body\"}],\"notifs_overflow\":0}") == NULL);
    state_lock();
    const status_state_t *st = state_get();
    CHECK(!st->grouped_enabled && st->grouped_session == 0);
    CHECK(!st->presentation.active && st->notif_count == 1 && st->notifs[0].id == 4);
    state_unlock();

    cJSON *response = send_wire("{\"t\":\"cards_query\"}");
    const cJSON *grouped = field(response, "grouped");
    CHECK(cJSON_IsFalse(field(grouped, "enabled")));
    CHECK(number(grouped, "session") == 0);
    CHECK(cJSON_IsNull(field(grouped, "present_id")));
    CHECK(number(grouped, "remaining_ms") == 0);
    cJSON_Delete(response);
}

static cJSON *make_readback(void)
{
    cJSON *result = cJSON_CreateObject();
    state_lock();
    const status_state_t *st = state_get();
    int ids[STATUS_MAX_NOTIFS];
    int count = st->notif_count;
    if (count > STATUS_MAX_NOTIFS) {
        count = STATUS_MAX_NOTIFS;
    }
    for (int i = 0; i < count; i++) {
        ids[i] = st->notifs[i].id;
    }
    const int overflow = st->notif_overflow;
    const bool enabled = st->grouped_enabled;
    const int session = enabled ? st->grouped_session : 0;
    const int generation = enabled ? st->grouped_generation : 0;
    const int present_id = st->grouped_present_id;
    const bool presenting = enabled && !st->grouped_manual && st->grouped_presenting;
    const bool home = !enabled || st->grouped_home;
    const bool manual = enabled && !home && st->grouped_manual;
    const bool persistent = st->grouped_persistent;
    const int64_t deadline = st->grouped_deadline_us;
    const bool have_sync = st->got_sync;
    state_unlock();

    cJSON_AddNumberToObject(result, "count", count);
    cJSON_AddNumberToObject(result, "overflow", overflow);
    cJSON_AddBoolToObject(result, "got_sync", have_sync);
    cJSON *array = cJSON_AddArrayToObject(result, "ids");
    for (int i = 0; i < count; i++) {
        cJSON_AddItemToArray(array, cJSON_CreateNumber(ids[i]));
    }
    cJSON *grouped = cJSON_AddObjectToObject(result, "grouped");
    cJSON_AddBoolToObject(grouped, "enabled", enabled);
    cJSON_AddNumberToObject(grouped, "session", session);
    cJSON_AddStringToObject(grouped, "group", home ? "home" : "notifications");
    cJSON_AddBoolToObject(grouped, "manual", manual);
    cJSON_AddNumberToObject(grouped, "generation", generation);
    if (presenting && present_id > 0) {
        cJSON_AddNumberToObject(grouped, "present_id", present_id);
        const int remaining = persistent ? -1 : deadline > s_now_us
            ? (int)((deadline - s_now_us + 999) / 1000) : 0;
        cJSON_AddNumberToObject(grouped, "remaining_ms", remaining);
    } else {
        cJSON_AddNullToObject(grouped, "present_id");
        cJSON_AddNumberToObject(grouped, "remaining_ms", 0);
    }
    return result;
}

static void emit_line_json_result(void)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *outbound = cJSON_AddArrayToObject(root, "outbound");
    for (int i = 0; i < s_outbound_count; i++) {
        cJSON *message = cJSON_Parse(s_outbound[i]);
        if (message != NULL) {
            cJSON_AddItemToArray(outbound, message);
        }
    }
    cJSON_AddItemToObject(root, "readback", make_readback());
    char *encoded = cJSON_PrintUnformatted(root);
    if (encoded != NULL) {
        puts(encoded);
        cJSON_free(encoded);
    }
    cJSON_Delete(root);
}

static int run_line_json(void)
{
    state_init();
    char line[8192];
    while (fgets(line, sizeof(line), stdin) != NULL) {
        const size_t length = strlen(line);
        if (length == 0) {
            continue;
        }
        line[strcspn(line, "\r\n")] = '\0';
        clear_outbound();
        proto_handle_line(line);
        emit_line_json_result();
        fflush(stdout);
    }
    return ferror(stdin) ? 1 : 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "--line-json") == 0) {
        return run_line_json();
    }
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) {
        test_staged_validation_and_deltas();
        test_presentation_and_session_reset();
        test_status_and_outbound_actions();
        test_cards_status_pending_until_ui_publication();
        test_legacy_compatibility();
        printf("native protocol: %d checks passed\n", s_checks);
        return 0;
    }
    fprintf(stderr, "usage: %s --self-test | --line-json\n", argv[0]);
    return 2;
}
