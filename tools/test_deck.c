/* Native checks execute the same focus policy compiled into the firmware. */
#include <assert.h>
#include <stdio.h>

#include "deck.h"

int main(void)
{
    deck_t deck = {0};
    int one[] = {1};
    deck_reconcile(&deck, one, 1);
    assert(deck.has_focus && deck.focus_id == 1);
    int two[] = {2, 1};
    deck_reconcile(&deck, two, 2);
    int neighbor = -1;
    assert(deck_neighbor_id(&deck, -1, &neighbor) && neighbor == 2);
    assert(deck_neighbor_id(&deck, 1, &neighbor) && neighbor == 2);
    assert(!deck_neighbor_id(&deck, 0, &neighbor));
    deck_request_focus(&deck, 2, 1, 1, 1000);
    assert(deck.focus_id == 1 && !deck_tick(&deck, 200000));
    int three[] = {3, 2, 1};
    deck_reconcile(&deck, three, 3);
    deck_request_focus(&deck, 3, 1, 1, 200000);
    assert(!deck_tick(&deck, 499999));
    assert(deck_tick(&deck, 500000) && deck.focus_id == 3);

    deck_advance(&deck);
    assert(deck.focus_id == 2 && deck_position(&deck) == 1);
    deck_reconcile(&deck, three, 3); /* Periodic sync or in-place replacement. */
    assert(deck.focus_id == 2);
    int close_and_arrive[] = {4, 3, 1};
    deck_reconcile(&deck, close_and_arrive, 3);
    assert(deck.focus_id == 1); /* Old successor, regardless of inserted IDs. */
    deck_reconcile(&deck, three, 3);
    deck_request_focus(&deck, 2, 2, 1, 600000);
    int removed_front[] = {3, 1};
    deck_reconcile(&deck, removed_front, 2);
    assert(deck.focus_id == 1);
    int removed_other[] = {1};
    deck_reconcile(&deck, removed_other, 1);
    assert(deck.focus_id == 1);

    deck_reconcile(&deck, three, 3);
    deck_request_focus(&deck, 2, 2, 1, 1000000);
    assert(deck.focus_id == 2 && !deck.has_pending);
    deck_request_focus(&deck, 3, 1, 2, 1000001);
    assert(!deck.has_pending && !deck_tick(&deck, 2000000));
    deck_advance(&deck);
    assert(deck.focus_id == 1);
    deck_request_focus(&deck, 3, 1, 1, 2000001);
    deck_advance(&deck); /* User browsing cancels a queued automatic shift. */
    assert(deck.focus_id == 3 && !deck.has_pending);
    deck_request_focus(&deck, 2, 1, 1, 3000000);
    deck_reconcile(&deck, removed_front, 2);
    assert(!deck.has_pending && !deck_tick(&deck, 4000000));

    deck_reconcile(&deck, one, 0);
    assert(!deck.has_focus && deck_position(&deck) == -1);
    int zero_id[] = {0};
    deck_reconcile(&deck, zero_id, 1);
    assert(deck.has_focus && deck.focus_id == 0);
    assert(!deck_neighbor_id(&deck, -1, &neighbor));
    assert(!deck_neighbor_id(&deck, 1, &neighbor));

    /* A tap at an elapsed arrival deadline still selects the visible next
     * card, rather than advancing from a pending foreground. */
    deck_reconcile(&deck, two, 2);
    deck_reconcile(&deck, three, 3);
    deck_request_focus(&deck, 3, 1, 1, 5000000);
    assert(deck_select_id(&deck, 2));
    assert(deck.focus_id == 2 && !deck.has_pending);
    assert(!deck_select_id(&deck, 99));
    assert(deck_next_id(&deck) == 1);
    deck_advance(&deck);
    assert(deck.focus_id == 1 && !deck_tick(&deck, 5500000));
    puts("deck focus: burst, sync, close, urgency, touch, and empty transitions pass");
    return 0;
}
