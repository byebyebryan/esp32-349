#include "deck_input.h"

#include <string.h>

static deck_input_action_t no_action(void)
{
    deck_input_action_t action = {0};
    action.kind = DECK_INPUT_ACTION_NONE;
    return action;
}

static void next_generation(deck_input_t *input)
{
    input->generation++;
    if (input->generation == 0) {
        input->generation = 1;
    }
}

static deck_input_action_t make_action(deck_input_action_kind_t kind,
                                      bool has_target, int target_id,
                                      bool commit_target, int offset_px,
                                      uint32_t generation)
{
    deck_input_action_t action = {0};
    action.kind = kind;
    action.has_target = has_target;
    action.target_id = target_id;
    action.commit_target = commit_target;
    action.offset_px = offset_px;
    action.generation = generation;
    return action;
}

static bool selected_neighbor_valid(const deck_input_t *input, int direction)
{
    if (direction > 0) {
        return input->has_previous;
    }
    if (direction < 0) {
        return input->has_next;
    }
    return false;
}

static int selected_neighbor_id(const deck_input_t *input, int direction)
{
    return direction > 0 ? input->previous_id : input->next_id;
}

static deck_input_action_t start_settle(deck_input_t *input,
                                        deck_input_action_kind_t kind,
                                        int target_id, bool commit_target,
                                        int direction, int offset_px)
{
    next_generation(input);
    input->state = DECK_INPUT_SETTLING;
    input->settle_commit = commit_target;
    input->has_settle_target = true;
    input->settle_target_id = target_id;
    input->settle_direction = direction;
    input->offset_px = offset_px;
    return make_action(kind, true, target_id, commit_target, offset_px,
                       input->generation);
}

static void clear_capture(deck_input_t *input)
{
    input->has_source = false;
    input->has_previous = false;
    input->has_next = false;
    input->captured_previous = false;
    input->captured_next = false;
    input->settle_commit = false;
    input->has_settle_target = false;
    input->settle_direction = 0;
    input->offset_px = 0;
    input->motion_count = 0;
}

static void finish_to_ignored_or_idle(deck_input_t *input)
{
    if (input->pointer_down) {
        input->state = DECK_INPUT_IGNORED;
    } else {
        input->state = DECK_INPUT_IDLE;
        clear_capture(input);
    }
}

static void record_motion(deck_input_t *input, int x, int y, int64_t now_us)
{
    if (input->motion_count > 0) {
        const int latest = input->motion_count - 1;
        if (input->motion_x[latest] == x && input->motion_y[latest] == y) {
            /* Repeated touch coordinates do not refresh velocity freshness. */
            return;
        }
        if (now_us <= input->motion_us[latest]) {
            /* A non-monotonic sample cannot establish a meaningful velocity. */
            input->motion_count = 1;
            input->motion_x[0] = x;
            input->motion_y[0] = y;
            input->motion_us[0] = now_us;
            input->last_motion_us = now_us;
            return;
        }
    }

    if (input->motion_count == DECK_INPUT_MOTION_SAMPLES) {
        memmove(input->motion_x, input->motion_x + 1,
                sizeof(input->motion_x[0]) * (DECK_INPUT_MOTION_SAMPLES - 1));
        memmove(input->motion_y, input->motion_y + 1,
                sizeof(input->motion_y[0]) * (DECK_INPUT_MOTION_SAMPLES - 1));
        memmove(input->motion_us, input->motion_us + 1,
                sizeof(input->motion_us[0]) * (DECK_INPUT_MOTION_SAMPLES - 1));
        input->motion_count--;
    }
    input->motion_x[input->motion_count] = x;
    input->motion_y[input->motion_count] = y;
    input->motion_us[input->motion_count] = now_us;
    input->motion_count++;
    input->last_motion_us = now_us;
}

static int64_t abs_i64(int64_t value)
{
    return value < 0 ? -value : value;
}

