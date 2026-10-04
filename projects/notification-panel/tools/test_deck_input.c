/* Native checks execute the same gesture policy used by the firmware UI. */
#include <assert.h>
#include <stdio.h>

#include "deck_input.h"

#define START_US INT64_C(1000000)

static void press_three(deck_input_t *input, int source, int previous, int next,
                        int x, int y, int64_t now_us)
{
    assert(deck_input_press(input, x, y, now_us, source, previous, next, 3,
                            false, false));
}

static void assert_settled(deck_input_t *input, deck_input_action_t release,
                           int expected_id, bool expected_commit)
{
    assert(release.kind == DECK_INPUT_ACTION_SNAP ||
           release.kind == DECK_INPUT_ACTION_NEXT_TAP);
    assert(release.has_target && release.commit_target == expected_commit);
    assert(release.target_id == expected_id);
    const deck_input_action_t settled = deck_input_complete(
        input, release.generation, true, true, true);
    assert(settled.kind == DECK_INPUT_ACTION_SETTLED);
    assert(settled.has_target && settled.target_id == expected_id);
    assert(settled.commit_target == expected_commit);
    assert(deck_input_state(input) == DECK_INPUT_IDLE);
    assert(!deck_input_busy(input) && !deck_input_pointer_down(input));
}

static void test_slow_directions_and_zero_id(void)
{
    deck_input_t input;
    deck_input_init(&input);
    press_three(&input, 0, 11, 12, 300, 80, START_US);
    assert(deck_input_move(&input, 330, 80, START_US + 100000) == 30);
    assert(deck_input_move(&input, 410, 80, START_US + 500000) == 110);
    const deck_input_action_t previous =
        deck_input_release(&input, START_US + 700000);
    assert(previous.kind == DECK_INPUT_ACTION_SNAP);
    assert(previous.offset_px == DECK_INPUT_CARD_PITCH_PX);
    assert_settled(&input, previous, 11, true);

    press_three(&input, 11, 12, 0, 300, 80, START_US + 1000000);
    assert(deck_input_move(&input, 270, 80, START_US + 1100000) == -30);
    assert(deck_input_move(&input, 190, 80, START_US + 1500000) == -110);
    const deck_input_action_t next =
        deck_input_release(&input, START_US + 1700000);
    assert(next.kind == DECK_INPUT_ACTION_SNAP && next.offset_px == -400);
    assert_settled(&input, next, 0, true); /* ID zero is a valid destination. */
}

static void test_short_drag_flick_pause_and_reversal(void)
{
    deck_input_t input;
    deck_input_init(&input);
    press_three(&input, 10, 20, 30, 300, 80, START_US);
    assert(deck_input_move(&input, 312, 80, START_US + 10000) == 12);
    assert(deck_input_move(&input, 320, 80, START_US + 30000) == 20);
    deck_input_action_t release =
        deck_input_release(&input, START_US + 40000);
    assert(release.kind == DECK_INPUT_ACTION_SNAP);
    assert(release.target_id == 10 && !release.commit_target &&
           release.offset_px == 0);
    assert_settled(&input, release, 10, false);

    press_three(&input, 10, 20, 30, 300, 80, START_US + 100000);
    (void)deck_input_move(&input, 313, 80, START_US + 110000);
    assert(deck_input_move(&input, 350, 80, START_US + 160000) == 50);
    release = deck_input_release(&input, START_US + 165000);
    assert(release.commit_target && release.target_id == 20);
    assert_settled(&input, release, 20, true);

    /* Identical coordinates do not make a paused flick sample look recent. */
    press_three(&input, 10, 20, 30, 300, 80, START_US + 300000);
    (void)deck_input_move(&input, 313, 80, START_US + 310000);
    (void)deck_input_move(&input, 350, 80, START_US + 360000);
    (void)deck_input_move(&input, 350, 80, START_US + 450000);
    release = deck_input_release(&input, START_US + 451000);
    assert(release.kind == DECK_INPUT_ACTION_SNAP);
    assert(!release.commit_target && release.target_id == 10);
    assert_settled(&input, release, 10, false);

    /* Reversing before release reduces both travel and current velocity. */
    press_three(&input, 10, 20, 30, 300, 80, START_US + 600000);
    (void)deck_input_move(&input, 313, 80, START_US + 610000);
    (void)deck_input_move(&input, 350, 80, START_US + 630000);
    assert(deck_input_move(&input, 320, 80, START_US + 650000) == 20);
    release = deck_input_release(&input, START_US + 651000);
    assert(!release.commit_target && release.target_id == 10);
    assert_settled(&input, release, 10, false);
}

