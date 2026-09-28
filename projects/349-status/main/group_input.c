#include "group_input.h"
#include <string.h>

static void choose_axis(group_input_t *g, group_axis_t axis)
{
    const uint32_t generation = g->motion.generation;
    deck_input_init(&g->motion);
    g->motion.generation = generation;
    g->axis = axis;
    if (axis == GROUP_AXIS_HORIZONTAL) {
        deck_input_press(&g->motion, g->x, g->y, g->press_us, g->source,
                         0, g->selected, 2, false, false);
        deck_input_geometry(&g->motion, 480, 96, 36);
        deck_input_validate(&g->motion, true, !g->home, g->home);
    } else {
        deck_input_press(&g->motion, 160 + g->y, g->x, g->press_us, g->source,
                         g->newer, g->older, 2, false, g->peek);
        deck_input_geometry(&g->motion, GROUP_CARD_PITCH_PX, 48, 24);
        deck_input_validate(&g->motion, true, g->has_newer, g->has_older);
    }
}

void group_input_init(group_input_t *g)
{
    memset(g, 0, sizeof(*g));
    deck_input_init(&g->motion);
}

static bool control_contains(const group_input_t *g, int x, int y)
{
    return x >= g->control_x && y >= g->control_y &&
           x < g->control_x + g->control_width &&
           y < g->control_y + g->control_height;
}

static bool control_exceeded_slop(const group_input_t *g, int x, int y)
{
    const int64_t dx = (int64_t)x - g->motion.start_x;
    const int64_t dy = (int64_t)y - g->motion.start_y;
    const int64_t ax = dx < 0 ? -dx : dx;
    const int64_t ay = dy < 0 ? -dy : dy;
    return ax >= DECK_INPUT_SLOP_PX || ay >= DECK_INPUT_SLOP_PX ||
           ax * ax + ay * ay >= DECK_INPUT_SLOP_PX * DECK_INPUT_SLOP_PX;
}

static void clear_control(group_input_t *g)
{
    g->control = GROUP_CONTROL_NONE;
    g->control_x = g->control_y = 0;
    g->control_width = g->control_height = 0;
    g->control_id = g->control_open_revision = 0;
    g->control_enabled = false;
    g->control_cancelled = false;
}

bool group_input_press(group_input_t *g, int x, int y, int64_t now,
                       bool home, int selected, bool has_cards,
                       int newer, bool has_newer, int older, bool has_older,
                       bool dismiss, bool peek)
{
    if (deck_input_busy(&g->motion)) return false;
    g->axis = GROUP_AXIS_NONE;
    clear_control(g);
    g->x = x; g->y = y; g->home = home; g->source = home ? 0 : selected;
    g->press_us = now;
    g->selected = selected; g->has_cards = has_cards;
    g->newer = newer; g->older = older;
    g->has_newer = has_newer; g->has_older = has_older; g->peek = peek;
    return deck_input_press(&g->motion, x, y, now, g->source, 0, 0, 1,
                            dismiss, false);
}

void group_input_capture_control(group_input_t *g, group_control_t control,
                                 int x, int y, int width, int height,
                                 int id, int open_revision, bool enabled)
{
    if (g == NULL || !g->motion.pointer_down ||
        (control != GROUP_CONTROL_OPEN && control != GROUP_CONTROL_DISMISS) ||
        width <= 0 || height <= 0 || g->axis != GROUP_AXIS_NONE) {
        return;
    }
    g->control = control;
    g->control_x = x;
    g->control_y = y;
    g->control_width = width;
    g->control_height = height;
    if (!control_contains(g, g->motion.start_x, g->motion.start_y)) {
        clear_control(g);
        return;
    }
    g->control_id = id;
    g->control_open_revision = open_revision;
    g->control_enabled = enabled;
    g->control_cancelled = false;
    g->motion.state = control == GROUP_CONTROL_OPEN
        ? DECK_INPUT_BUTTON_OPEN : DECK_INPUT_BUTTON_DISMISS;
}

