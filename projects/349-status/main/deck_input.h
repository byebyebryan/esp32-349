#pragma once

#include <stdbool.h>
#include <stdint.h>

#define DECK_INPUT_RAIL_WIDTH_PX 160
#define DECK_INPUT_SLOP_PX 12
#define DECK_INPUT_AMBIGUOUS_PX 24
#define DECK_INPUT_COMMIT_PX 96
#define DECK_INPUT_FLICK_TRAVEL_PX 36
#define DECK_INPUT_FLICK_VELOCITY_MILLI_PX_PER_MS 500
#define DECK_INPUT_RECENT_MOTION_US 80000
#define DECK_INPUT_CARD_PITCH_PX 400
#define DECK_INPUT_MOTION_SAMPLES 8

typedef enum {
    DECK_INPUT_IDLE,
    DECK_INPUT_PRESSED,
    DECK_INPUT_DRAGGING,
    DECK_INPUT_SETTLING,
    DECK_INPUT_BUTTON_DISMISS,
    DECK_INPUT_BUTTON_PEEK,
    DECK_INPUT_IGNORED,
} deck_input_state_t;

typedef enum {
    DECK_INPUT_ACTION_NONE,
    DECK_INPUT_ACTION_DISMISS,
    DECK_INPUT_ACTION_NEXT_TAP,
    DECK_INPUT_ACTION_SNAP,
    DECK_INPUT_ACTION_SETTLED,
} deck_input_action_kind_t;

typedef struct {
    deck_input_action_kind_t kind;
    bool has_target;
    int target_id;
    /* True only when a settled destination should become selected. */
    bool commit_target;
    /* Settling endpoint relative to the captured source card. */
    int offset_px;
    uint32_t generation;
} deck_input_action_t;

/* Fixed-size input policy. IDs and their validity are captured at press. */
typedef struct {
    deck_input_state_t state;
    bool pointer_down;
    bool has_source;
    bool has_previous;
    bool has_next;
    bool captured_previous;
    bool captured_next;
    int source_id;
    int previous_id;
    int next_id;
    bool settle_commit;
    bool has_settle_target;
    int settle_target_id;
    int settle_direction;
    int start_x;
    int start_y;
    int last_x;
    int last_y;
    int offset_px;
    int64_t last_motion_us;
    int motion_count;
    int motion_x[DECK_INPUT_MOTION_SAMPLES];
    int motion_y[DECK_INPUT_MOTION_SAMPLES];
    int64_t motion_us[DECK_INPUT_MOTION_SAMPLES];
    uint32_t generation;
    int pitch_px, commit_px, flick_travel_px;
} deck_input_t;

/* Optional geometry for a captured axis; legacy defaults remain unchanged. */
void deck_input_geometry(deck_input_t *input, int pitch, int commit, int flick);

void deck_input_init(deck_input_t *input);
bool deck_input_press(deck_input_t *input, int x, int y, int64_t now_us,
                      int source_id, int previous_id, int next_id, int count,
                      bool dismiss_hit, bool peek_hit);
int deck_input_move(deck_input_t *input, int x, int y, int64_t now_us);
deck_input_action_t deck_input_release(deck_input_t *input, int64_t now_us);

/*
 * Refresh captured-ID validity. A missing active destination returns a snap
 * to source; a missing source invalidates the generation and latches input
 * until release. Unused-neighbor removal does not cancel the gesture.
 */
deck_input_action_t deck_input_validate(deck_input_t *input,
                                        bool source_valid,
                                        bool previous_valid,
                                        bool next_valid);

/*
 * Settle callbacks must pass their captured generation and current ID
 * validity. A current successful callback returns SETTLED with the selected
 * ID and commit flag before clearing the state. A stale callback returns NONE.
 */
deck_input_action_t deck_input_complete(deck_input_t *input,
                                        uint32_t generation,
                                        bool source_valid,
                                        bool previous_valid,
                                        bool next_valid);

/* Cancel animations/motion and ignore the current pointer until release. */
void deck_input_cancel(deck_input_t *input);
int deck_input_offset(const deck_input_t *input);
uint32_t deck_input_generation(const deck_input_t *input);
deck_input_state_t deck_input_state(const deck_input_t *input);
bool deck_input_busy(const deck_input_t *input);
bool deck_input_pointer_down(const deck_input_t *input);