static bool motion_reached(const deck_input_t *input, int64_t threshold_px)
{
    const int64_t dx = (int64_t)input->last_x - input->start_x;
    const int64_t dy = (int64_t)input->last_y - input->start_y;
    const int64_t ax = abs_i64(dx);
    const int64_t ay = abs_i64(dy);
    if (ax >= threshold_px || ay >= threshold_px) {
        return true;
    }
    return ax * ax + ay * ay >= threshold_px * threshold_px;
}

static bool recent_velocity_matches(const deck_input_t *input,
                                    int64_t now_us, int direction)
{
    if (direction == 0 || input->motion_count < 2 ||
        now_us < input->last_motion_us ||
        now_us - input->last_motion_us > DECK_INPUT_RECENT_MOTION_US) {
        return false;
    }

    const int latest = input->motion_count - 1;
    int first = latest;
    while (first > 0 &&
           input->motion_us[latest] - input->motion_us[first - 1] <=
               DECK_INPUT_RECENT_MOTION_US) {
        first--;
    }
    const int64_t elapsed_us = input->motion_us[latest] - input->motion_us[first];
    if (elapsed_us <= 0) {
        return false;
    }
    const int64_t delta_x = (int64_t)input->motion_x[latest] -
                            input->motion_x[first];
    if ((direction > 0 && delta_x <= 0) || (direction < 0 && delta_x >= 0)) {
        return false;
    }
    return abs_i64(delta_x) * INT64_C(1000000) >=
           (int64_t)DECK_INPUT_FLICK_VELOCITY_MILLI_PX_PER_MS * elapsed_us;
}

static int drag_offset(const deck_input_t *input, int x)
{
    const int64_t dx = (int64_t)x - input->start_x;
    if (dx > 0) {
        return input->has_previous
                   ? (dx > input->pitch_px ? input->pitch_px : (int)dx)
                   : 0;
    }
    if (dx < 0) {
        return input->has_next
                   ? (dx < -input->pitch_px ? -input->pitch_px : (int)dx)
                   : 0;
    }
    return 0;
}

void deck_input_init(deck_input_t *input)
{
    if (input == NULL) {
        return;
    }
    memset(input, 0, sizeof(*input));
    input->state = DECK_INPUT_IDLE;
    input->generation = 1;
    deck_input_geometry(input, DECK_INPUT_CARD_PITCH_PX, DECK_INPUT_COMMIT_PX,
                        DECK_INPUT_FLICK_TRAVEL_PX);
}

void deck_input_geometry(deck_input_t *input, int pitch, int commit, int flick)
{
    if (input && pitch > 0 && commit > 0 && flick > 0) {
        input->pitch_px = pitch;
        input->commit_px = commit;
        input->flick_travel_px = flick;
    }
}

bool deck_input_press(deck_input_t *input, int x, int y, int64_t now_us,
                      int source_id, int previous_id, int next_id, int count,
                      bool dismiss_hit, bool peek_hit)
{
    if (input == NULL || input->state != DECK_INPUT_IDLE) {
        return false;
    }

    input->pointer_down = true;
    input->has_source = count > 0;
    input->source_id = source_id;
    input->captured_previous = count > 1;
    input->has_previous = input->captured_previous;
    input->previous_id = previous_id;
    input->captured_next = count > 1;
    input->has_next = input->captured_next;
    input->next_id = next_id;
    input->start_x = x;
    input->start_y = y;
    input->last_x = x;
    input->last_y = y;
    input->offset_px = 0;
    input->settle_commit = false;
    input->has_settle_target = false;
    input->settle_direction = 0;
    input->motion_count = 1;
    input->motion_x[0] = x;
    input->motion_y[0] = y;
    input->motion_us[0] = now_us;
    input->last_motion_us = now_us;

    if (x < DECK_INPUT_RAIL_WIDTH_PX || !input->has_source) {
        input->state = DECK_INPUT_IGNORED;
    } else if (dismiss_hit) {
        input->state = DECK_INPUT_BUTTON_DISMISS;
    } else if (peek_hit) {
        input->state = DECK_INPUT_BUTTON_PEEK;
    } else {
        input->state = DECK_INPUT_PRESSED;
    }
    return true;
}

