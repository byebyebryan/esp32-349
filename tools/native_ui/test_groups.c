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
    s_state.history_enabled = false;
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
        .cpu = .18f, .cpu_freq_mhz_valid = true, .cpu_freq_mhz = 3600,
        .mem_valid = true, .mem = .43f,
        .mem_used_bytes_valid = true, .mem_used_bytes = 9019431322.0,
        .network_valid = true, .network = true,
        .rx_bytes_per_s_valid = true, .rx_bytes_per_s = 2400000.0,
        .tx_bytes_per_s_valid = true, .tx_bytes_per_s = 86000.0};
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

static int text_width(const char *value, const lv_font_t *font)
{
    lv_point_t size = {0};
    lv_text_get_size(&size, value, font, 0, 0, LV_COORD_MAX, 0);
    return size.x;
}

static void assert_color(lv_color_t actual, uint32_t expected)
{
    assert(lv_color_eq(actual, lv_color_hex(expected)));
}

static void assert_rail_geometry(void)
{
    assert(lv_obj_get_x(s_rail_clock) == 10 && lv_obj_get_y(s_rail_clock) == 7);
    assert(lv_obj_get_width(s_rail_clock) == 144);
    assert(!lv_obj_has_flag(s_rail_clock, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_get_y(s_rail_footer) == 156);
    const char *const names[] = {"CPU", "MEM", "UP", "DN"};
    for (int i = 0; i < 4; i++) {
        assert(strcmp(label_storage(s_metric_names[i]), names[i]) == 0);
        assert(lv_obj_get_x(s_metric_names[i]) == 12);
        assert(lv_obj_get_y(s_metric_names[i]) == 61 + 24 * i);
        assert(lv_obj_get_width(s_metric_names[i]) == 36);
        const lv_font_t *font = lv_obj_get_style_text_font(s_metric_values[i], 0);
        const bool full_usage = i < 2 && strcmp(label_storage(s_metric_values[i]), "100%") == 0;
        const int baseline_offset = status_text_16.line_height - status_text_16.base_line
            - font->line_height + font->base_line;
        assert(lv_obj_get_x(s_metric_values[i]) == (i < 2 ? 110 : 52));
        assert(lv_obj_get_y(s_metric_values[i]) == 58 + 24 * i + baseline_offset);
        assert(lv_obj_get_width(s_metric_values[i]) == (i < 2 ? 38 : 96));
        assert(text_width(label_storage(s_metric_names[i]), s_meta) <= 36);
        const int width = text_width(label_storage(s_metric_values[i]), font);
        assert(width <= lv_obj_get_width(s_metric_values[i]));
        assert(font == (full_usage ? &lv_font_montserrat_14 : &status_text_16));
        if (i < 2) {
            assert(lv_obj_get_x(s_metric_values[i]) + lv_obj_get_width(s_metric_values[i]) - width >=
                   lv_obj_get_x(s_metric_details[i]) + lv_obj_get_width(s_metric_details[i]));
        }
    }
    assert(text_width("99%", &status_text_16) + text_width(" ", &status_text_16) == 38);
    for (int i = 0; i < 2; i++) {
        assert(lv_obj_get_x(s_metric_details[i]) == 52);
        assert(lv_obj_get_y(s_metric_details[i]) == 58 + 24 * i);
        assert(lv_obj_get_width(s_metric_details[i]) == 58);
        assert(lv_obj_get_style_text_align(s_metric_details[i], 0) == LV_TEXT_ALIGN_RIGHT);
        assert(text_width(label_storage(s_metric_details[i]), &status_text_16) <= 58);
    }
}

static void rail_telemetry_captures(void)
{
    const double rate_boundaries[] = {
        .0001, .049, .05, 9.95, 99.5, 999.5, 9950, 999500,
        9950000, 999500000, 9950000000, 999500000000, 1000000000000.0,
    };
    for (unsigned i = 0; i < sizeof(rate_boundaries) / sizeof(rate_boundaries[0]); i++) {
        char formatted[32];
        format_rate(formatted, sizeof(formatted), true, rate_boundaries[i]);
        assert(text_width(formatted, &status_text_16) <= 96);
    }
    const struct { double mhz; const char *display; } frequencies[] = {
        {0, "0.0G"}, {607.4, "0.6G"}, {800, "0.8G"},
        {999.4, "1.0G"}, {999.5, "1.0G"}, {3600, "3.6G"},
        {9949, "9.9G"}, {9950, "10G"}, {99999, "100G"}, {100000, "100G"},
    };
    for (unsigned i = 0; i < sizeof(frequencies) / sizeof(frequencies[0]); i++) {
        char formatted[32];
        format_frequency(formatted, sizeof(formatted), true, frequencies[i].mhz);
        assert(strcmp(formatted, frequencies[i].display) == 0);
        assert(text_width(formatted, &status_text_16) <= 50);
    }
    for (unsigned unit = 0; unit < 5; unit++) {
        const double amounts[] = {0, 8.4, 99.94, 99.95, 100, 999.5, 1023.49, 1023.5};
        for (unsigned i = 0; i < sizeof(amounts) / sizeof(amounts[0]); i++) {
            char formatted[32];
            format_memory(formatted, sizeof(formatted), true, amounts[i] * pow(1024.0, unit));
            assert(text_width(formatted, &status_text_16) <= 50);
        }
    }
    const int ids[] = {3};
    groups_reset(ids, 1);
    step(1000); /* Acquire RTC after its fixture becomes valid. */
    s_state.dashboard = (status_dashboard_t){
        .valid = true,
        .cpu_valid = true, .cpu = .18f,
        .cpu_freq_mhz_valid = true, .cpu_freq_mhz = 3600,
        .mem_valid = true, .mem = .43f,
        .mem_used_bytes_valid = true, .mem_used_bytes = 9019431322.0,
        .network_valid = true, .network = true,
        .rx_bytes_per_s_valid = true, .rx_bytes_per_s = 2400000.0,
        .tx_bytes_per_s_valid = true, .tx_bytes_per_s = 86000.0,
    };
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert_rail_geometry();
    assert(strcmp(label_storage(s_rail_clock), "14:35") == 0);
    assert(strcmp(label_storage(s_metric_details[0]), "3.6G") == 0);
    assert(strcmp(label_storage(s_metric_details[1]), "8.4G") == 0);
    assert(strcmp(label_storage(s_metric_values[2]), "86 KB/s") == 0);
    assert(strcmp(label_storage(s_metric_values[3]), "2.4 MB/s") == 0);
    assert_color(lv_obj_get_style_text_color(s_rail_clock, LV_PART_MAIN),
                 UI_THEME_RAIL_CLOCK);
    assert_color(lv_obj_get_style_text_color(s_metric_values[0], LV_PART_MAIN),
                 UI_THEME_RAIL_VALUE);
    assert_color(lv_obj_get_style_text_color(s_metric_values[1], LV_PART_MAIN),
                 UI_THEME_RAIL_VALUE);
    assert_color(lv_obj_get_style_text_color(s_metric_details[0], LV_PART_MAIN),
                 UI_THEME_RAIL_VALUE);
    assert_color(lv_obj_get_style_text_color(s_metric_details[1], LV_PART_MAIN),
                 UI_THEME_RAIL_VALUE);
    assert_color(lv_obj_get_style_text_color(s_metric_values[2], LV_PART_MAIN),
                 UI_THEME_RAIL_VALUE);
    assert_color(lv_obj_get_style_text_color(s_metric_values[3], LV_PART_MAIN),
                 UI_THEME_RAIL_VALUE);
    for (int i = 0; i < 4; i++) {
        assert_color(lv_obj_get_style_text_color(s_metric_names[i], LV_PART_MAIN),
                     UI_THEME_RAIL_LABEL);
    }
    assert(capture_frame("telemetry-rail-normal-home"));
    s_state.dashboard.cpu_freq_mhz = 607.4;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(strcmp(label_storage(s_metric_details[0]), "0.6G") == 0);
    assert_rail_geometry();
    s_state.dashboard.cpu_freq_mhz = 3600;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    const float percentages[] = {.01f, .99f, 1.0f};
    for (unsigned i = 0; i < sizeof(percentages) / sizeof(percentages[0]); i++) {
        s_state.dashboard.cpu = s_state.dashboard.mem = percentages[i];
        ui_deck_tick(STATE_DIRTY_DASHBOARD);
        repaint();
        assert_rail_geometry();
        assert(strcmp(label_storage(s_metric_details[0]), "3.6G") == 0);
        assert(strcmp(label_storage(s_metric_details[1]), "8.4G") == 0);
        if (percentages[i] == .99f) assert(capture_frame("telemetry-rail-details-99-percent"));
    }
    assert(capture_frame("telemetry-rail-details-100-percent"));
    s_state.dashboard.mem = .43f;

    const int rtc_reads = s_rtc_reads;
    s_state.dashboard.cpu = .88f;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    assert(s_rtc_reads == rtc_reads);
    assert(strcmp(label_storage(s_metric_values[0]), "88%") == 0);
    const int64_t next_clock_second = (s_now_us / 1000000 + 1) * 1000000;
    step((uint32_t)((next_clock_second - s_now_us) / 1000));
    assert(s_rtc_reads == rtc_reads + 1);
    step(50);
    assert(s_rtc_reads == rtc_reads + 1);
    s_state.dashboard.cpu = .18f;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();

    s_state.dashboard.rx_bytes_per_s = 0;
    s_state.dashboard.tx_bytes_per_s = 0;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(strcmp(label_storage(s_metric_values[2]), "0 B/s") == 0);
    assert(strcmp(label_storage(s_metric_values[3]), "0 B/s") == 0);
    assert(capture_frame("telemetry-rail-zero"));

    s_state.dashboard.rx_bytes_per_s = 1000000000000.0;
    s_state.dashboard.tx_bytes_per_s = 999500.0;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(strcmp(label_storage(s_metric_values[2]), "1.0 MB/s") == 0);
    assert(strcmp(label_storage(s_metric_values[3]), ">999GB/s") == 0);
    assert_rail_geometry();
    assert(capture_frame("telemetry-rail-high"));

    s_state.dashboard.rx_bytes_per_s = 9950.0;
    s_state.dashboard.tx_bytes_per_s = 999500.0;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(strcmp(label_storage(s_metric_values[2]), "1.0 MB/s") == 0);
    assert(strcmp(label_storage(s_metric_values[3]), "10.0 KB/s") == 0);
    assert_rail_geometry();
    assert(capture_frame("telemetry-rail-rounding-boundary"));

    s_state.dashboard.rx_bytes_per_s_valid = false;
    s_state.dashboard.tx_bytes_per_s_valid = false;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(strcmp(label_storage(s_metric_values[2]), "--") == 0);
    assert(strcmp(label_storage(s_metric_values[3]), "--") == 0);
    assert_color(lv_obj_get_style_text_color(s_metric_values[2], LV_PART_MAIN),
                 UI_THEME_RAIL_LABEL);
    assert_color(lv_obj_get_style_text_color(s_metric_values[3], LV_PART_MAIN),
                 UI_THEME_RAIL_LABEL);
    assert(capture_frame("telemetry-rail-unavailable"));
    s_state.dashboard.cpu_freq_mhz_valid = false;
    s_state.dashboard.mem_used_bytes_valid = false;
    s_state.dashboard.cpu_valid = false;
    s_state.dashboard.mem_valid = false;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(strcmp(label_storage(s_metric_details[0]), "--") == 0);
    assert(strcmp(label_storage(s_metric_details[1]), "--") == 0);
    assert(strcmp(label_storage(s_metric_values[0]), "--") == 0);
    assert(strcmp(label_storage(s_metric_values[1]), "--") == 0);
    for (int i = 0; i < 2; i++) {
        assert_color(lv_obj_get_style_text_color(s_metric_details[i], LV_PART_MAIN),
                     UI_THEME_RAIL_LABEL);
        assert_color(lv_obj_get_style_text_color(s_metric_values[i], LV_PART_MAIN),
                     UI_THEME_RAIL_LABEL);
    }
    assert_rail_geometry();
    assert(capture_frame("telemetry-rail-details-unavailable"));
    s_state.dashboard.cpu_freq_mhz_valid = true;
    s_state.dashboard.mem_used_bytes_valid = true;
    s_state.dashboard.cpu_valid = true;
    s_state.dashboard.mem_valid = true;

    s_state.dashboard.rx_bytes_per_s_valid = true;
    s_state.dashboard.tx_bytes_per_s_valid = true;
    s_state.dashboard.rx_bytes_per_s = 2400000.0;
    s_state.dashboard.tx_bytes_per_s = 86000.0;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    group_swipe(450, 90, -130, 0);
    assert(!s_group_home);
    assert_rail_geometry();
    assert(strcmp(label_storage(s_metric_values[2]), "86 KB/s") == 0);
    assert(strcmp(label_storage(s_metric_values[3]), "2.4 MB/s") == 0);
    assert(capture_frame("telemetry-rail-normal-notifications"));

    s_state.dashboard.network = false;
    s_state.dashboard.rx_bytes_per_s_valid = false;
    s_state.dashboard.tx_bytes_per_s_valid = false;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(strcmp(label_storage(s_rail_footer), "Uplink offline") == 0);
    assert(capture_frame("telemetry-rail-offline"));

    s_state.dashboard.network = true;
    s_state.dashboard.rx_bytes_per_s_valid = true;
    s_state.dashboard.tx_bytes_per_s_valid = true;
    s_state.dashboard.rx_bytes_per_s = 0;
    s_state.dashboard.tx_bytes_per_s = 0;
    s_host_connected = false;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    assert(s_view.stale);
    assert(strcmp(label_storage(s_rail_footer), "Readings stale") == 0);
    assert_rail_geometry();
    assert_color(lv_obj_get_style_text_color(s_rail_clock, LV_PART_MAIN),
                 UI_THEME_RAIL_CLOCK);
    for (int i = 0; i < 4; i++) {
        assert_color(lv_obj_get_style_text_color(s_metric_values[i], LV_PART_MAIN),
                     UI_THEME_RAIL_LABEL);
    }
    for (int i = 0; i < 2; i++) {
        assert_color(lv_obj_get_style_text_color(s_metric_details[i], LV_PART_MAIN),
                     UI_THEME_RAIL_LABEL);
    }
    assert(capture_frame("telemetry-rail-stale"));
    s_host_connected = true;
    ui_deck_tick(STATE_DIRTY_DASHBOARD);
    repaint();
    for (int i = 0; i < 4; i++) {
        assert_color(lv_obj_get_style_text_color(s_metric_values[i], LV_PART_MAIN),
                     UI_THEME_RAIL_VALUE);
    }
    for (int i = 0; i < 2; i++) {
        assert_color(lv_obj_get_style_text_color(s_metric_details[i], LV_PART_MAIN),
                     UI_THEME_RAIL_VALUE);
    }
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
    assert_color(lv_obj_get_style_text_color(s_cards[1].app, LV_PART_MAIN),
                 UI_THEME_TEXT_SAGE);
    assert_color(lv_obj_get_style_text_color(s_cards[1].body, LV_PART_MAIN),
                 UI_THEME_TEXT_BODY);
    for (int i = 0; i < 3; i++) {
        assert(lv_obj_has_flag(s_cards[i].age, LV_OBJ_FLAG_HIDDEN));
    }
    assert_color(lv_obj_get_style_bg_color(s_cards[1].open,
                                           LV_PART_MAIN | LV_STATE_DEFAULT),
                 UI_THEME_OPEN_FILL);
    assert_color(lv_obj_get_style_border_color(s_cards[1].open,
                                               LV_PART_MAIN | LV_STATE_DEFAULT),
                 UI_THEME_OPEN_BORDER);
    assert_color(lv_obj_get_style_bg_color(s_cards[1].open,
                                           LV_PART_MAIN | LV_STATE_PRESSED),
                 UI_THEME_OPEN_PRESSED);
    assert_color(lv_obj_get_style_line_color(lv_obj_get_child(s_cards[1].open_icon, 0),
                                             LV_PART_MAIN),
                 UI_THEME_TEXT_SAGE);
    assert_color(lv_obj_get_style_bg_color(s_cards[1].dismiss, LV_PART_MAIN),
                 UI_THEME_BUTTON);
    assert_color(lv_obj_get_style_bg_color(s_cards[1].dismiss,
                                           LV_PART_MAIN | LV_STATE_PRESSED),
                 UI_THEME_BUTTON_PRESSED);
    assert(!lv_obj_has_flag(s_cards[1].open_icon, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_cards[1].open_label, LV_OBJ_FLAG_HIDDEN));
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
    pointer_press(528, 44);
    assert(lv_obj_has_state(s_cards[1].open, LV_STATE_PRESSED));
    assert(capture_frame("actions-ready-pressed"));
    pointer_release(528, 44);
    assert(s_activate_count == activations + 1);
    assert(s_last_activate_id == 2 && s_last_activate_revision == 5);
    assert(s_group_manual && !s_group_auto);
    assert(s_state.action_pending && s_state.action_pending_id == 2);
    assert(strcmp(lv_label_get_text(s_cards[1].open_label), "…") == 0);
    assert(lv_obj_has_flag(s_cards[1].open_icon, LV_OBJ_FLAG_HIDDEN));
    assert(!lv_obj_has_flag(s_cards[1].open_label, LV_OBJ_FLAG_HIDDEN));
    assert_color(lv_obj_get_style_bg_color(s_cards[1].open, LV_PART_MAIN),
                 UI_THEME_OPEN_FILL);
    assert_color(lv_obj_get_style_border_color(s_cards[1].open, LV_PART_MAIN),
                 UI_THEME_OPEN_BORDER);
    assert_color(lv_obj_get_style_text_color(s_cards[1].open_label, LV_PART_MAIN),
                 UI_THEME_TEXT_SAGE);
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
    assert(!lv_obj_has_flag(s_cards[1].open_icon, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_cards[1].open_label, LV_OBJ_FLAG_HIDDEN));
    assert_color(lv_obj_get_style_bg_color(s_cards[1].open, LV_PART_MAIN),
                 UI_THEME_OPEN_DISABLED_FILL);
    assert_color(lv_obj_get_style_border_color(s_cards[1].open, LV_PART_MAIN),
                 UI_THEME_OPEN_DISABLED_BORDER);
    assert_color(lv_obj_get_style_bg_color(s_cards[1].open,
                                           LV_PART_MAIN | LV_STATE_PRESSED),
                 UI_THEME_OPEN_DISABLED_FILL);
    assert_color(lv_obj_get_style_line_color(lv_obj_get_child(s_cards[1].open_icon, 0),
                                             LV_PART_MAIN),
                 UI_THEME_OPEN_DISABLED_GLYPH);
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

    /* A touch beyond the padded control strip never acquires an action. */
    const int before_gap = s_activate_count;
    const int current = current_id();
    pointer_press(487, 44); pointer_release(487, 44);
    assert(s_activate_count == before_gap && current_id() == current);
    assert(!s_group_home);

    /* A button drag cancels, even when it crosses into × or returns. */
    pointer_press(528, 44); pointer_move(576, 44, 100);
    pointer_move(528, 44, 100); pointer_release(528, 44);
    assert(s_activate_count == before_gap);
    const int dismisses = s_dismiss_count;
    pointer_press(528, 44); pointer_move(510, 44, 100);
    pointer_release(528, 44);
    assert(s_activate_count == before_gap + 1 && s_dismiss_count == dismisses);
    s_state.action_pending = false;
    pointer_press(528, 44); pointer_move(528, 60, 100);
    pointer_release(528, 60);
    assert(s_activate_count == before_gap + 2);
    s_state.action_pending = false;
    /* Finger roll just beyond an edge keeps the originally pressed action. */
    pointer_press(558, 44); pointer_move(565, 47, 100);
    pointer_release(565, 47);
    assert(s_activate_count == before_gap + 3 && s_dismiss_count == dismisses);
    s_state.action_pending = false;
    const int after_roll = s_activate_count;
    pointer_press(558, 44); pointer_move(568, 44, 100);
    pointer_move(558, 44, 100); pointer_release(558, 44);
    assert(s_activate_count == after_roll && s_dismiss_count == dismisses);
    pointer_press(528, 44); pointer_move(552, 44, 100);
    assert(!s_group_input.control_cancelled && s_group_input.axis == GROUP_AXIS_NONE);
    assert(lv_obj_has_state(s_cards[1].open, LV_STATE_PRESSED));
    pointer_release(552, 44);
    assert(s_activate_count == after_roll + 1);
    s_state.action_pending = false;
    const int after_inbounds = s_activate_count;
    pointer_press(528, 44);
    grouped_cancel(); /* The production INDEV_RESET and PRESS_LOST path. */
    pointer_release(528, 44);
    assert(s_activate_count == after_inbounds && s_dismiss_count == dismisses);

    /* Revision change and removal while held cancel permanently. */
    pointer_press(528, 44);
    set_open(2, 8, true);
    pointer_release(528, 44);
    assert(s_activate_count == after_inbounds);
    pointer_press(528, 44);
    int only_one[] = {1};
    set_raw_order(only_one, 1);
    ui_deck_tick(STATE_DIRTY_NOTIF);
    pointer_release(528, 44);
    assert(s_activate_count == after_inbounds);

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

static void action_control_initial_targets(void)
{
    const int ids[] = {2, 1};
    for (int history = 0; history < 2; history++) {
        groups_reset(ids, 2);
        s_state.actions_enabled = true;
        if (history) {
            s_state.history_enabled = true;
            for (int i = 0; i < s_state.notif_count; i++) {
                s_state.notifs[i].history_revision = 1;
                s_state.notifs[i].history_updated_us = s_now_us;
                s_state.notifs[i].history_deadline_us = s_now_us + 1800000000;
            }
            ui_deck_tick(STATE_DIRTY_NOTIF);
            group_finish();
        } else present(1, 2, 1, -1);
        set_open(2, 7, true);
        lv_area_t bounds;
        lv_obj_get_coords(s_cards[1].open, &bounds);
        const int y = bounds.y1 + 24;
        const int points[][2] = {
            {bounds.x1 - 6, y},
            {history ? bounds.x2 + 6 : bounds.x1 + 24,
             history ? y : bounds.y2 + 6},
            {history ? bounds.x1 + 24 : bounds.x2 + 4,
             history ? bounds.y2 + 4 : y}, /* Open half of the shared gap. */
        };
        for (int i = 0; i < 3; i++) {
            const int activations = s_activate_count;
            pointer_press(points[i][0], points[i][1]);
            assert(s_group_input.control == GROUP_CONTROL_OPEN);
            assert(lv_obj_has_state(s_cards[1].open, LV_STATE_PRESSED));
            assert(!lv_obj_has_state(s_cards[1].dismiss, LV_STATE_PRESSED));
            pointer_release(points[i][0], points[i][1]);
            assert(s_activate_count == activations + 1 && current_id() == 2);
            assert(!lv_obj_has_state(s_cards[1].open, LV_STATE_PRESSED));
            s_state.action_pending = false;
            set_open(2, 7, true);
        }
        /* The other half of the gap belongs to × before its visible edge. */
        const int dismisses = s_dismiss_count;
        const int close_x = history ? bounds.x1 + 24 : bounds.x2 + 5;
        const int close_y = history ? bounds.y2 + 5 : y;
        pointer_press(close_x, close_y);
        assert(s_group_input.control == GROUP_CONTROL_DISMISS);
        assert(lv_obj_has_state(s_cards[1].dismiss, LV_STATE_PRESSED));
        assert(!lv_obj_has_state(s_cards[1].open, LV_STATE_PRESSED));
        pointer_release(close_x, close_y);
        assert(s_dismiss_count == dismisses + 1);
        group_finish();
        assert(current_id() == 1);

        /* Screen-edge and lower-margin taps also reach the close control. */
        lv_obj_get_coords(s_cards[1].dismiss, &bounds);
        pointer_press(bounds.x2 + 6, bounds.y2 + 6);
        assert(s_group_input.control == GROUP_CONTROL_DISMISS);
        assert(lv_obj_has_state(s_cards[1].dismiss, LV_STATE_PRESSED));
        pointer_release(bounds.x2 + 6, bounds.y2 + 6);
        assert(s_dismiss_count == dismisses + 2);
        group_finish();
        assert(s_deck.count == 0);
    }
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
    for (int i = 0; i < 3; i++) {
        assert(!lv_obj_has_state(s_cards[i].open, LV_STATE_PRESSED));
        assert(!lv_obj_has_state(s_cards[i].dismiss, LV_STATE_PRESSED));
    }
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

static void history_layout_and_navigation(void)
{
    const int ids[] = {3, 2, 1};
    groups_reset(ids, 3);
    s_state.history_enabled = true;
    s_state.actions_enabled = true;
    for (int i = 0; i < s_state.notif_count; i++) {
        status_notif_t *n = &s_state.notifs[i];
        n->history_revision = i + 1;
        n->history_updated_us = s_now_us - 480000000;
        n->history_deadline_us = s_now_us + 1800000000;
        n->open_revision = 1;
        n->open_ready = n->id != 2;
        if (n->id == 3) {
            n->urgency = 2;
            /* Use the widest ASCII sender that fits the protocol field. */
            memset(n->app, 'W', sizeof(n->app) - 1);
            n->app[sizeof(n->app) - 1] = '\0';
        } else {
            snprintf(n->app, sizeof(n->app), "Claude Code");
        }
        snprintf(n->summary, sizeof(n->summary), "Review complete — 東京");
        snprintf(n->body, sizeof(n->body),
            "We've reviewed the notification history and its retention policy. "
            "The smaller body font shows more of the original message, including "
            "we’ve, 東京 が, arrows → and common symbols ✓. This is longer than "
            "the former 159-byte body buffer, so the third line should carry useful content.");
    }
    ui_deck_tick(STATE_DIRTY_NOTIF);
    group_finish();
    assert(s_history_mode && !s_group_home);
    assert_rail_geometry();
    assert(lv_obj_has_flag(s_idle, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_group_cue, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_get_y(s_viewport) == 0 && lv_obj_get_height(s_viewport) == 172);
    assert(lv_obj_get_height(s_cards[1].root) == 140);
    assert(lv_obj_get_width(s_cards[1].open) == 64);
    assert(lv_obj_get_width(s_cards[1].dismiss) == 64);
    assert(lv_obj_get_height(s_cards[1].open) == 66);
    assert(lv_obj_get_y(s_cards[1].dismiss) == 74);
    assert(lv_obj_get_height(s_cards[1].dismiss) == 66);
    assert(lv_obj_get_x(s_cards[1].open) == 400);
    assert(lv_obj_get_y(s_cards[1].open) == 0);
    assert(lv_obj_get_x(s_cards[1].dismiss) == 400);
    assert(lv_obj_get_height(s_cards[1].body) == 66);
    assert(lv_obj_get_y(s_cards[1].position) == 124);
    assert(lv_obj_get_width(s_cards[1].body) == 376);
    assert(lv_obj_get_x(s_cards[1].body) + lv_obj_get_width(s_cards[1].body)
        <= lv_obj_get_x(s_cards[1].open) - GROUP_CONTROL_MARGIN_PX);
    assert(lv_obj_get_style_text_font(s_cards[1].body, 0) == &status_text_16);
    assert(strlen(label_storage(s_cards[1].app)) ==
           sizeof(s_state.notifs[0].app) - 1);
    assert(strspn(label_storage(s_cards[1].app), "W") ==
           sizeof(s_state.notifs[0].app) - 1);
    assert(strcmp(label_storage(s_cards[1].age), "8m ago") == 0);
    assert(!lv_obj_has_flag(s_cards[1].age, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_cards[0].age, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_cards[2].age, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_get_width(s_cards[1].app) == 296);
    assert(text_width(label_storage(s_cards[1].app), s_meta) >
           lv_obj_get_width(s_cards[1].app));
    assert(lv_obj_get_x(s_cards[1].age) == 316);
    assert(lv_obj_get_width(s_cards[1].age) == 72);
    assert(lv_obj_get_style_text_align(s_cards[1].age, LV_PART_MAIN) == LV_TEXT_ALIGN_RIGHT);
    assert(lv_obj_get_x(s_cards[1].app) + lv_obj_get_width(s_cards[1].app) <=
           lv_obj_get_x(s_cards[1].age) - HISTORY_AGE_GAP);
    assert_color(lv_obj_get_style_text_color(s_cards[1].app, LV_PART_MAIN),
                 UI_THEME_CRITICAL);
    assert_color(lv_obj_get_style_text_color(s_cards[1].age, LV_PART_MAIN),
                 UI_THEME_TEXT_SECONDARY);
    assert_color(lv_obj_get_style_bg_color(s_cards[1].accent, LV_PART_MAIN),
                 UI_THEME_CRITICAL);
    assert(capture_frame("history-multiple-long-critical-neutral-age"));

    const int newest = current_id();
    group_swipe(350, 90, -130, 0);
    assert(current_id() == newest && !s_group_home);
    group_swipe(350, 110, 0, -65);
    assert(current_id() == 2 && s_group_manual);
    assert(!lv_obj_has_flag(s_cards[1].age, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_cards[0].age, LV_OBJ_FLAG_HIDDEN));
    assert(lv_obj_has_flag(s_cards[2].age, LV_OBJ_FLAG_HIDDEN));
    assert_color(lv_obj_get_style_text_color(s_cards[1].app, LV_PART_MAIN),
                 UI_THEME_TEXT_SAGE);
    assert_color(lv_obj_get_style_text_color(s_cards[1].age, LV_PART_MAIN),
                 UI_THEME_TEXT_SECONDARY);
    assert(!s_view.open_enabled);
    assert(capture_frame("history-unavailable-open"));
    present(1, 3, 1, 10000);
    assert(current_id() == 2); /* Browsing owns focus. */
    present(2, 3, 2, 10000);
    assert(current_id() == 2 && s_group_manual); /* Critical arrivals also wait. */
    step(30001);
    assert(!s_group_manual && !s_group_home);
    present(3, 3, 1, 1000);
    assert(current_id() == 3);
    step(1001);
    assert(!s_group_home && current_id() == 3); /* No return to a clock. */

    present(4, 3, 2, -1);
    for (int i = 0; i < s_state.notif_count; i++) {
        if (s_state.notifs[i].id == 3) s_state.notifs[i].history_deadline_us = s_now_us + 1000;
    }
    step(2); group_finish();
    assert(current_id() == 2 && !s_group_auto);
    assert(!s_state.grouped_presenting); /* No expired presentation in readback. */

    s_host_connected = false;
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_view.stale && !s_group_home && !s_view.open_enabled);
    assert(!lv_obj_has_flag(s_viewport, LV_OBJ_FLAG_HIDDEN));
    assert(capture_frame("history-disconnected"));
    for (int i = 0; i < s_state.notif_count; i++)
        s_state.notifs[i].history_deadline_us = s_now_us + 1000;
    step(2);
    group_finish();
    assert(s_state.notif_count == 0 && !s_group_home);
    assert(strcmp(label_storage(s_group_empty), "No recent notifications") == 0);
    assert(!lv_obj_has_flag(s_group_empty, LV_OBJ_FLAG_HIDDEN));
    assert(capture_frame("history-empty"));
    s_host_connected = true;

    const int single[] = {4};
    set_raw_order(single, 1);
    s_state.notifs[0].history_revision = 4;
    s_state.notifs[0].history_updated_us = s_now_us;
    s_state.notifs[0].history_deadline_us = s_now_us + 1800000000;
    s_state.notifs[0].open_revision = 1;
    snprintf(s_state.notifs[0].app, sizeof(s_state.notifs[0].app), "Codex");
    snprintf(s_state.notifs[0].summary, sizeof(s_state.notifs[0].summary), "One recent notification");
    snprintf(s_state.notifs[0].body, sizeof(s_state.notifs[0].body),
        "We've completed the review and kept the smaller font's full repertoire: "
        "we’ve, 東京 が, arrows → and check marks ✓. A single card keeps the same "
        "height and action targets as a stack, preserving the clock "
        "and telemetry on the left. Longer messages still end with a visible ellipsis.");
    ui_deck_tick(STATE_DIRTY_NOTIF);
    assert(s_group_lifecycle.active);
    step(75); repaint();
    assert(capture_frame("history-arrival-motion"));
    group_finish();
    assert(lv_obj_get_height(s_cards[1].root) == 140);
    assert(lv_obj_get_width(s_cards[1].open) == 64);
    assert(lv_obj_get_width(s_cards[1].dismiss) == 64);
    assert(lv_obj_get_height(s_cards[1].open) == 66);
    assert(lv_obj_get_height(s_cards[1].dismiss) == 66);
    assert(lv_obj_get_x(s_cards[1].open) == 400);
    assert(lv_obj_get_y(s_cards[1].open) == 0);
    assert(lv_obj_get_x(s_cards[1].dismiss) == 400);
    assert(lv_obj_get_y(s_cards[1].dismiss) == 74);
    assert(lv_obj_get_width(s_cards[1].body) == 376);
    assert(lv_obj_get_height(s_cards[1].body) == 66);
    assert(lv_obj_get_y(s_cards[1].position) == 124);
    assert(capture_frame("history-single"));
    s_test_clock_valid = false;
    step(1000); repaint();
    assert(strcmp(label_storage(s_rail_clock), "--:--") == 0);
    assert_color(lv_obj_get_style_text_color(s_rail_clock, LV_PART_MAIN),
                 UI_THEME_RAIL_LABEL);
    assert(capture_frame("history-clock-unavailable"));
    s_test_clock_valid = true;
    step(1000); repaint();
    assert(strcmp(label_storage(s_rail_clock), "14:35") == 0);
    assert_color(lv_obj_get_style_text_color(s_rail_clock, LV_PART_MAIN),
                 UI_THEME_RAIL_CLOCK);
    const int dismisses = s_dismiss_count;
    pointer_press(590, 110);
    pointer_move(614, 130, 100); /* >24 px, still inside the close button. */
    assert(s_group_input.axis == GROUP_AXIS_NONE && !s_group_input.control_cancelled);
    assert(lv_obj_has_state(s_cards[1].dismiss, LV_STATE_PRESSED));
    assert(capture_frame("history-close-pressed"));
    pointer_release(614, 130);
    assert(s_dismiss_count == dismisses + 1);
    assert(s_group_lifecycle.active);
    step(75); repaint();
    assert(capture_frame("history-dismiss-motion"));
    group_finish();
    assert(s_deck.count == 0 && !s_group_home);
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
    assert(s_label_count <= 40);
    s_test_clock_valid = true;
    navigation_and_geometry();
    rail_telemetry_captures();
    leases_and_manual();
    held_updates_and_removal();
    counts_controls_and_text();
    action_controls_and_capture();
    action_control_initial_targets();
    lifecycle_motion_and_removal();
    lifecycle_concurrent_updates();
    history_layout_and_navigation();
    fixture_shutdown();
    fclose(s_trace_file);
    puts("grouped production LVGL: passed");
    return 0;
}
