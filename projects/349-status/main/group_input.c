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

bool group_input_press(group_input_t *g, int x, int y, int64_t now,
                       bool home, int selected, bool has_cards,
                       int newer, bool has_newer, int older, bool has_older,
                       bool dismiss, bool peek)
{
    if (deck_input_busy(&g->motion)) return false;
    g->axis = GROUP_AXIS_NONE;
    g->x = x; g->y = y; g->home = home; g->source = home ? 0 : selected;
    g->press_us = now;
    g->selected = selected; g->has_cards = has_cards;
    g->newer = newer; g->older = older;
    g->has_newer = has_newer; g->has_older = has_older; g->peek = peek;
    return deck_input_press(&g->motion, x, y, now, g->source, 0, 0, 1,
                            dismiss, false);
}

int group_input_move(group_input_t *g, int x, int y, int64_t now)
{
    if (!g->motion.pointer_down) return 0;
    if (g->motion.state == DECK_INPUT_BUTTON_DISMISS ||
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

deck_input_action_t group_input_complete(group_input_t *g, uint32_t generation,
                                         bool source, bool newer, bool older,
                                         bool selected)
{
    bool previous, next;
    validity(g, newer, older, selected, &previous, &next);
    return deck_input_complete(&g->motion, generation, g->home || source, previous, next);
}

void group_input_cancel(group_input_t *g) { deck_input_cancel(&g->motion); }