int deck_input_move(deck_input_t *input, int x, int y, int64_t now_us)
{
    if (input == NULL || !input->pointer_down) {
        return 0;
    }

    if (input->state == DECK_INPUT_PRESSED ||
        input->state == DECK_INPUT_DRAGGING ||
        input->state == DECK_INPUT_BUTTON_DISMISS ||
        input->state == DECK_INPUT_BUTTON_OPEN ||
        input->state == DECK_INPUT_BUTTON_PEEK) {
        record_motion(input, x, y, now_us);
    }
    input->last_x = x;
    input->last_y = y;

    if (input->state == DECK_INPUT_BUTTON_DISMISS ||
        input->state == DECK_INPUT_BUTTON_OPEN) {
        if (motion_reached(input, DECK_INPUT_SLOP_PX)) {
            input->state = DECK_INPUT_IGNORED;
        }
        return 0;
    }

    if (input->state == DECK_INPUT_PRESSED ||
        input->state == DECK_INPUT_BUTTON_PEEK) {
        const int64_t dx = (int64_t)x - input->start_x;
        const int64_t dy = (int64_t)y - input->start_y;
        const int64_t ax = abs_i64(dx);
        const int64_t ay = abs_i64(dy);
        if (ax >= DECK_INPUT_SLOP_PX && ax * 2 >= ay * 3) {
            input->state = DECK_INPUT_DRAGGING;
            input->offset_px = drag_offset(input, x);
        } else if (ay >= DECK_INPUT_SLOP_PX && ay * 2 >= ax * 3) {
            input->state = DECK_INPUT_IGNORED;
            input->offset_px = 0;
        } else if (motion_reached(input, DECK_INPUT_AMBIGUOUS_PX)) {
            input->state = DECK_INPUT_IGNORED;
            input->offset_px = 0;
        }
        return input->offset_px;
    }

    if (input->state == DECK_INPUT_DRAGGING) {
        input->offset_px = drag_offset(input, x);
        return input->offset_px;
    }
    return input->offset_px;
}

deck_input_action_t deck_input_release(deck_input_t *input, int64_t now_us)
{
    (void)now_us;
    if (input == NULL || !input->pointer_down) {
        return no_action();
    }

    input->pointer_down = false;
    if (input->state == DECK_INPUT_BUTTON_DISMISS) {
        const deck_input_action_t action = input->has_source
            ? make_action(DECK_INPUT_ACTION_DISMISS, true, input->source_id,
                          false, 0, input->generation)
            : no_action();
        input->state = DECK_INPUT_IDLE;
        clear_capture(input);
        return action;
    }
    if (input->state == DECK_INPUT_BUTTON_OPEN) {
        const deck_input_action_t action = input->has_source
            ? make_action(DECK_INPUT_ACTION_OPEN, true, input->source_id,
                          false, 0, input->generation)
            : no_action();
        input->state = DECK_INPUT_IDLE;
        clear_capture(input);
        return action;
    }
    if (input->state == DECK_INPUT_BUTTON_PEEK) {
        if (input->has_source && input->has_next) {
            return start_settle(input, DECK_INPUT_ACTION_NEXT_TAP,
                                input->next_id, true, -1,
                                -input->pitch_px);
        }
        input->state = DECK_INPUT_IDLE;
        clear_capture(input);
        return no_action();
    }
    if (input->state == DECK_INPUT_SETTLING) {
        return no_action();
    }
    if (input->state != DECK_INPUT_DRAGGING) {
        input->state = DECK_INPUT_IDLE;
        clear_capture(input);
        return no_action();
    }

    const int direction = input->offset_px > 0 ? 1 :
                          input->offset_px < 0 ? -1 : 0;
    const int64_t travel = abs_i64(input->offset_px);
    const bool can_commit = direction != 0 &&
                            selected_neighbor_valid(input, direction);
    const bool commit = can_commit &&
        (travel >= input->commit_px ||
         (travel >= input->flick_travel_px &&
          recent_velocity_matches(input, now_us, direction)));
    const int target_id = commit ? selected_neighbor_id(input, direction)
                                 : input->source_id;
    const int target_offset = commit ? direction * input->pitch_px : 0;
    return start_settle(input, DECK_INPUT_ACTION_SNAP, target_id, commit,
                        commit ? direction : 0, target_offset);
}

