/* Direct-state LVGL scenarios. Parser/state composition is a separate gate. */
#define main legacy_fixture_main
#include "test_ui.c"
#undef main

static int s_session_counter = 100;
static void groups_reset(const int *ids, int count)
{
    if (s_pointer_state == LV_INDEV_STATE_PRESSED) pointer_release(320, 90);
    grouped_cancel();
    step(200);
    s_state.grouped_enabled = true;
    s_state.actions_enabled = false;
    s_state.action_pending = false;
    s_state.action_pending_id = 0;
    s_state.action_pending_open_rev = 0;
    s_state.action_pending_request = 0;
    s_state.action_cooldown_until_us = 0;
    s_state.action_request_exhausted = false;
    s_state.action_blocked = false;
    s_state.action_feedback = STATUS_ACTION_FEEDBACK_NONE;
    s_state.action_feedback_until_us = 0;
    s_session_counter += 10;
    s_state.grouped_session = s_session_counter;
    s_state.presentation = (status_presentation_t){0};
    s_state.hidden_count = 0;
    s_state.notif_overflow = 0;
    s_state.dashboard = (status_dashboard_t){.valid = true, .cpu_valid = true,
        .cpu = .18f, .mem_valid = true, .mem = .43f, .network_valid = true, .network = true};
    set_raw_order(ids, count);
    ui_deck_tick(STATE_DIRTY_DASHBOARD | STATE_DIRTY_NOTIF);
    repaint();
    assert(s_group_home && !s_group_auto && !s_group_manual);
}

static void group_finish(void)
{
    for (int i = 0; i < 32 && (s_group_input.motion.state == DECK_INPUT_SETTLING ||
                              s_group_lifecycle.active); i++) step(20);
    assert(s_group_input.motion.state != DECK_INPUT_SETTLING);
    assert(!s_group_lifecycle.active);
    repaint();
}

static void group_swipe(int x, int y, int dx, int dy)
{
    pointer_press(x, y);
    pointer_move(x + dx / 2, y + dy / 2, 100);
    pointer_move(x + dx, y + dy, 100);
    pointer_release(x + dx, y + dy);
    group_finish();
}

static void present_begin(int generation, int id, int urgency, int duration_ms)
{
    s_state.presentation = (status_presentation_t){.generation = generation, .id = id,
        .urgency = urgency, .active = true, .persistent = duration_ms < 0,
        .deadline_us = s_now_us + (int64_t)duration_ms * 1000};
    ui_deck_tick(STATE_DIRTY_NOTIF);
    repaint();
}

static void present(int generation, int id, int urgency, int duration_ms)
{
    present_begin(generation, id, urgency, duration_ms);
    group_finish();
}

static void set_open(int id, int revision, bool ready)
{
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == id) {
            s_state.notifs[i].open_revision = revision;
            s_state.notifs[i].open_ready = ready;
            ui_deck_tick(STATE_DIRTY_NOTIF);
            repaint();
            return;
        }
    }
    assert(false);
}

static void navigation_and_geometry(void)
{
    int ids[] = {3, 2, 1};
    groups_reset(ids, 3);
    assert(capture_frame("groups-home"));
    assert(lv_obj_get_x(s_content) == 160);
    group_swipe(450, 90, -130, 0);
    assert(!s_group_home && s_group_manual && current_id() == 3);
    assert(lv_obj_get_width(s_cards[1].root) == 464);
    assert(lv_obj_get_height(s_cards[1].body) == 52);
    assert(capture_frame("groups-three"));
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 2);
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 1);
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 1); /* Oldest boundary. */
    group_swipe(350, 50, 0, 65);
    assert(current_id() == 2);
    group_swipe(350, 50, 0, 65);
    assert(current_id() == 3);
    group_swipe(350, 50, 0, 65);
    assert(current_id() == 3);
    group_swipe(350, 90, 120, 0);
    assert(s_group_home && !s_group_manual);
    group_swipe(350, 90, 120, 0);
    assert(s_group_home);
}