static void test_axis_and_pointer_ownership(void)
{
    deck_input_t input;
    deck_input_init(&input);
    press_three(&input, 1, 2, 3, 300, 60, START_US);
    (void)deck_input_move(&input, 316, 71, START_US + 10000);
    (void)deck_input_move(&input, 321, 80, START_US + 20000);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_release(&input, START_US + 21000).kind ==
           DECK_INPUT_ACTION_NONE);

    press_three(&input, 1, 2, 3, 300, 60, START_US + 30000);
    (void)deck_input_move(&input, 300, 75, START_US + 40000);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_release(&input, START_US + 41000).kind ==
           DECK_INPUT_ACTION_NONE);

    /* Rail presses retain ignored ownership even after entering the viewport. */
    press_three(&input, 1, 2, 3, 159, 60, START_US + 50000);
    (void)deck_input_move(&input, 300, 60, START_US + 60000);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_release(&input, START_US + 61000).kind ==
           DECK_INPUT_ACTION_NONE);

    assert(deck_input_press(&input, 300, 60, START_US + 70000,
                            0, 0, 0, 0, false, false));
    (void)deck_input_move(&input, 380, 60, START_US + 80000);
    assert(deck_input_release(&input, START_US + 81000).kind ==
           DECK_INPUT_ACTION_NONE);

    assert(deck_input_press(&input, 300, 60, START_US + 90000,
                            0, 0, 0, 1, false, false));
    (void)deck_input_move(&input, 340, 60, START_US + 100000);
    assert(deck_input_offset(&input) == 0);
    const deck_input_action_t one_card =
        deck_input_release(&input, START_US + 101000);
    assert(one_card.kind == DECK_INPUT_ACTION_SNAP &&
           one_card.target_id == 0 && !one_card.commit_target);
    assert_settled(&input, one_card, 0, false);
}

static void test_buttons_and_two_card_neighbors(void)
{
    deck_input_t input;
    deck_input_init(&input);
    assert(deck_input_press(&input, 550, 20, START_US, 0, 8, 8, 2,
                            true, false));
    (void)deck_input_move(&input, 555, 22, START_US + 10000);
    deck_input_action_t action = deck_input_release(&input, START_US + 20000);
    assert(action.kind == DECK_INPUT_ACTION_DISMISS && action.target_id == 0);

    assert(deck_input_press(&input, 550, 20, START_US + 30000, 0, 8, 8, 2,
                            true, false));
    (void)deck_input_move(&input, 570, 20, START_US + 40000);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_release(&input, START_US + 41000).kind ==
           DECK_INPUT_ACTION_NONE);

    assert(deck_input_press(&input, 480, 80, START_US + 50000, 0, 8, 8, 2,
                            false, true));
    action = deck_input_release(&input, START_US + 51000);
    assert(action.kind == DECK_INPUT_ACTION_NEXT_TAP &&
           action.target_id == 8 && action.commit_target &&
           action.offset_px == -DECK_INPUT_CARD_PITCH_PX);
    assert_settled(&input, action, 8, true);

    assert(deck_input_press(&input, 480, 80, START_US + 60000, 0, 8, 8, 2,
                            false, true));
    (void)deck_input_move(&input, 500, 80, START_US + 70000);
    action = deck_input_release(&input, START_US + 71000);
    assert(action.kind == DECK_INPUT_ACTION_SNAP &&
           !action.commit_target && action.target_id == 0);
    assert_settled(&input, action, 0, false);

    assert(deck_input_press(&input, 480, 80, START_US + 80000, 0, 8, 8, 2,
                            false, true));
    (void)deck_input_move(&input, 480, 100, START_US + 90000);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_release(&input, START_US + 91000).kind ==
           DECK_INPUT_ACTION_NONE);

    assert(deck_input_press(&input, 480, 80, START_US + 100000, 0, 8, 8, 2,
                            false, true));
    (void)deck_input_move(&input, 496, 91, START_US + 110000);
    (void)deck_input_move(&input, 501, 100, START_US + 120000);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_release(&input, START_US + 121000).kind ==
           DECK_INPUT_ACTION_NONE);
}

