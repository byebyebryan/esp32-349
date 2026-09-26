# Drag-and-snap notification deck

Design and implementation plan, 2026-09-25. **Implemented and accepted in
Starship's short physical trials at the recorded speed. The initial 25
updates/s target was not reached; the user found motion responsive enough.**
This refines the [accepted side-peek UI](ui-deck-plan.md) on Starship. The
[interaction preview](swipe-deck.html) uses synthetic cards and browser fonts;
it demonstrates motion, not panel performance or actual glyph rendering.

## Goal and scope

Make the foreground card follow a horizontal finger movement, then settle
onto the next or previous card. Retain readable notification text, the fixed
160 px telemetry rail, explicit local dismiss, and honest cache counts.

The first goal covers horizontal navigation, including slow dragging and a
short deliberate flick. Peek tapping remains an alternative. Vertical body
scrolling, swipe-to-dismiss, a notification history, and host paging are
separate decisions. The existing three-line body and title truncation remain.

## Geometry

| Element | Proposed geometry |
|---|---|
| Rail | x=0, width 160, height 172; stationary during navigation |
| Deck viewport | x=160, width 480, height 172; inner clipping ends at x=472 to preserve the 8 px right margin |
| Multiple-card foreground | x=8 within viewport, y=8, 392 × 156 |
| Adjacent card pitch | 400 px: 392 px card plus 8 px gap |
| Right peek at rest | Next card starts at x=408; 64 px exposed before the 8 px right margin |
| Previous card at rest | Starts at x=-392, outside the viewport |
| Single card | x=8, y=8, 464 × 156; no horizontal navigation |
| Text and dismiss | Existing 22 px title, 20 px body, 48 × 36 px × target |

The peek becomes the clipped edge of a full card rather than a separate small
widget. Adjacent cards use the same height and text geometry as the foreground,
so dragging does not resize or reflow text. Source and the start of the title
hint at the next card; the primary footer keeps `position / reachable` and
the separate `+N uncached` count. A restrained tonal difference can distinguish
the neighbor, without scaling text or adding a new row of controls.

## Interaction contract

| Input | Result |
|---|---|
| Drag left | Bring the right-hand next card forward |
| Drag right | Bring the previous card forward |
| Release past the distance threshold | Finish moving exactly one card |
| Short deliberate horizontal flick | Finish one card if the velocity and minimum travel thresholds both pass |
| Short drag or cancelled pointer | Return to the original card |
| Tap the right peek | Advance one card using the same settle animation |
| Tap the body | No action |
| Tap × | Hide that ID locally; retain the existing desktop notification behavior |
| Drag starting on × | Cancel the button click; do not navigate or dismiss |
| Vertical or ambiguous diagonal movement | No navigation or dismissal in this goal |
| Gesture starting in the rail | No deck navigation, including when it crosses into the card area |

Navigation remains **circular**, matching the accepted tap deck. Left from the
last card wraps to the first; right from the first wraps to the last. With two
cards, both directions select the other ID. One gesture never skips several
cards. `+N uncached` remains informational and is never a navigation target.

Starting values to tune on the board, expressed in landscape display pixels:

- Movement slop: 12 px. Lock horizontal intent when `abs(dx) >= 1.5 * abs(dy)`;
  lock vertical intent with the inverse condition. If neither wins by 24 px
  of movement, ignore the diagonal gesture until release.
- Commit distance: 96 px. Clamp card displacement to one 400 px pitch.
- Flick: at least 36 px horizontal travel and 0.5 px/ms release velocity in
  the same direction, measured over recent actual movement. Reject stale
  velocity after a pause; do not use only whole-gesture average velocity.
- Settle: approximately 180 ms, ease out, no overshoot or momentum through
  several cards. A cancelled drag eases back to zero.
- Suppress synthetic click/dismiss events after a drag until the pointer has
  been released. Do not interpret the touch driver's brief release debounce
  as a second gesture or an extra flick.

Thresholds are initial design values, not claims about this panel. Native
input policy tests use explicit values; short physical trials determine the
accepted tuning. Browser preview tuning is not panel acceptance.

## Notification changes during interaction

Host lifetime and cache updates continue throughout a drag. The interaction
captures the source, previous, and next **IDs**, their copied card text, and
the chosen direction. It never retains a pointer into the swappable PSRAM
cache or commits a target by its changing array position.