static void leases_and_manual(void)
{
    int ids[] = {3, 2, 1};
    groups_reset(ids, 3);
    present(1, 3, 1, 1000);
    assert(!s_group_home && s_group_auto);
    step(1001);
    assert(s_group_home && s_state.notif_count == 3);
    group_finish();
    group_swipe(450, 90, -130, 0);
    assert(!s_group_home && s_group_manual && !s_group_auto);
    present(2, 2, 1, 5000);
    assert(current_id() == 3); /* Normal arrival cannot steal manual focus. */
    present(3, 2, 2, 1000);
    assert(current_id() == 2 && s_group_auto && !s_group_manual);
    pointer_press(350, 100);
    pointer_move(350, 80, 100);
    assert(s_group_manual && !s_group_auto);
    pointer_release(350, 80);
    group_finish();
    step(1200);
    assert(!s_group_home && current_id() == 2);
    present(4, 3, 2, -1);
    step(2000);
    assert(!s_group_home && s_group_auto);
    s_state.presentation.active = false;
    ui_deck_tick(0);
    assert(s_group_home);
}

static void held_updates_and_removal(void)
{
    int ids[] = {3, 2, 1};
    groups_reset(ids, 3);
    present(1, 3, 1, -1);
    pointer_press(350, 110);
    pointer_move(350, 55, 100);
    assert(s_group_input.axis == GROUP_AXIS_VERTICAL && s_group_offset == -55);
    char original[160];
    snprintf(original, sizeof(original), "%s", label_storage(s_cards[1].body));
    strcpy(s_notifs[2].body, "Replacement while held");
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(strcmp(label_storage(s_cards[1].body), original) == 0);
    present(2, 1, 2, 3000);
    assert(current_id() == 3 && s_group_offset == -55);
    assert(capture_frame("groups-held-critical"));
    pointer_release(350, 55);
    group_finish();
    assert(current_id() == 1 && s_group_auto);

    groups_reset(ids, 3);
    present(1, 3, 1, -1);
    pointer_press(350, 110);
    present(2, 2, 1, 1000);
    assert(current_id() == 3); /* A body press freezes the current card. */
    pointer_release(350, 110);
    group_finish();
    assert(current_id() == 2 && s_group_auto); /* A tap does not cancel attention. */

    groups_reset(ids, 3);
    present(1, 3, 1, -1);
    pointer_press(350, 110);
    pointer_move(350, 55, 100);
    int without_destination[] = {3, 1};
    set_raw_order(without_destination, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    group_finish();
    assert(current_id() == 3);
    pointer_release(350, 55);
    group_finish();
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 1);

    groups_reset(ids, 3);
    present(1, 3, 1, -1);
    pointer_press(350, 110);
    pointer_move(350, 55, 100);
    pointer_release(350, 55);
    assert(s_group_input.motion.state == DECK_INPUT_SETTLING);
    const uint32_t old_generation = s_group_input.motion.generation;
    set_raw_order(without_destination, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_group_input.motion.generation != old_generation);
    group_finish();
    assert(current_id() == 3 && !reachable(2));

    groups_reset(ids, 3);
    present(1, 3, 1, -1);
    pointer_press(350, 110);
    pointer_move(350, 55, 100);
    int without_source[] = {2, 1};
    set_raw_order(without_source, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_group_input.motion.state == DECK_INPUT_IGNORED);
    pointer_release(350, 55);
    group_finish();
    assert(current_id() == 2 && !reachable(3));

    groups_reset(ids, 3);
    present(1, 3, 1, -1);
    pointer_press(350, 110);
    pointer_move(350, 55, 100);
    s_state.grouped_session++;
    s_state.presentation = (status_presentation_t){0}; /* Atomic session commit clears old lease. */
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_group_home && !s_group_auto);
    pointer_release(350, 55);
    group_finish();
    assert(s_group_home);
}

