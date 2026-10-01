#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <limits.h>
#include <math.h>
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

static void sync_grouped_actions(int session, const char *records, bool actions_enabled)
{
    cJSON *cards = parse_json(records);
    const int count = cJSON_GetArraySize(cards);
    char message[1200];
    snprintf(message, sizeof(message),
        "{\"t\":\"sync_begin\",\"tx\":27,\"count\":%d,\"limit\":%d,"
        "\"overflow\":0,\"bar\":{\"zones\":[]},\"dashboard\":{},"
        "\"grouped\":{\"session\":%d}%s}",
        count, count, session,
        actions_enabled ? ",\"actions\":{\"enabled\":true}" : "");
    CHECK(send_wire(message) == NULL);
    if (count > 0) {
        char chunk[4096];
        snprintf(chunk, sizeof(chunk),
            "{\"t\":\"sync_cards\",\"tx\":27,\"start\":0,\"notifs\":%s}", records);
        CHECK(send_wire(chunk) == NULL);
    }
    CHECK(send_wire("{\"t\":\"sync_commit\",\"tx\":27}") == NULL);
    cJSON_Delete(cards);
}

static void sync_grouped_history(int session, const char *records)
{
    cJSON *cards = parse_json(records);
    const int count = cJSON_GetArraySize(cards);
    char message[1200];
    snprintf(message, sizeof(message),
        "{\"t\":\"sync_begin\",\"tx\":31,\"count\":%d,\"limit\":%d,"
        "\"overflow\":0,\"bar\":{\"zones\":[]},\"dashboard\":{},"
        "\"grouped\":{\"session\":%d,\"history\":true}}",
        count, count, session);
    CHECK(send_wire(message) == NULL);
    if (count > 0) {
        char chunk[4096];
        snprintf(chunk, sizeof(chunk),
            "{\"t\":\"sync_cards\",\"tx\":31,\"start\":0,\"notifs\":%s}", records);
        CHECK(send_wire(chunk) == NULL);
    }
    CHECK(send_wire("{\"t\":\"sync_commit\",\"tx\":31}") == NULL);
    cJSON_Delete(cards);
}

static void body_style_delta(int session, int id, int revision, const char *body,
                             const char *runs, bool include_history)
{
    char message[4096];
    const char *style_fields = runs == NULL ? "" : runs;
    if (include_history) {
        snprintf(message, sizeof(message),
            "{\"t\":\"notify\",\"session\":%d,\"id\":%d,\"app\":\"test\","
            "\"summary\":\"body style\",\"body\":\"%s\",\"urgency\":1,"
            "\"history\":{\"rev\":%d,\"age_ms\":0,\"remaining_ms\":10000}%s}",
            session, id, body, revision, style_fields);
    } else {
        snprintf(message, sizeof(message),
            "{\"t\":\"notify\",\"session\":%d,\"id\":%d,\"app\":\"test\","
            "\"summary\":\"body style\",\"body\":\"%s\",\"urgency\":1%s}",
            session, id, body, style_fields);
    }
    CHECK(send_wire(message) == NULL);
}

static cJSON *action_result(int session, uint32_t boot_id, int id, int revision,
                            int request, const char *status)
{
    cJSON *obj = cJSON_CreateObject();
    cJSON_AddStringToObject(obj, "t", "action_result");
    cJSON_AddNumberToObject(obj, "session", session);
    cJSON_AddNumberToObject(obj, "boot_id", (double)boot_id);
    cJSON_AddNumberToObject(obj, "id", id);
    cJSON_AddNumberToObject(obj, "open_rev", revision);
    cJSON_AddNumberToObject(obj, "request", request);
    cJSON_AddStringToObject(obj, "status", status);
    return obj;
}

