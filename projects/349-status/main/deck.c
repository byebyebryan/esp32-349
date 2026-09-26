#include "deck.h"

#include <string.h>

static int index_of(const deck_t *deck, int id)
{
    for (int i = 0; i < deck->count; i++) {
        if (deck->ids[i] == id) {
            return i;
        }
    }
    return -1;
}

int deck_position(const deck_t *deck)
{
    return deck->has_focus ? index_of(deck, deck->focus_id) : -1;
}

void deck_cancel_pending(deck_t *deck)
{
    deck->has_pending = false;
}

void deck_reconcile(deck_t *deck, const int *ids, int count)
{
    const int old_position = deck_position(deck);
    if (count < 0) {
        count = 0;
    } else if (count > DECK_CAPACITY) {
        count = DECK_CAPACITY;
    }
    int successor = -1;
    if (old_position >= 0) {
        /* Keep the old next ID when a close and new arrivals share a tick. */
        for (int step = 1; step < deck->count && successor < 0; step++) {
            const int candidate = deck->ids[(old_position + step) % deck->count];
            for (int i = 0; i < count; i++) {
                if (ids[i] == candidate) {
                    successor = i;
                    break;
                }
            }
        }
    }
    memcpy(deck->ids, ids, sizeof(ids[0]) * (size_t)count);
    deck->count = count;
    if (count == 0) {
        deck->has_focus = false;
        deck_cancel_pending(deck);
        return;
    }
    if (!deck->has_focus || index_of(deck, deck->focus_id) < 0) {
        /* Removing the foreground selects its surviving circular successor.
         * Unrelated closes preserve the focused ID. */
        const int position = successor >= 0 ? successor : 0;
        deck->focus_id = deck->ids[position];
        deck->has_focus = true;
    }
    if (deck->has_pending && index_of(deck, deck->pending_id) < 0) {
        deck_cancel_pending(deck);
    }
}

void deck_request_focus(deck_t *deck, int id, int urgency, int focused_urgency, int64_t now_us)
{
    if (index_of(deck, id) < 0) {
        return;
    }
    if (urgency >= 2 || deck->count == 1) {
        deck->focus_id = id;
        deck->has_focus = true;
        deck_cancel_pending(deck);
    } else if (focused_urgency < 2) {
        deck->pending_id = id;
        deck->pending_due_us = now_us + DECK_SETTLE_US;
        deck->has_pending = true;
    }
}

bool deck_tick(deck_t *deck, int64_t now_us)
{
    if (!deck->has_pending || now_us < deck->pending_due_us) {
        return false;
    }
    const int previous = deck->focus_id;
    const int wanted = deck->pending_id;
    deck_cancel_pending(deck);
    if (index_of(deck, wanted) < 0) {
        return false;
    }
    deck->focus_id = wanted;
    deck->has_focus = true;
    return previous != wanted;
}

int deck_next_id(const deck_t *deck)
{
    const int position = deck_position(deck);
    return position >= 0 ? deck->ids[(position + 1) % deck->count] : 0;
}

void deck_advance(deck_t *deck)
{
    if (deck->count > 1) {
        deck->focus_id = deck_next_id(deck);
    }
    deck_cancel_pending(deck);
}