| Change | Behavior |
|---|---|
| Normal arrival during press/drag/settle | Update the cache but defer automatic focus; discard that queued normal focus when the interaction finishes |
| Critical arrival during interaction | Retain the latest eligible critical ID; take focus immediately after interaction finishes, if it is still active |
| New arrival after interaction finishes | Existing normal 300 ms settling and immediate critical policy resume |
| Replacement of a visible ID | Keep the copied text stable during movement; refresh it when settled. The ID remains selected |
| Unrelated close or expiry | Apply to the cache; preserve captured source and destination while both remain valid |
| Destination closes/expires | Cancel travel to it and settle back to the source if that source still exists |
| Source closes/expires | Cancel motion at the next UI reconciliation; select its surviving successor through the existing close policy |
| Full sync | Reconcile by ID. Continue only if the captured source and chosen destination survive; otherwise use the removal rules above |
| Deck becomes empty | Cancel motion, clear gesture state, and show the idle clock |
| Host becomes stale or legacy UI is selected | Cancel motion and show the appropriate connection/legacy state; require release before accepting new touch input |

At settlement, recheck IDs against the current reachable cache, commit focus,
refresh text/counts, then apply a retained critical arrival. No removed ID is
resurrected by a late animation callback. Counts describe the current cache;
they can change during a gesture, but the selected position changes only when
a destination is committed. Replacement/local-dismiss semantics remain as
specified by the cache implementation.

Holding a finger down does not extend notification lifetime. A critically
urgent arrival waits for release or cancellation rather than moving the card
under the finger; a later goal can revisit this policy if real usage needs it.

## Implementation approach

### 1. Bounded navigation and input policy

Extend `main/deck.c` / `deck.h` with previous/next lookup and selection by ID.
Keep order, local-hidden filtering, close fallback, and critical priority.
Add a small native-testable input policy module for axis lock, displacement,
release decision, cancellation, and the captured IDs. Use explicit states:
`idle -> pressed -> dragging -> settling -> idle`, with ignored/button input
requiring release before returning to idle. Every asynchronous settle has a
generation token so cancellation invalidates old callbacks.

Separate deferred focus from cache reconciliation. The current
`snapshot(..., false)` consumes arrival sequences, so calling it throughout
a drag would lose a critical arrival. Preserve critical events explicitly and
cancel normal events according to the interaction contract.

### 2. Three reusable card views

Refactor `main/ui_deck.c` into a clipped deck viewport with exactly three
reusable slots: previous, current, next. Bind text at rest/start/end and when
content changes; update positions during movement. A two-card deck can bind
both neighbors to the same ID. Zero/one-card modes hide unused slots.

Keep per-slot copied records and correctly sized label-text storage; the
current fixed 24-label table is insufficient for three full card views.
Measure the LVGL task stack and internal heap rather than adding more card
copies to the callback stack. No card-sized image snapshots or framebuffer
layers are required by this plan.

Drive movement from LVGL pointer/scroll events and settle from its animation
facilities; the 100 ms state timer is for reconciliation, not finger-following.
Use one input ownership model. Confirm whether LVGL's native scroll/snap path
can implement all cancellation rules with recycled slots; if it cannot, use
explicit press/move/release tracking plus LVGL animation. Do not combine
independent swipe recognition with scrolling that already consumes the gesture.
The existing touch coordinate transform remains the display driver's concern.

### 3. Preserve service and rendering boundaries

No host payload, capability, cache limit, or lifetime change is needed.
`dashboard-v1` continues to select the UI. Wire/readback still exposes the
settled focused ID and next ID; any temporary drag diagnostics used in a probe
must be clearly marked as diagnostics.

Reuse the existing display task and DMA completion path. The driver sends a
complete shadow frame whenever LVGL finishes a refresh; moving only the right
viewport reduces drawing work but does **not** reduce panel transfer bytes.
The prior high idle-CPU readings do not establish animation performance.
Collect a bounded drag/snap sample before making a smoothness claim.

## Checkpoints and acceptance

| Checkpoint | Deliverable and gate |
|---|---|
| A: policy | Native tests execute actual direction/threshold policy, circular ID selection, cancellations, deferred critical events, and removal/replacement/sync cases |
| B: rendering | Three reusable slots, correct clipping and × hit target, peek fallback, live dragging and one-card settlement; firmware build and existing host suite pass |
| C: headless integration | Readback verifies committed IDs after navigation, close/expiry during movement, sync, normal/critical arrivals, and zero/one/two/32-card states |
| D: Starship tuning | Isolated physical drag/touch trials and bounded performance/memory observation, recorded with the actual flashed build |

For checkpoint C, drive the real event/policy path through a development probe
or LVGL input fixture. A manually edited focus field is not interaction proof.
Headless checks complement the physical trials; they cannot validate touch
reliability or pixel motion.