int group_input_move(group_input_t *g, int x, int y, int64_t now)
{
    if (!g->motion.pointer_down) return 0;
    if (g->control != GROUP_CONTROL_NONE) {
        if (!g->control_cancelled &&
            (!control_contains(g, x, y) || control_exceeded_slop(g, x, y))) {
            g->control_cancelled = true;
            deck_input_cancel(&g->motion);
        }
        if (g->control_cancelled) return 0;
        return deck_input_move(&g->motion, x, y, now);
    }
    if (g->motion.state == DECK_INPUT_BUTTON_DISMISS ||
        g->motion.state == DECK_INPUT_BUTTON_OPEN ||
        g->motion.state == DECK_INPUT_IGNORED) {
        return deck_input_move(&g->motion, x, y, now);
    }
    if (g->axis == GROUP_AXIS_NONE) {
        const int64_t dx = (int64_t)x - g->x, dy = (int64_t)y - g->y;
        const int64_t ax = dx < 0 ? -dx : dx, ay = dy < 0 ? -dy : dy;
        if (ax >= 12 && ax * 2 >= ay * 3) {
            choose_axis(g, GROUP_AXIS_HORIZONTAL);
        } else if (ay >= 12 && ay * 2 >= ax * 3 && !g->home && g->has_cards) {
            choose_axis(g, GROUP_AXIS_VERTICAL);
        } else if (ax >= 24 || ay >= 24 || ax * ax + ay * ay >= 24 * 24) {
            deck_input_cancel(&g->motion);
            return 0;
        } else return 0;
    }
    return g->axis == GROUP_AXIS_VERTICAL
        ? deck_input_move(&g->motion, 160 + y, x, now)
        : deck_input_move(&g->motion, x, y, now);
}

deck_input_action_t group_input_release(group_input_t *g, int64_t now)
{
    return group_input_release_at(g, g->motion.last_x, g->motion.last_y, now);
}

deck_input_action_t group_input_release_at(group_input_t *g, int x, int y,
                                           int64_t now)
{
    if (g->control != GROUP_CONTROL_NONE) {
        const group_control_t control = g->control;
        const int id = g->control_id;
        const int revision = g->control_open_revision;
        const bool enabled = g->control_enabled;
        const bool inside = !g->control_cancelled && control_contains(g, x, y) &&
                            !control_exceeded_slop(g, x, y);
        if (!inside) {
            g->control_cancelled = true;
            deck_input_cancel(&g->motion);
        }
        deck_input_action_t action = deck_input_release(&g->motion, now);
        clear_control(g);
        if (!inside || (control == GROUP_CONTROL_OPEN && !enabled)) {
            return (deck_input_action_t){0};
        }
        if (control == GROUP_CONTROL_OPEN && action.kind == DECK_INPUT_ACTION_OPEN) {
            action.target_id = id;
            action.open_revision = revision;
            return action;
        }
        if (control == GROUP_CONTROL_DISMISS && action.kind == DECK_INPUT_ACTION_DISMISS) {
            action.target_id = id;
            return action;
        }
        return (deck_input_action_t){0};
    }
    if (g->axis == GROUP_AXIS_NONE && g->peek &&
        g->motion.state == DECK_INPUT_PRESSED && g->has_older) {
        choose_axis(g, GROUP_AXIS_VERTICAL);
    }
    return deck_input_release(&g->motion, now);
}

static void validity(const group_input_t *g, bool newer, bool older, bool selected,
                     bool *previous, bool *next)
{
    if (g->axis == GROUP_AXIS_HORIZONTAL) {
        *previous = !g->home;
        *next = g->home && selected;
    } else {
        *previous = g->has_newer && newer;
        *next = g->has_older && older;
    }
}

deck_input_action_t group_input_validate(group_input_t *g, bool source,
                                         bool newer, bool older, bool selected)
{
    bool previous, next;
    validity(g, newer, older, selected, &previous, &next);
    return deck_input_validate(&g->motion, g->home || source, previous, next);
}

deck_input_action_t group_input_validate_action(group_input_t *g,
                                                bool source, bool newer,
                                                bool older, bool selected,
                                                bool actions_enabled, int id,
                                                int open_revision,
                                                bool open_enabled)
{
    deck_input_action_t action = group_input_validate(g, source, newer, older, selected);
    if (action.kind != DECK_INPUT_ACTION_NONE ||
        g->control == GROUP_CONTROL_NONE || g->control_cancelled) {
        return action;
    }
    const bool identity_changed = !source || id != g->control_id;
    const bool open_changed = g->control == GROUP_CONTROL_OPEN &&
        (!actions_enabled || open_revision != g->control_open_revision ||
         (g->control_enabled && !open_enabled));
    if (identity_changed || open_changed) {
        g->control_cancelled = true;
        deck_input_cancel(&g->motion);
    }
    return action;
}

deck_input_action_t group_input_complete(group_input_t *g, uint32_t generation,
                                         bool source, bool newer, bool older,
                                         bool selected)
{
    bool previous, next;
    validity(g, newer, older, selected, &previous, &next);
    return deck_input_complete(&g->motion, generation, g->home || source, previous, next);
}

void group_input_cancel(group_input_t *g)
{
    if (g->control != GROUP_CONTROL_NONE) g->control_cancelled = true;
    deck_input_cancel(&g->motion);
}
