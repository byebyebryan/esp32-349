#pragma once

#include <stdbool.h>
#include <stdint.h>

#define DECK_CAPACITY 32
#define DECK_SETTLE_US 300000

/* Pure focus policy. IDs are reachable, nonhidden cards, newest first. */
typedef struct {
    int ids[DECK_CAPACITY];
    int count;
    int focus_id;
    bool has_focus;
    int pending_id;
    int64_t pending_due_us;
    bool has_pending;
} deck_t;

void deck_reconcile(deck_t *deck, const int *ids, int count);
void deck_request_focus(deck_t *deck, int id, int urgency, int focused_urgency, int64_t now_us);
bool deck_tick(deck_t *deck, int64_t now_us);
void deck_advance(deck_t *deck);
void deck_cancel_pending(deck_t *deck);
int deck_position(const deck_t *deck);
bool deck_neighbor_id(const deck_t *deck, int direction, int *out_id);
bool deck_select_id(deck_t *deck, int id);
int deck_next_id(const deck_t *deck);
