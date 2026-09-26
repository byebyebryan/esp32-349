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
    for (int i = 0; i < 16 && s_group_input.motion.state == DECK_INPUT_SETTLING; i++) step(20);
    assert(s_group_input.motion.state != DECK_INPUT_SETTLING);
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

static void present(int generation, int id, int urgency, int duration_ms)
{
    s_state.presentation = (status_presentation_t){.generation = generation, .id = id,
        .urgency = urgency, .active = true, .persistent = duration_ms < 0,
        .deadline_us = s_now_us + (int64_t)duration_ms * 1000};
    ui_deck_tick(STATE_DIRTY_NOTIF);
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
    fixture_shutdown();
    fclose(s_trace_file);
    puts("grouped production LVGL: passed");
    return 0;
}
