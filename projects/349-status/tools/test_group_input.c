#include <assert.h>
#include <stdio.h>
#include "../main/group_input.h"

static void press(group_input_t *g, bool home, int newer, int older, bool cross)
{
    group_input_init(g);
    assert(group_input_press(g, 320, 90, 1000, home, 2, true,
                              newer, newer != 0, older, older != 0, cross, false));
}

int main(void)
{
    group_input_t g;
    press(&g, true, 0, 0, false);
    assert(group_input_move(&g, 200, 92, 100000) == -120);
    assert(g.axis == GROUP_AXIS_HORIZONTAL);
    /* Axis cannot switch after capture. */
    assert(group_input_move(&g, 200, 160, 200000) == -120);
    deck_input_action_t a = group_input_release(&g, 200000);
    assert(a.commit_target && a.target_id == 2 && a.offset_px == -480);
    a = group_input_complete(&g, a.generation, true, false, false, true);
    assert(a.kind == DECK_INPUT_ACTION_SETTLED);

    press(&g, false, 1, 3, false);
    assert(group_input_move(&g, 321, 20, 100000) == -70);
    assert(g.axis == GROUP_AXIS_VERTICAL);
    a = group_input_release(&g, 200000);
    assert(a.commit_target && a.target_id == 3 && a.offset_px == -GROUP_CARD_PITCH_PX);
    const uint32_t generation = a.generation;
    a = group_input_validate(&g, true, true, false, true);
    assert(a.kind == DECK_INPUT_ACTION_SNAP && !a.commit_target && a.target_id == 2);
    a = group_input_complete(&g, generation, true, true, false, true);
    assert(a.kind == DECK_INPUT_ACTION_NONE);

    press(&g, false, 0, 3, false);
    assert(group_input_move(&g, 320, 160, 100000) == 0);
    a = group_input_release(&g, 200000);
    assert(!a.commit_target); /* Newest bound, no wrap. */
    press(&g, false, 1, 0, false);
    assert(group_input_move(&g, 320, 10, 100000) == 0);
    a = group_input_release(&g, 200000);
    assert(!a.commit_target); /* Oldest bound. */

    press(&g, false, 1, 3, false);
    group_input_move(&g, 340, 110, 100000);
    assert(g.motion.state == DECK_INPUT_IGNORED);
    assert(group_input_release(&g, 100000).kind == DECK_INPUT_ACTION_NONE);
    press(&g, false, 1, 3, true);
    group_input_move(&g, 200, 90, 100000);
    assert(g.motion.state == DECK_INPUT_IGNORED);
    assert(group_input_release(&g, 100000).kind == DECK_INPUT_ACTION_NONE);

    group_input_init(&g);
    assert(group_input_press(&g, 80, 70, 1000, false, 2, true, 1, true, 3, true, false, false));
    assert(group_input_move(&g, 400, 70, 100000) == 0);
    assert(group_input_release(&g, 100000).kind == DECK_INPUT_ACTION_NONE);

    press(&g, false, 1, 3, false);
    assert(group_input_move(&g, 320, 70, 100000) == -20);
    a = group_input_release(&g, 200000);
    assert(!a.commit_target && a.offset_px == 0);
    assert(group_input_complete(&g, a.generation, true, true, true, true).kind == DECK_INPUT_ACTION_SETTLED);
    press(&g, false, 1, 3, false);
    assert(group_input_move(&g, 320, 62, 21000) == -28);
    a = group_input_release(&g, 22000);
    assert(a.commit_target && a.target_id == 3); /* Single fresh sample deliberate flick. */
    group_input_init(&g);
    assert(group_input_press(&g, 320, 90, 1000, true, GROUP_EMPTY_NOTIFICATIONS_ID,
                             false, 0, false, 0, false, false, false));
    assert(group_input_move(&g, 200, 90, 100000) == -120);
    a = group_input_release(&g, 200000);
    assert(a.commit_target && a.target_id == GROUP_EMPTY_NOTIFICATIONS_ID);
    assert(group_input_complete(&g, a.generation, true, false, false, true).kind == DECK_INPUT_ACTION_SETTLED);
    assert(group_input_press(&g, 320, 90, 300000, false, GROUP_EMPTY_NOTIFICATIONS_ID,
                             false, 0, false, 0, false, false, false));
    assert(group_input_move(&g, 320, 20, 400000) == 0);
    assert(g.axis == GROUP_AXIS_NONE);
    assert(group_input_release(&g, 400000).kind == DECK_INPUT_ACTION_NONE);
    assert(group_input_press(&g, 320, 90, 500000, false, GROUP_EMPTY_NOTIFICATIONS_ID,
                             false, 0, false, 0, false, false, false));
    assert(group_input_move(&g, 440, 90, 600000) == 120);
    a = group_input_release(&g, 700000);
    assert(a.commit_target && a.target_id == 0);
    puts("group input policy: passed");
    return 0;
}