static void test_unused_neighbor_and_destination_invalidation(void)
{
    deck_input_t input;
    deck_input_init(&input);
    press_three(&input, 10, 20, 30, 300, 80, START_US);
    (void)deck_input_move(&input, 270, 80, START_US + 20000);
    assert(deck_input_validate(&input, true, false, true).kind ==
           DECK_INPUT_ACTION_NONE); /* Removed previous card is unused. */
    assert(deck_input_state(&input) == DECK_INPUT_DRAGGING);
    (void)deck_input_move(&input, 190, 80, START_US + 200000);
    deck_input_action_t release =
        deck_input_release(&input, START_US + 300000);
    assert(release.commit_target && release.target_id == 30);
    assert_settled(&input, release, 30, true);

    press_three(&input, 10, 20, 30, 300, 80, START_US + 400000);
    (void)deck_input_move(&input, 250, 80, START_US + 420000);
    const deck_input_action_t rollback =
        deck_input_validate(&input, true, true, false);
    assert(rollback.kind == DECK_INPUT_ACTION_SNAP);
    assert(rollback.target_id == 10 && !rollback.commit_target &&
           rollback.offset_px == 0);
    assert(deck_input_state(&input) == DECK_INPUT_SETTLING);
    assert(deck_input_pointer_down(&input) && deck_input_busy(&input));
    const deck_input_action_t returned = deck_input_complete(
        &input, rollback.generation, true, true, false);
    assert(returned.kind == DECK_INPUT_ACTION_SETTLED);
    assert(returned.target_id == 10 && !returned.commit_target);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_pointer_down(&input));
    assert(deck_input_move(&input, 220, 80, START_US + 500000) == 0);
    assert(deck_input_release(&input, START_US + 510000).kind ==
           DECK_INPUT_ACTION_NONE);
    assert(deck_input_state(&input) == DECK_INPUT_IDLE);
}

static void test_source_invalidation_and_late_generation(void)
{
    deck_input_t input;
    deck_input_init(&input);
    press_three(&input, 1, 2, 3, 300, 80, START_US);
    (void)deck_input_move(&input, 250, 80, START_US + 20000);
    const uint32_t before_source_close = deck_input_generation(&input);
    assert(deck_input_validate(&input, false, true, true).kind ==
           DECK_INPUT_ACTION_NONE);
    assert(deck_input_generation(&input) != before_source_close);
    assert(deck_input_state(&input) == DECK_INPUT_IGNORED);
    assert(deck_input_pointer_down(&input));
    assert(deck_input_move(&input, 200, 80, START_US + 30000) == 0);
    assert(deck_input_release(&input, START_US + 31000).kind ==
           DECK_INPUT_ACTION_NONE);
    assert(!deck_input_busy(&input));

    press_three(&input, 1, 2, 3, 300, 80, START_US + 40000);
    (void)deck_input_move(&input, 250, 80, START_US + 50000);
    deck_input_action_t release =
        deck_input_release(&input, START_US + 60000);
    assert(release.commit_target && release.target_id == 3);
    const uint32_t late_generation = release.generation;
    deck_input_action_t rollback =
        deck_input_validate(&input, true, true, false);
    assert(rollback.kind == DECK_INPUT_ACTION_SNAP &&
           rollback.target_id == 1 && !rollback.commit_target);
    assert(deck_input_complete(&input, late_generation, true, true, false)
               .kind == DECK_INPUT_ACTION_NONE);
    const deck_input_action_t completed = deck_input_complete(
        &input, rollback.generation, true, true, false);
    assert(completed.kind == DECK_INPUT_ACTION_SETTLED);
    assert(completed.target_id == 1 && !completed.commit_target);

    press_three(&input, 1, 2, 3, 300, 80, START_US + 70000);
    (void)deck_input_move(&input, 250, 80, START_US + 80000);
    release = deck_input_release(&input, START_US + 90000);
    assert(release.commit_target && release.target_id == 3);
    deck_input_cancel(&input);
    assert(deck_input_state(&input) == DECK_INPUT_IDLE);
    assert(deck_input_generation(&input) != release.generation);
    assert(deck_input_complete(&input, release.generation, true, true, true)
               .kind == DECK_INPUT_ACTION_NONE);
}

int main(void)
{
    test_slow_directions_and_zero_id();
    test_short_drag_flick_pause_and_reversal();
    test_axis_and_pointer_ownership();
    test_buttons_and_two_card_neighbors();
    test_unused_neighbor_and_destination_invalidation();
    test_source_invalidation_and_late_generation();
    puts("deck input: directions, thresholds, ownership, validity, and generations pass");
    return 0;
}