static void counts_controls_and_text(void)
{
    int ids[] = {1};
    groups_reset(ids, 1);
    strcpy(s_notifs[0].summary, "We've / we’ve — 東京");
    strcpy(s_notifs[0].body, "English notifications, 東京 が → ✓. A longer sentence wraps and truncates within the card.");
    present(1, 1, 1, -1);
    assert(lv_obj_get_height(s_cards[1].body) == 84);
    assert(!s_cards[0].valid && !s_cards[2].valid);
    assert(capture_frame("groups-one-fonts"));
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 1);
    pointer_press(350, 95); pointer_release(350, 95);
    assert(!s_group_home && s_dismiss_count == 0);
    /* Lower-left part of the enlarged target was outside the former button.
     * A drag from there cancels the button without acquiring either axis. */
    pointer_press(577, 60); pointer_move(470, 100, 100); pointer_release(470, 100);
    assert(s_dismiss_count == 0);
    assert(current_id() == 1 && !s_group_home);
    assert(lv_obj_get_x(s_cards[1].title) + lv_obj_get_width(s_cards[1].title)
           <= lv_obj_get_x(s_cards[1].dismiss) - 8);
    pointer_press(577, 60); pointer_release(577, 60);
    assert(s_dismiss_count == 1 && s_group_home);

    int pair[] = {2, 1};
    groups_reset(pair, 2);
    strcpy(s_notifs[0].summary, "東京 → ✓");
    strcpy(s_notifs[1].summary, "Build review is ready");
    strcpy(s_notifs[1].body, "We've reviewed the changes. Open the pull request for details and the next steps.");
    present(1, 2, 1, -1);
    lv_font_glyph_dsc_t glyph;
    assert(lv_font_get_glyph_dsc(s_meta, &glyph, 0x6771, 0));
    assert(capture_frame("groups-long-two"));

    int many[32]; for (int i = 0; i < 32; i++) many[i] = 33 - i;
    groups_reset(many, 32);
    s_state.notif_overflow = 1;
    s_state.dashboard.battery_present = true; s_state.dashboard.battery_level = .7f;
    present(1, 33, 1, -1);
    assert(strstr(label_storage(s_cards[1].position), "1 / 32") != NULL);
    assert(strstr(label_storage(s_cards[1].position), "+1 uncached") != NULL);
    assert(capture_frame("groups-capacity-battery"));
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 32);
    s_host_connected = false;
    ui_deck_tick(0);
    assert(s_group_home && !s_group_auto);
    groups_reset(ids, 0);
    assert(capture_frame("groups-empty"));
    group_swipe(350, 100, -120, 0);
    assert(!s_group_home && s_group_manual && s_deck.count == 0);
    assert(strcmp(lv_label_get_text(s_group_empty), "No notifications") == 0);
    assert(capture_frame("groups-empty-notifications"));
    group_swipe(350, 110, 0, -65);
    assert(!s_group_home && s_deck.count == 0);
    group_swipe(350, 100, 120, 0);
    assert(s_group_home && !s_group_manual);
    group_swipe(350, 100, -120, 0);
    pointer_press(350, 100); pointer_move(380, 100, 100);
    set_raw_order(ids, 1);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(!lv_obj_has_flag(s_group_empty, LV_OBJ_FLAG_HIDDEN));
    pointer_release(380, 100); group_finish();
    assert(!s_group_home && s_group_manual && current_id() == 1);
    assert(lv_obj_has_flag(s_group_empty, LV_OBJ_FLAG_HIDDEN));
}