deck_input_action_t deck_input_validate(deck_input_t *input,
                                        bool source_valid,
                                        bool previous_valid,
                                        bool next_valid)
{
    if (input == NULL || input->state == DECK_INPUT_IDLE ||
        input->state == DECK_INPUT_IGNORED) {
        return no_action();
    }

    if (!input->has_source || !source_valid) {
        next_generation(input);
        input->offset_px = 0;
        input->settle_commit = false;
        input->has_settle_target = false;
        input->has_source = false;
        input->has_previous = false;
        input->has_next = false;
        finish_to_ignored_or_idle(input);
        return no_action();
    }

    input->has_previous = input->captured_previous && previous_valid;
    input->has_next = input->captured_next && next_valid;

    int direction = 0;
    if (input->state == DECK_INPUT_DRAGGING) {
        direction = input->offset_px > 0 ? 1 :
                    input->offset_px < 0 ? -1 : 0;
    } else if (input->state == DECK_INPUT_SETTLING && input->settle_commit) {
        direction = input->settle_direction;
    } else if (input->state == DECK_INPUT_BUTTON_PEEK) {
        direction = -1;
    }

    if (direction != 0 && !selected_neighbor_valid(input, direction)) {
        if (input->state == DECK_INPUT_BUTTON_PEEK) {
            input->state = DECK_INPUT_IGNORED;
            return no_action();
        }
        input->offset_px = 0;
        return start_settle(input, DECK_INPUT_ACTION_SNAP,
                            input->source_id, false, 0, 0);
    }
    return no_action();
}

deck_input_action_t deck_input_complete(deck_input_t *input,
                                        uint32_t generation,
                                        bool source_valid,
                                        bool previous_valid,
                                        bool next_valid)
{
    if (input == NULL || input->state != DECK_INPUT_SETTLING ||
        generation != input->generation) {
        return no_action();
    }

    deck_input_action_t validation = deck_input_validate(
        input, source_valid, previous_valid, next_valid);
    if (validation.kind != DECK_INPUT_ACTION_NONE ||
        input->state != DECK_INPUT_SETTLING || generation != input->generation) {
        return validation;
    }

    const deck_input_action_t completed = make_action(
        DECK_INPUT_ACTION_SETTLED, input->has_settle_target,
        input->settle_target_id, input->settle_commit, input->offset_px,
        generation);
    finish_to_ignored_or_idle(input);
    next_generation(input);
    return completed;
}

void deck_input_cancel(deck_input_t *input)
{
    if (input == NULL || input->state == DECK_INPUT_IDLE) {
        return;
    }
    next_generation(input);
    input->offset_px = 0;
    input->settle_commit = false;
    input->has_settle_target = false;
    finish_to_ignored_or_idle(input);
}

int deck_input_offset(const deck_input_t *input)
{
    return input != NULL ? input->offset_px : 0;
}

uint32_t deck_input_generation(const deck_input_t *input)
{
    return input != NULL ? input->generation : 0;
}

deck_input_state_t deck_input_state(const deck_input_t *input)
{
    return input != NULL ? input->state : DECK_INPUT_IDLE;
}

bool deck_input_busy(const deck_input_t *input)
{
    return input != NULL && input->state != DECK_INPUT_IDLE;
}

bool deck_input_pointer_down(const deck_input_t *input)
{
    return input != NULL && input->pointer_down;
}