On Starship, temporarily isolate test cards from desktop mirroring and keep
them active long enough for the trials. Record and restore the exact original
config, clear injected cards, and resume mirroring afterward. Short gates:

1. Three-card slow left/right drag, a short cancelled drag, deliberate flick,
   and circular wrap; each successful gesture moves exactly one card.
2. Twenty mixed attempts: at least 19 intentional navigations succeed, with
   zero accidental dismissals. Tune slop/velocity if this gate fails.
3. Body taps, peek taps, × taps, movement starting on ×, vertical/diagonal
   movement, and movement starting in the rail do what the contract specifies.
4. Test zero, one, two, and 32 reachable cards plus separate overflow; confirm
   counts and navigation reflect only reachable cached cards.
5. Inject normal/critical arrivals, replacement, destination expiry, source
   expiry, and full sync while touching. No arrival-driven focus jump under a
   held finger, removed-card resurrection, duplicate gesture, or stuck animation.
6. Sample active dragging for about 30 seconds: aim for at least 25 displayed
   updates/s and finger feedback within 100 ms. Record frame period, visible
   lag, minimum internal heap, and stack headroom. These are local targets;
   if they fail, stop at this gate and adjust the renderer/input design.
7. After interaction ends, the UI returns to its quiet state: unchanged text
   is not continuously rebound, stats remain responsive, and normal mirror
   behavior resumes.

The next implementation goal ends with reviewed source and recorded Starship
short-gate evidence. It has no soak or host-suspend gate. Commit/push and a
Snap rollout follow the user's requested workflow rather than this plan.

## Design-preview validation

The browser preview was checked with real pointer events: left/right dragging,
short-drag cancellation, flick selection, peek activation, local dismissal,
and empty-deck idle state. It retains exactly three view slots and fits 736 px
and 360 px browser widths without horizontal page overflow. Runtime checks
passed. The browser uses scalable system text; its appearance is a motion and
geometry reference, not evidence for embedded fonts, touch timing, or FPS.

## Implementation evidence

The implementation uses explicit pointer ownership and LVGL's settle animation,
with three reusable card views. Native policy checks and the production LVGL
pointer fixture pass, including both directions, peek dragging, cancellation,
flicks, explicit dismiss, rail ownership, and ID reconciliation during motion.
The fixture also exercises normal/critical arrivals, copied-text replacement,
full-sync ordering, removals while held and settling, stale/legacy cancellation,
release latching, and 0/1/2/32-card states with separate overflow. Its Debug and
Release builds retain assertions. The rendered fixture was inspected at rest
and during a drag; it checks software geometry, not panel motion.

The firmware build, dashboard parser, unchanged font audit, and all 118 host
tests pass. Starship physical trials cover finger-following, cancellation,
flick/wrap, hit targets, counts, and arrivals/removals/full sync during touch.
Two earlier renderer builds each passed 20/20 mixed navigations with no
accidental dismissals; the final renderer passed the repeated motion and
behavior checks. Exact identities and evidence boundaries are recorded in
[ACCEPTANCE.md](../ACCEPTANCE.md).

The renderer cleanup restores the full PSRAM DIRECT draw buffer and keeps
the existing rotated PSRAM shadow, with synchronous drawing under the display
lock. The 24-row internal-buffer experiment reduced conversion time but
increased drawing time and spent 30,720 bytes of internal RAM; it was reverted.
The detailed input-latency profiling API was also removed. Basic frame and
transfer diagnostics, heap/stack reporting, and native pointer/rotation tests
remain. The cleanup's validation and build identity are recorded separately
in `ACCEPTANCE.md`.

Before that cleanup, one uninterrupted active batch averaged 16.46 updates/s;
processed-input-to-transfer completion was about 52 ms mean and below 67 ms
in the recorded motion/hit-target samples, excluding sensor polling. The user
said the builds felt responsive enough, so FPS tuning stopped with the
25 updates/s target explicitly unmet. This usability decision supersedes the
instruction to continue tuning a failed numerical target in checkpoint D;
no claim of 25 updates/s or measured contact-to-photon latency is made.

## Later: vertical reading

If body truncation remains a real problem, add vertical scrolling inside the
body while keeping source/title/dismiss fixed. That requires direction lock
between body scrolling and deck navigation, a small overflow cue, and a policy
for reading position on replacement. The wire body currently has a fixed byte
limit: scrolling would reveal only text already cached, not recover text the
host has clipped. Any larger text budget needs a separate protocol/memory
review. Horizontal navigation can be accepted independently first.