static void action_controls_and_capture(void)
{
    int two[] = {2, 1};
    groups_reset(two, 2);
    s_state.actions_enabled = true;
    set_open(2, 5, true);
    snprintf(s_notifs[1].summary, sizeof(s_notifs[1].summary),
             "%s", "A very long English notification title with CJK 東京 → ✓");
    present(1, 2, 1, -1);
    assert(!s_group_home && s_group_auto);
    assert(!lv_obj_has_flag(s_cards[1].open, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_get_x(s_cards[1].open) == 328);
    assert(lv_obj_get_y(s_cards[1].open) == 0);
    assert(lv_obj_get_width(s_cards[1].open) == 64);
    assert(lv_obj_get_height(s_cards[1].open) == 48);
    assert(lv_obj_get_x(s_cards[1].dismiss) == 400);
    assert(lv_obj_get_width(s_cards[1].dismiss) == 64);
    assert(lv_obj_get_height(s_cards[1].dismiss) == 48);
    assert(lv_obj_get_width(s_cards[1].title) == 304);
    assert(lv_obj_get_width(s_cards[1].body) == 440);
    assert(lv_obj_get_height(s_cards[1].title) == 28);
    assert(lv_label_get_long_mode(s_cards[1].title) == LV_LABEL_LONG_MODE_CLIP);
    assert(strstr(label_storage(s_cards[1].title), "...") != NULL);
    assert(strncmp(label_storage(s_cards[1].body), "body-", 5) == 0);
    assert(strcmp(lv_label_get_text(s_cards[1].open_label), "Open") == 0);
    assert(lv_obj_has_flag(s_cards[0].open, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_cards[0].dismiss, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_get_width(s_cards[0].title) == 440);
    assert(capture_frame("actions-ready-long-english-cjk"));
    snprintf(s_notifs[1].summary, sizeof(s_notifs[1].summary), "%s",
             "東京で通知を確認するための長い見出し → ✓");
    ui_deck_tick(STATE_DIRTY_NOTIF);
    repaint();
    assert(capture_frame("actions-ready-cjk"));
    snprintf(s_notifs[1].summary, sizeof(s_notifs[1].summary),
             "%s", "A very long English notification title with CJK 東京 → ✓");
    ui_deck_tick(STATE_DIRTY_NOTIF);
    repaint();

    const int activations = s_activate_count;
    const int body_dismissals = s_dismiss_count;
    pointer_press(350, 100); pointer_release(350, 100);
    assert(s_activate_count == activations && current_id() == 2);
    assert(s_group_auto && s_dismiss_count == body_dismissals);
    pointer_press(528, 44); pointer_release(528, 44);
    assert(s_activate_count == activations + 1);
    assert(s_last_activate_id == 2 && s_last_activate_revision == 5);
    assert(s_group_manual && !s_group_auto);
    assert(s_state.action_pending && s_state.action_pending_id == 2);
    assert(strcmp(lv_label_get_text(s_cards[1].open_label), "…") == 0);
    assert(capture_frame("actions-pending"));
    const int pending_id = current_id();
    pointer_press(528, 44); pointer_release(528, 44);
    assert(s_activate_count == activations + 1 && current_id() == pending_id);
    assert(s_state.action_pending);
    s_state.action_pending = false;
    s_state.action_pending_id = 0;
    s_state.action_pending_open_rev = 0;
    s_state.action_pending_request = 0;
    s_group_manual = true;
    set_open(2, 6, false);
    assert(!lv_obj_has_flag(s_cards[1].open, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_get_width(s_cards[1].title) == 304);
    assert(strcmp(lv_label_get_text(s_cards[1].open_label), "Open") == 0);
    assert(capture_frame("actions-disabled"));
    const int before_disabled = s_activate_count;
    pointer_press(528, 44); pointer_release(528, 44);
    assert(s_activate_count == before_disabled);
    assert(s_group_manual);

    set_open(2, 7, true);
    const int before_edges = s_activate_count;
    pointer_press(496, 20); pointer_release(496, 20);
    assert(s_activate_count == before_edges + 1);
    s_state.action_pending = false;
    pointer_press(559, 67); pointer_release(559, 67);
    assert(s_activate_count == before_edges + 2);
    s_state.action_pending = false;

    /* The 8 px control gap and neighboring preview never acquire actions. */
    const int before_gap = s_activate_count;
    const int current = current_id();
    pointer_press(564, 44); pointer_release(564, 44);
    assert(s_activate_count == before_gap && current_id() == current);
    assert(!s_group_home);

    /* A button drag cancels, even when it crosses into × or returns. */
    pointer_press(528, 44); pointer_move(576, 44, 100);
    pointer_move(528, 44, 100); pointer_release(528, 44);
    assert(s_activate_count == before_gap);
    const int dismisses = s_dismiss_count;
    pointer_press(528, 44); pointer_move(510, 44, 100);
    pointer_release(528, 44);
    assert(s_activate_count == before_gap && s_dismiss_count == dismisses);
    pointer_press(528, 44); pointer_move(528, 60, 100);
    pointer_release(528, 60);
    assert(s_activate_count == before_gap);
    pointer_press(528, 44);
    grouped_cancel(); /* The production INDEV_RESET and PRESS_LOST path. */
    pointer_release(528, 44);
    assert(s_activate_count == before_gap && s_dismiss_count == dismisses);

    /* Revision change and removal while held cancel permanently. */
    pointer_press(528, 44);
    set_open(2, 8, true);
    pointer_release(528, 44);
    assert(s_activate_count == before_gap);
    pointer_press(528, 44);
    int only_one[] = {1};
    set_raw_order(only_one, 1);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    pointer_release(528, 44);
    assert(s_activate_count == before_gap);

    /* Body-start movement ending on Open remains ordinary swipe ownership. */
    groups_reset(two, 2);
    s_state.actions_enabled = true;
    set_open(2, 1, true);
    present(1, 2, 1, -1);
    const int before_body_drag = s_activate_count;
    pointer_press(350, 100);
    pointer_move(528, 44, 100);
    pointer_release(528, 44);
    group_finish();
    assert(s_activate_count == before_body_drag);

    /* Older peers keep the accepted 376 px header and hide Open entirely. */
    groups_reset(two, 2);
    present(1, 2, 1, -1);
    assert(lv_obj_has_flag(s_cards[1].open, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_get_width(s_cards[1].title) == 376);
    assert(lv_obj_get_width(s_cards[1].body) == 440);
    assert(lv_obj_get_x(s_cards[1].dismiss) == 400);
    assert(capture_frame("actions-legacy-baseline"));
}

static void lifecycle_capture(const char *prefix)
{
    for (int frame = 0; frame <= 12; frame++) {
        char name[80];
        snprintf(name, sizeof(name), "%s-%02d", prefix, frame);
        assert(capture_frame(name));
        if (frame < 12) step(15);
    }
    group_finish();
}

static void lifecycle_motion_and_removal(void)
{
    const int ids[] = {3, 2, 1};
    groups_reset(ids, 3);
    s_state.actions_enabled = true;
    set_open(3, 1, true);
    present_begin(1, 3, 1, -1);
    assert(s_group_lifecycle.active && s_group_lifecycle.kind == GROUP_LIFECYCLE_GROUP);
    assert(lv_obj_get_x(s_idle) == 0 && lv_obj_get_x(s_viewport) == 480);
    step(60);
    repaint();
    assert(lv_obj_get_x(s_viewport) > 0 && lv_obj_get_x(s_viewport) < 480);
    assert(lv_obj_get_x(s_content) == 160);
    group_finish();
    assert(!s_group_home && current_id() == 3);

    const int dismisses = s_dismiss_count, activations = s_activate_count;
    char outgoing_title[160], outgoing_body[STATUS_NOTIF_BODY_MAX];
    snprintf(outgoing_title, sizeof(outgoing_title), "%s", label_storage(s_cards[1].title));
    snprintf(outgoing_body, sizeof(outgoing_body), "%s", label_storage(s_cards[1].body));
    pointer_press(600, 44); pointer_release(600, 44);
    repaint();
    assert(s_dismiss_count == dismisses + 1 && !reachable(3));
    assert(s_group_lifecycle.active && s_group_lifecycle.kind == GROUP_LIFECYCLE_CARD);
    assert(s_cards[0].id == 3 && current_id() == 2);
    assert(strcmp(label_storage(s_cards[0].title), outgoing_title) == 0);
    assert(strcmp(label_storage(s_cards[0].body), outgoing_body) == 0);
    assert(lv_obj_get_y(s_cards[0].root) == 0);
    assert(lv_obj_get_y(s_cards[1].root) == GROUP_CARD_PITCH_PX);
    /* Repeated touch during motion cannot dismiss or open a moving card,
     * including a finger held past the animation's completion. */
    pointer_press(600, 44);
    step(80);
    repaint();
    assert(lv_obj_get_y(s_cards[0].root) < 0);
    assert(lv_obj_get_y(s_cards[1].root) > 0 &&
           lv_obj_get_y(s_cards[1].root) < GROUP_CARD_PITCH_PX);
    assert(lv_obj_get_x(s_content) == 160);
    group_finish();
    pointer_release(600, 44);
    assert(s_dismiss_count == dismisses + 1 && s_activate_count == activations);
    assert(current_id() == 2 && reachable(2) && !reachable(3));

    /* The outgoing two-card geometry stays frozen as its successor expands. */
    pointer_press(600, 44); pointer_release(600, 44);
    repaint();
    assert(s_group_lifecycle.active && s_cards[0].id == 2 && current_id() == 1);
    assert(lv_obj_get_height(s_cards[0].root) == 120);
    assert(lv_obj_get_height(s_cards[1].root) == 144);
    lifecycle_capture("lifecycle-dismiss");
    assert(s_deck.count == 1 && current_id() == 1 && !reachable(2));
    pointer_press(600, 44); pointer_release(600, 44);
    repaint();
    assert(s_group_home && s_deck.count == 0 && !reachable(1));
    assert(s_group_lifecycle.active && s_group_lifecycle.kind == GROUP_LIFECYCLE_GROUP);
    assert(lv_obj_get_x(s_idle) == -480 && lv_obj_get_x(s_viewport) == 0);
    assert(s_cards[1].id == 1); /* Frozen pixels, absent from the live collection. */
    lifecycle_capture("lifecycle-last-dismiss");
    assert(s_group_home && !s_cards[1].valid && !s_group_lifecycle.active);

    groups_reset(ids, 3);
    present(1, 2, 1, -1);
    char old_body[STATUS_NOTIF_BODY_MAX];
    snprintf(old_body, sizeof(old_body), "%s", label_storage(s_cards[1].body));
    present_begin(2, 3, 1, -1);
    assert(s_group_lifecycle.active && s_group_lifecycle.kind == GROUP_LIFECYCLE_CARD);
    assert(s_cards[2].id == 2 && current_id() == 3);
    assert(lv_obj_get_y(s_cards[2].root) == 0);
    assert(lv_obj_get_y(s_cards[1].root) == -GROUP_CARD_PITCH_PX);
    assert(strcmp(label_storage(s_cards[2].body), old_body) == 0);
    lifecycle_capture("lifecycle-arrival");
    assert(current_id() == 3 && !s_group_lifecycle.active);

    /* A same-ID replacement and background insertion keep the visible focus. */
    strcpy(s_notifs[2].body, "Updated text without moving the whole card");
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(!s_group_lifecycle.active);
    assert(strcmp(label_storage(s_cards[1].body), s_notifs[2].body) == 0);
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 2 && !s_group_lifecycle.active);
    present_begin(3, 1, 1, -1);
    assert(current_id() == 2 && !s_group_lifecycle.active && s_group_manual);
    /* Desktop removal of a background card is quiet; removal of the visible
     * card uses the same transition as local × without emitting a dismiss. */
    const int without_background[] = {2, 1};
    set_raw_order(without_background, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(!s_group_lifecycle.active && current_id() == 2);
    const int before_desktop_close = s_dismiss_count;
    const int survivor[] = {1};
    set_raw_order(survivor, 1);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_group_lifecycle.active && current_id() == 1 && s_cards[0].id == 2);
    assert(s_dismiss_count == before_desktop_close && !reachable(2));
    group_finish();
    assert(!s_group_home && current_id() == 1);
}

static void lifecycle_concurrent_updates(void)
{
    const int ids[] = {3, 2, 1};
    groups_reset(ids, 3);
    present(1, 1, 1, -1);
    present_begin(2, 2, 1, -1);
    const uint32_t first_generation = s_group_lifecycle.generation;
    present_begin(3, 1, 1, -1);
    present_begin(4, 3, 2, -1);
    assert(s_group_lifecycle.generation == first_generation && current_id() == 2);
    group_finish();
    assert(current_id() == 3 && s_group_generation == 4);
    assert(!s_group_lifecycle.active && s_group_auto);

    /* Removing the incoming destination cancels, without a late callback
     * resurrecting it or changing focus in the following session. */
    present_begin(5, 2, 2, -1);
    assert(s_group_lifecycle.active);
    const int without_destination[] = {3, 1};
    set_raw_order(without_destination, 2);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(!s_group_lifecycle.active && !reachable(2));
    step(250); group_finish();
    assert(!reachable(2) && s_cards[1].id != 2);

    groups_reset(ids, 3);
    present_begin(1, 3, 1, -1);
    s_state.grouped_session++;
    s_state.presentation = (status_presentation_t){0};
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(!s_group_lifecycle.active && s_group_home);
    step(250);
    assert(s_group_home && !s_group_auto);
    present_begin(1, 3, 1, -1);
    s_host_connected = false;
    ui_deck_tick(0);
    assert(!s_group_lifecycle.active && s_group_home && s_view.stale);
    step(250);
    assert(s_group_home && s_view.stale);

    /* Lease expiry returns Home with motion, leaving the card browsable. */
    const int one[] = {1};
    groups_reset(one, 1);
    present(1, 1, 1, 500);
    step(501);
    assert(s_group_home && reachable(1) && s_group_lifecycle.active);
    group_finish();
    group_swipe(450, 90, -130, 0);
    assert(!s_group_home && current_id() == 1 && s_group_manual);
    assert(!s_group_lifecycle.active); /* User swipe already animated once. */
}

int main(int argc, char **argv)
{
    s_artifact_dir = argc > 2 && strcmp(argv[1], "--artifacts") == 0
        ? argv[2] : "/tmp/349-native-groups";
    assert(make_directory_tree(s_artifact_dir));
    char path[PATH_MAX];
    assert(artifact_path(path, sizeof(path), "trace.txt"));
    s_trace_file = fopen(path, "w"); assert(s_trace_file);
    fixture_init(false);
    s_test_clock_valid = true;
    navigation_and_geometry();
    leases_and_manual();
    held_updates_and_removal();
    counts_controls_and_text();
    action_controls_and_capture();
    lifecycle_motion_and_removal();
    lifecycle_concurrent_updates();
    fixture_shutdown();
    fclose(s_trace_file);
    puts("grouped production LVGL: passed");
    return 0;
}