static bool apply_action_result(uint32_t boot_id, int session, int id,
                                int revision, int request, const char *status)
{
    cJSON *obj = action_result(session, boot_id, id, revision, request, status);
    const bool accepted = state_apply_action_result(obj, boot_id);
    cJSON_Delete(obj);
    return accepted;
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

static void test_notification_actions_lifecycle(void)
{
    state_init();
    sync_grouped_actions(501,
        "[{\"id\":10,\"app\":\"A\",\"summary\":\"ready\",\"body\":\"a\",\"urgency\":1,"
        "\"open\":{\"rev\":5,\"state\":\"ready\"}},"
        "{\"id\":11,\"app\":\"B\",\"summary\":\"history\",\"body\":\"b\",\"urgency\":1,"
        "\"open\":{\"rev\":3,\"state\":\"unavailable\"}}]", true);

    state_lock();
    status_state_t *st = state_get();
    CHECK(st->actions_enabled && st->grouped_enabled && st->grouped_session == 501);
    CHECK(st->notif_count == 2 && st->notifs[0].id == 10 && st->notifs[1].id == 11);
    CHECK(st->notifs[0].open_revision == 5 && st->notifs[0].open_ready);
    CHECK(st->notifs[1].open_revision == 3 && !st->notifs[1].open_ready);
    CHECK(state_action_open_enabled(st, &st->notifs[0], s_now_us));
    CHECK(!state_action_open_enabled(st, &st->notifs[1], s_now_us));
    state_unlock();

    /* A malformed action field rejects its whole staged chunk. */
    CHECK(send_wire(
        "{\"t\":\"sync_begin\",\"tx\":28,\"count\":1,\"limit\":1,\"overflow\":0,"
        "\"bar\":{\"zones\":[]},\"dashboard\":{},\"grouped\":{\"session\":501},"
        "\"actions\":{\"enabled\":true}}") == NULL);
    cJSON *response = send_wire(
        "{\"t\":\"sync_cards\",\"tx\":28,\"start\":0,\"notifs\":["
        "{\"id\":99,\"app\":\"X\",\"summary\":\"bad\",\"body\":\"x\",\"urgency\":1}]}");
    CHECK(response && strcmp(string(response, "reason"), "sync_cards_invalid") == 0);
    cJSON_Delete(response);
    state_lock();
    CHECK(state_get()->notif_count == 2 && state_get()->notifs[0].id == 10);
    state_unlock();

    /* Metadata deltas update only existing records and enforce revision order. */
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":10,"
        "\"open\":{\"rev\":4,\"state\":\"unavailable\"}}") == NULL);
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":10,"
        "\"open\":{\"rev\":5,\"state\":\"unavailable\"}}") == NULL);
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":99,"
        "\"open\":{\"rev\":6,\"state\":\"ready\"}}") == NULL);
    state_lock();
    CHECK(state_get()->notifs[0].id == 10 && state_get()->notifs[0].open_revision == 5);
    state_unlock();
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":10,"
        "\"open\":{\"rev\":6,\"state\":\"unavailable\"}}") == NULL);
    state_lock();
    CHECK(state_get()->notifs[0].id == 10 && state_get()->notifs[0].open_revision == 6);
    CHECK(!state_get()->notifs[0].open_ready && state_get()->notif_focus_seq == 0);
    state_unlock();

    /* An older atomic snapshot cannot restore revoked availability. */
    CHECK(send_wire(
        "{\"t\":\"sync_begin\",\"tx\":29,\"count\":1,\"limit\":1,\"overflow\":0,"
        "\"bar\":{\"zones\":[]},\"dashboard\":{},\"grouped\":{\"session\":501},"
        "\"actions\":{\"enabled\":true}}") == NULL);
    CHECK(send_wire(
        "{\"t\":\"sync_cards\",\"tx\":29,\"start\":0,\"notifs\":["
        "{\"id\":10,\"app\":\"A\",\"summary\":\"stale\",\"body\":\"a\",\"urgency\":1,"
        "\"open\":{\"rev\":5,\"state\":\"ready\"}}]}") == NULL);
    response = send_wire("{\"t\":\"sync_commit\",\"tx\":29}");
    CHECK(response != NULL && strcmp(string(response, "reason"), "sync_commit_invalid") == 0);
    cJSON_Delete(response);
    state_lock();
    CHECK(state_get()->notif_count == 2 && state_get()->notifs[0].id == 10);
    CHECK(state_get()->notifs[0].open_revision == 6 && !state_get()->notifs[0].open_ready);
    state_unlock();

    /* Negotiated notify requires metadata, and replacements must move revision forward. */
    CHECK(send_wire(
        "{\"t\":\"notify\",\"session\":501,\"id\":10,\"app\":\"A\","
        "\"summary\":\"missing action metadata\",\"body\":\"a\",\"urgency\":1}") == NULL);
    state_lock();
    CHECK(strcmp(state_get()->notifs[0].summary, "ready") == 0);
    state_unlock();
    CHECK(send_wire(
        "{\"t\":\"notify\",\"session\":501,\"id\":10,\"app\":\"A\","
        "\"summary\":\"replacement\",\"body\":\"a2\",\"urgency\":1,"
        "\"open\":{\"rev\":7,\"state\":\"ready\"},\"cached\":true,\"total\":2}") == NULL);
    state_lock();
    CHECK(state_get()->notifs[1].id == 10 && state_get()->notifs[1].open_revision == 7);
    CHECK(state_get()->notifs[1].open_ready && state_get()->notif_focus_seq == 0);
    state_unlock();

    const uint32_t high_boot = UINT32_MAX;
    int action_session = 0, request = 0;
    CHECK(state_action_begin(high_boot, 10, 7, s_now_us, &action_session, &request));
    CHECK(action_session == 501 && request == 1);
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":11,"
        "\"open\":{\"rev\":4,\"state\":\"ready\"}}") == NULL);
    CHECK(!state_action_begin(high_boot, 10, 7, s_now_us, &action_session, &request));
    CHECK(!state_action_begin(high_boot, 11, 4, s_now_us, &action_session, &request));

    cJSON *wrong = action_result(501, high_boot - 1, 10, 7, 1, "dispatched");
    CHECK(!state_apply_action_result(wrong, high_boot));
    cJSON_Delete(wrong);
    CHECK(!apply_action_result(high_boot, 502, 10, 7, 1, "dispatched"));
    CHECK(!apply_action_result(high_boot, 501, 10, 6, 1, "dispatched"));
    CHECK(!apply_action_result(high_boot, 501, 10, 7, 2, "dispatched"));
    CHECK(!apply_action_result(high_boot, 501, 10, 7, 1, "invented"));
    wrong = action_result(501, high_boot, 10, 7, 1, "dispatched");
    cJSON_ReplaceItemInObjectCaseSensitive(wrong, "boot_id", cJSON_CreateNumber(4294967296.0));
    CHECK(!state_apply_action_result(wrong, high_boot));
    cJSON_ReplaceItemInObjectCaseSensitive(wrong, "boot_id", cJSON_CreateNumber(4294967295.5));
    CHECK(!state_apply_action_result(wrong, high_boot));
    cJSON_ReplaceItemInObjectCaseSensitive(wrong, "boot_id", cJSON_CreateNumber(NAN));
    CHECK(!state_apply_action_result(wrong, high_boot));
    cJSON_Delete(wrong);
    state_lock();
    CHECK(state_get()->action_pending);
    state_unlock();

    /* A successful dispatch can close the card before its result arrives. */
    CHECK(send_wire("{\"t\":\"close\",\"id\":10,\"session\":501,\"total\":1}") == NULL);
    CHECK(apply_action_result(high_boot, 501, 10, 7, 1, "dispatched"));
    state_lock();
    CHECK(!state_get()->action_pending);
    CHECK(state_get()->action_feedback == STATUS_ACTION_FEEDBACK_NONE);
    state_unlock();

    native_protocol_advance_time(500000);
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":11,"
        "\"open\":{\"rev\":4,\"state\":\"ready\"}}") == NULL);
    CHECK(state_action_begin(high_boot, 11, 4, s_now_us, &action_session, &request));
    CHECK(request == 2);
    /* Same-session full sync preserves an in-flight tuple exactly. */
    sync_grouped_actions(501,
        "[{\"id\":11,\"app\":\"B\",\"summary\":\"ready again\",\"body\":\"b\",\"urgency\":1,"
        "\"open\":{\"rev\":4,\"state\":\"ready\"}}]", true);
    state_lock();
    CHECK(state_get()->action_pending && state_get()->action_pending_request == 2);
    state_unlock();
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":11,"
        "\"open\":{\"rev\":5,\"state\":\"unavailable\"}}") == NULL);
    state_lock();
    state_get()->grouped_manual = true;
    state_get()->grouped_generation++;
    state_unlock();
    CHECK(apply_action_result(high_boot, 501, 11, 4, 2, "dispatched"));
    state_lock();
    CHECK(state_get()->action_feedback == STATUS_ACTION_FEEDBACK_NONE);
    CHECK(!state_get()->action_pending);
    CHECK(state_get()->action_cooldown_until_us == s_now_us + 500000);
    CHECK(!state_get()->action_blocked);
    state_unlock();
    CHECK(!state_action_begin(high_boot, 11, 5, s_now_us, &action_session, &request));
    native_protocol_advance_time(500000);
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":11,"
        "\"open\":{\"rev\":6,\"state\":\"ready\"}}") == NULL);
    CHECK(state_action_begin(high_boot, 11, 6, s_now_us, &action_session, &request));
    CHECK(request == 3);
    CHECK(apply_action_result(high_boot, 501, 11, 6, 3, "failed"));
    state_lock();
    CHECK(state_get()->action_feedback == STATUS_ACTION_FEEDBACK_TRY_AGAIN);
    CHECK(state_get()->action_blocked && state_get()->action_blocked_open_rev == 6);
    state_unlock();
    CHECK(!state_action_begin(high_boot, 11, 6, s_now_us, &action_session, &request));

    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":11,"
        "\"open\":{\"rev\":7,\"state\":\"ready\"}}") == NULL);
    native_protocol_advance_time(500000);
    CHECK(state_action_begin(high_boot, 11, 7, s_now_us, &action_session, &request));
    CHECK(request == 4);
    native_protocol_advance_time(3000000);
    state_action_tick(s_now_us);
    state_lock();
    CHECK(!state_get()->action_pending);
    CHECK(state_get()->action_feedback == STATUS_ACTION_FEEDBACK_NO_CONFIRMATION);
    CHECK(state_get()->action_blocked && state_get()->action_blocked_open_rev == 7);
    state_unlock();
    CHECK(!state_action_begin(high_boot, 11, 7, s_now_us, &action_session, &request));

    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":11,"
        "\"open\":{\"rev\":8,\"state\":\"ready\"}}") == NULL);
    native_protocol_advance_time(500000);
    CHECK(state_action_begin(high_boot, 11, 8, s_now_us, &action_session, &request));
    CHECK(request == 5);
    state_action_disconnect();
    state_lock();
    CHECK(!state_get()->action_pending && state_get()->action_blocked_open_rev == 8);
    state_unlock();
    CHECK(!apply_action_result(high_boot, 501, 11, 6, 5, "dispatched"));

    /* Host-session change and action negotiation removal ignore old replies. */
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":501,\"id\":11,"
        "\"open\":{\"rev\":9,\"state\":\"ready\"}}") == NULL);
    CHECK(state_action_begin(high_boot, 11, 9, s_now_us, &action_session, &request));
    CHECK(request == 6);
    sync_grouped_actions(502,
        "[{\"id\":11,\"app\":\"B\",\"summary\":\"new host\",\"body\":\"b\",\"urgency\":1,"
        "\"open\":{\"rev\":1,\"state\":\"ready\"}}]", true);
    state_lock();
    CHECK(!state_get()->action_pending && state_get()->grouped_session == 502);
    state_unlock();
    CHECK(!apply_action_result(high_boot, 501, 11, 9, 6, "dispatched"));
    CHECK(state_action_begin(high_boot, 11, 1, s_now_us, &action_session, &request));
    CHECK(request == 7); /* The boot-scoped counter survives host sessions. */
    CHECK(apply_action_result(high_boot, 502, 11, 1, 7, "unknown"));
    state_lock();
    CHECK(state_get()->action_feedback == STATUS_ACTION_FEEDBACK_NO_CONFIRMATION);
    CHECK(state_get()->action_blocked && state_get()->action_blocked_open_rev == 1);
    state_unlock();
    CHECK(!state_action_begin(high_boot, 11, 1, s_now_us, &action_session, &request));
    native_protocol_advance_time(2000000);
    state_action_tick(s_now_us);
    state_lock();
    CHECK(state_get()->action_feedback == STATUS_ACTION_FEEDBACK_NONE);
    CHECK(state_get()->action_blocked && state_get()->action_blocked_open_rev == 1);
    state_unlock();
    CHECK(!state_action_begin(high_boot, 11, 1, s_now_us, &action_session, &request));
    native_protocol_advance_time(500000);
    CHECK(send_wire(
        "{\"t\":\"card_action\",\"session\":502,\"id\":11,"
        "\"open\":{\"rev\":2,\"state\":\"ready\"}}") == NULL);
    CHECK(state_action_begin(high_boot, 11, 2, s_now_us, &action_session, &request));
    CHECK(request == 8);
    sync_grouped_actions(502,
        "[{\"id\":11,\"app\":\"B\",\"summary\":\"legacy action off\",\"body\":\"b\",\"urgency\":1}]",
        false);
    state_lock();
    CHECK(!state_get()->action_pending && !state_get()->actions_enabled);
    CHECK(state_get()->notifs[0].open_revision == 0 && !state_get()->notifs[0].open_ready);
    state_unlock();
    CHECK(!apply_action_result(high_boot, 502, 11, 2, 8, "dispatched"));
    state_lock();
    state_get()->action_next_request = INT32_MAX;
    state_unlock();
    sync_grouped_actions(503,
        "[{\"id\":11,\"app\":\"B\",\"summary\":\"ready\",\"body\":\"b\",\"urgency\":1,"
        "\"open\":{\"rev\":1,\"state\":\"ready\"}}]", true);
    native_protocol_advance_time(500000);
    CHECK(!state_action_begin(high_boot, 11, 1, s_now_us, &action_session, &request));
    state_lock();
    CHECK(state_get()->action_request_exhausted);
    state_unlock();
}

