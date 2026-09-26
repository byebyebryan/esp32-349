#pragma once
#include "deck_input.h"
#define GROUP_CARD_PITCH_PX 128
/* Local navigation target for an empty Notifications group; never a wire ID. */
#define GROUP_EMPTY_NOTIFICATIONS_ID (-1)

typedef enum { GROUP_AXIS_NONE, GROUP_AXIS_HORIZONTAL, GROUP_AXIS_VERTICAL } group_axis_t;
typedef struct {
    deck_input_t motion;
    group_axis_t axis;
    int x, y, source, selected, newer, older;
    int64_t press_us;
    bool home, has_cards, has_newer, has_older, peek;
} group_input_t;

void group_input_init(group_input_t *input);
bool group_input_press(group_input_t *input, int x, int y, int64_t now,
                       bool home, int selected, bool has_cards,
                       int newer, bool has_newer, int older, bool has_older,
                       bool dismiss, bool peek);
int group_input_move(group_input_t *input, int x, int y, int64_t now);
deck_input_action_t group_input_release(group_input_t *input, int64_t now);
/* Source/neighbors use captured identities, not current positions. */
deck_input_action_t group_input_validate(group_input_t *input,
                                         bool source, bool newer, bool older,
                                         bool selected);
deck_input_action_t group_input_complete(group_input_t *input, uint32_t generation,
                                         bool source, bool newer, bool older,
                                         bool selected);
void group_input_cancel(group_input_t *input);