static void test_status_and_outbound_actions(void)
{
    state_init();
    sync_grouped_actions(78,
        "[{\"id\":10,\"app\":\"A\",\"summary\":\"ready\",\"body\":\"a\",\"urgency\":1,"
        "\"open\":{\"rev\":12,\"state\":\"ready\"}}]", true);
    clear_outbound();
    proto_handle_line("{\"t\":\"hello\"}");
    CHECK(s_outbound_count == 1);
    cJSON *hello = parse_json(s_outbound[0]);
    const uint32_t boot_id = (uint32_t)number(hello, "boot_id");
    bool found_actions = false, found_history = false, found_body_styles = false;
    const cJSON *hello_cap = field(hello, "cap");
    const cJSON *hello_item = NULL;
    cJSON_ArrayForEach(hello_item, hello_cap) {
        found_actions |= cJSON_IsString(hello_item) &&
            strcmp(hello_item->valuestring, "notification-actions-v1") == 0;
        found_history |= cJSON_IsString(hello_item) &&
            strcmp(hello_item->valuestring, "notification-history-v1") == 0;
        found_body_styles |= cJSON_IsString(hello_item) &&
            strcmp(hello_item->valuestring, "notification-body-style-v1") == 0;
    }
    CHECK(boot_id != 0 && found_actions && found_history && found_body_styles);
    cJSON_Delete(hello);

    clear_outbound();
    CHECK(proto_send_input_activate(10, 12));
    CHECK(s_outbound_count == 1);
    cJSON *activate = parse_json(s_outbound[0]);
    CHECK(strcmp(string(activate, "action"), "activate") == 0);
    CHECK(number(activate, "session") == 78 && number(activate, "id") == 10);
    CHECK(number(activate, "open_rev") == 12 && number(activate, "request") == 1);
    CHECK((uint32_t)number(activate, "boot_id") == boot_id);
    cJSON_Delete(activate);
    CHECK(!proto_send_input_activate(10, 12)); /* Only one global request is pending. */

    char reply[256];
    snprintf(reply, sizeof(reply),
        "{\"t\":\"action_result\",\"session\":78,\"boot_id\":%u,"
        "\"id\":10,\"open_rev\":12,\"request\":1,\"status\":\"dispatched\"}", boot_id);
    CHECK(send_wire(reply) == NULL);

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
    st->dashboard = (status_dashboard_t){
        .valid = true,
        .cpu_valid = true, .cpu = .25f,
        .cpu_freq_mhz_valid = true, .cpu_freq_mhz = 3600.5,
        .mem_valid = true, .mem = .75f,
        .mem_used_bytes_valid = true, .mem_used_bytes = 25769803776.0,
        .network_valid = true, .network = true,
        .rx_bytes_per_s_valid = true, .rx_bytes_per_s = 0,
        .tx_bytes_per_s_valid = true, .tx_bytes_per_s = 125000.5,
    };
    state_unlock();

    CHECK(send_wire("{\"t\":\"close\",\"id\":11,\"session\":79,\"total\":1}") == NULL);
    cJSON *response = send_wire("{\"t\":\"cards_query\"}");
    CHECK(response != NULL && number(response, "count") == 1);
    CHECK(cJSON_IsTrue(field(response, "view_pending")));
    CHECK(field(response, "deck") == NULL && field(response, "grouped") == NULL);
    const cJSON *dashboard = field(response, "dashboard");
    CHECK(cJSON_IsObject(dashboard));
    CHECK(field(dashboard, "cpu")->valuedouble == .25
        && field(dashboard, "mem")->valuedouble == .75);
    CHECK(cJSON_IsTrue(field(dashboard, "network")));
    CHECK(number(dashboard, "rx_bytes_per_s") == 0);
    CHECK(field(dashboard, "tx_bytes_per_s")->valuedouble == 125000.5);
    CHECK(field(dashboard, "cpu_freq_mhz")->valuedouble == 3600.5);
    CHECK(field(dashboard, "mem_used_bytes")->valuedouble == 25769803776.0);
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
    const cJSON *dashboard = field(response, "dashboard");
    CHECK(cJSON_IsObject(dashboard));
    CHECK(cJSON_IsNull(field(dashboard, "rx_bytes_per_s")));
    CHECK(cJSON_IsNull(field(dashboard, "tx_bytes_per_s")));
    CHECK(cJSON_IsNull(field(dashboard, "cpu_freq_mhz")));
    CHECK(cJSON_IsNull(field(dashboard, "mem_used_bytes")));
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

static void sync_history_card(int revision, int age_ms, int remaining_ms)
{
    cJSON *begin = parse_json(
        "{\"tx\":50,\"count\":1,\"limit\":32,\"overflow\":0,"
        "\"bar\":{\"zones\":[]},\"dashboard\":{},"
        "\"grouped\":{\"session\":901,\"history\":true}}");
    CHECK(state_sync_begin(begin));
    cJSON_Delete(begin);
    cJSON *chunk = parse_json("{\"tx\":50,\"start\":0,\"notifs\":[{\"id\":1,\"urgency\":1}]}");
    cJSON *card = cJSON_GetArrayItem(field(chunk, "notifs"), 0);
    char body[401];
    memset(body, 'a', sizeof(body) - 1); body[sizeof(body) - 1] = '\0';
    cJSON_AddStringToObject(card, "body", body);
    cJSON *history = cJSON_AddObjectToObject(card, "history");
    cJSON_AddNumberToObject(history, "rev", revision);
    cJSON_AddNumberToObject(history, "age_ms", age_ms);
    cJSON_AddNumberToObject(history, "remaining_ms", remaining_ms);
    CHECK(state_sync_cards(chunk));
    cJSON_Delete(chunk);
    cJSON *commit = parse_json("{\"tx\":50}");
    CHECK(state_sync_commit(commit, NULL, NULL, NULL));
    cJSON_Delete(commit);
}

static void test_history_deadlines_and_projection(void)
{
    state_init();
    const int64_t started = s_now_us;
    sync_history_card(1, 120000, 5000);
    state_lock();
    CHECK(state_get()->history_enabled);
    CHECK(state_get()->notifs[0].history_updated_us == started - 120000000);
    CHECK(state_get()->notifs[0].history_deadline_us == started + 5000000);
    CHECK(strlen(state_get()->notifs[0].body) == 400);
    state_get()->deck_enabled = true;
    state_get()->grouped_home = true; /* Prior UI publication during mode switch. */
    state_unlock();
    cJSON *pending = send_wire("{\"t\":\"cards_query\"}");
    CHECK(pending != NULL && cJSON_IsTrue(field(pending, "view_pending")));
    CHECK(field(pending, "grouped") == NULL);
    cJSON_Delete(pending);
    state_lock();
    state_get()->grouped_home = false;
    state_get()->deck_reachable = 1;
    state_get()->deck_position = 1;
    state_get()->deck_focus_id = 1;
    state_unlock();
    cJSON *settled = send_wire("{\"t\":\"cards_query\"}");
    CHECK(settled != NULL && !cJSON_IsTrue(field(settled, "view_pending")));
    CHECK(strcmp(string(field(settled, "grouped"), "group"), "notifications") == 0);
    cJSON_Delete(settled);

    s_now_us += 2000000;
    sync_history_card(1, 0, 5000); /* Snapshot cannot restart age/lifetime. */
    state_lock();
    CHECK(state_get()->notifs[0].history_updated_us == started - 120000000);
    CHECK(state_get()->notifs[0].history_deadline_us == started + 5000000);
    state_unlock();
    s_connected = false;
    s_now_us = started + 5000001;
    state_lock();
    status_state_t *st = state_get();
    st->actions_enabled = true;
    st->notifs[0].open_revision = 1;
    st->notifs[0].open_ready = true;
    CHECK(!state_action_open_enabled(st, &st->notifs[0], s_now_us));
    st->actions_enabled = false;
    state_unlock();
    state_history_tick(s_now_us);
    state_lock(); CHECK(state_get()->notif_count == 0); state_unlock();
    sync_history_card(1, 0, 5000); /* Repeated stale sync cannot resurrect it. */
    state_lock(); CHECK(state_get()->notif_count == 0); state_unlock();
    CHECK(send_wire("{\"t\":\"notify\",\"id\":1,\"session\":901,\"urgency\":1,"
        "\"history\":{\"rev\":1,\"age_ms\":0,\"remaining_ms\":5000}}") == NULL);
    state_lock(); CHECK(state_get()->notif_count == 0); state_unlock();
    CHECK(send_wire("{\"t\":\"notify\",\"id\":1,\"session\":901,\"urgency\":1,"
        "\"history\":{\"rev\":2,\"age_ms\":0,\"remaining_ms\":5000}}") == NULL);
    state_lock();
    CHECK(state_get()->notif_count == 1 && state_get()->notifs[0].history_revision == 2);
    state_unlock();
    CHECK(send_wire("{\"t\":\"notify\",\"id\":2,\"session\":901,\"urgency\":1,"
        "\"history\":{\"rev\":true,\"age_ms\":0,\"remaining_ms\":5000}}") == NULL);
    CHECK(send_wire("{\"t\":\"notify\",\"id\":2,\"session\":901,\"urgency\":1}") == NULL);
    state_lock(); CHECK(state_get()->notif_count == 1); state_unlock();
    s_now_us += 5000000;
    state_history_tick(s_now_us);
    sync_history_card(1, 0, 5000); /* Older replay cannot lower the tombstone. */
    sync_history_card(2, 0, 5000);
    state_lock(); CHECK(state_get()->notif_count == 0); state_unlock();
    sync_history_card(3, 0, 5000); /* Only a genuinely newer revision returns. */
    state_lock(); CHECK(state_get()->notif_count == 1); state_unlock();
    clear_outbound();
    proto_send_input_history_idle(0);
    CHECK(s_outbound_count == 1);
    cJSON *idle = parse_json(s_outbound[0]);
    CHECK(cJSON_IsFalse(field(idle, "manual")));
    CHECK(strcmp(string(idle, "group"), "notifications") == 0);
    cJSON_Delete(idle);

    /* Switching to an old peer's grouped contract keeps its short body. */
    char records[550], body[401];
    memset(body, 'b', sizeof(body) - 1); body[sizeof(body) - 1] = '\0';
    snprintf(records, sizeof(records), "[{\"id\":1,\"body\":\"%s\",\"urgency\":1}]", body);
    sync_grouped(902, records);
    state_lock();
    CHECK(!state_get()->history_enabled && strlen(state_get()->notifs[0].body) == 159);
    state_unlock();
    s_connected = true;
}

static void test_notification_body_style_ranges(void)
{
    state_init();
    sync_grouped_history(903,
        "[{\"id\":1,\"app\":\"test\",\"summary\":\"styled\","
        "\"body\":\"Aé東京Z\",\"urgency\":1,"
        "\"history\":{\"rev\":1,\"age_ms\":0,\"remaining_ms\":10000},"
        "\"body_runs\":[{\"start\":1,\"end\":9,\"style\":3}]}]");
    state_lock();
    const status_state_t *st = state_get();
    CHECK(st->history_enabled && st->notif_count == 1);
    CHECK(strcmp(st->notifs[0].body, "Aé東京Z") == 0);
    CHECK(st->notifs[0].body_run_count == 1);
    CHECK(st->notifs[0].body_runs[0].start == 1);
    CHECK(st->notifs[0].body_runs[0].end == 9);
    CHECK(st->notifs[0].body_runs[0].style == 3);
    state_unlock();

    /* A replacement without style metadata must remove the prior emphasis. */
    body_style_delta(903, 1, 2, "plain replacement", NULL, true);
    state_lock();
    st = state_get();
    CHECK(st->notif_count == 1 && strcmp(st->notifs[0].body, "plain replacement") == 0);
    CHECK(st->notifs[0].body_run_count == 0);
    state_unlock();

    /* Invalid byte boundaries and overlapping ranges fall back for the whole body. */
    body_style_delta(903, 1, 3, "Aé東京Z",
        ",\"body_runs\":[{\"start\":2,\"end\":9,\"style\":1}]", true);
    state_lock();
    st = state_get();
    CHECK(st->notif_count == 1 && strcmp(st->notifs[0].body, "Aé東京Z") == 0);
    CHECK(st->notifs[0].body_run_count == 0);
    state_unlock();

    body_style_delta(903, 1, 4, "abcdefghij",
        ",\"body_runs\":[{\"start\":1,\"end\":5,\"style\":1},"
        "{\"start\":4,\"end\":6,\"style\":2}]", true);
    state_lock();
    st = state_get();
    CHECK(st->notif_count == 1 && strcmp(st->notifs[0].body, "abcdefghij") == 0);
    CHECK(st->notifs[0].body_run_count == 0);
    state_unlock();

    /* The fixed parser bound also fails closed without dropping the card. */
    char runs[1200] = ",\"body_runs\":[";
    size_t used = strlen(runs);
    for (int i = 0; i < STATUS_NOTIF_BODY_RUNS_MAX + 1; i++) {
        int written = snprintf(runs + used, sizeof(runs) - used,
            "%s{\"start\":%d,\"end\":%d,\"style\":1}",
            i == 0 ? "" : ",", i * 2, i * 2 + 1);
        CHECK(written > 0 && (size_t)written < sizeof(runs) - used);
        used += (size_t)written;
    }
    CHECK(used + 2 < sizeof(runs));
    runs[used++] = ']';
    runs[used] = '\0';
    body_style_delta(903, 1, 5,
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", runs, true);
    state_lock();
    st = state_get();
    CHECK(st->notif_count == 1 && st->notifs[0].body_run_count == 0);
    CHECK(strcmp(st->notifs[0].body, "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx") == 0);
    state_unlock();

    /* A legacy grouped peer may send the unknown field; C ignores it. */
    sync_grouped(904,
        "[{\"id\":2,\"app\":\"test\",\"summary\":\"legacy\","
        "\"body\":\"plain\",\"urgency\":1,"
        "\"body_runs\":[{\"start\":0,\"end\":5,\"style\":1}]}]");
    state_lock();
    st = state_get();
    CHECK(!st->history_enabled && st->notif_count == 1 && st->notifs[0].id == 2);
    CHECK(st->notifs[0].body_run_count == 0);
    state_unlock();
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
        test_notification_actions_lifecycle();
        test_status_and_outbound_actions();
        test_cards_status_pending_until_ui_publication();
        test_legacy_compatibility();
        test_history_deadlines_and_projection();
        test_notification_body_style_ranges();
        printf("native protocol: %d checks passed\n", s_checks);
        return 0;
    }
    fprintf(stderr, "usage: %s --self-test | --line-json\n", argv[0]);
    return 2;
}
