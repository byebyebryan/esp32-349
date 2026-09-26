# Grouped UI wire contract

Implementation contract, 2026-09-26. This extends protocol 1 only when firmware
advertises `grouped-ui-v1` together with `card-sync-v1` and `dashboard-v1`.
Older peers keep active notification semantics and the existing horizontal deck.

## Identity and snapshots

The host allocates a random positive 31-bit `session` per daemon lifetime.
Display IDs are host-local positive integers, independent of desktop IDs.
Desktop monitor reconnection invalidates desktop associations and actions, but
archives bounded display text. No retained history survives daemon restart.

Negotiated `sync_begin` adds `grouped: {"session": N}`. Its committed cache is
the newest retained records, in oldest-to-newest order, capped by the host's
32-record retention limit, configured cache limit, and device capacity.
`overflow` counts retained records outside that cache, never evicted records.
Absence of `grouped` selects legacy behavior. Invalid grouped metadata rejects
the entire staged transaction. A changed session clears selection and local
hidden IDs and returns Home; ordinary same-session sync preserves selection.
Snapshots do not replay arrivals or renew automatic attention. Cold boot starts
at Home even if the host still has an active presentation.

Grouped `notify` and `close` deltas add `session`; mismatched or missing session
is ignored in grouped mode. A replacement moves its record to newest order.
Local selection and copied text remain stable during a captured gesture.
New normal arrivals do not interrupt manual browsing. Critical attention waits
until the gesture and animation finish, with identity revalidation.

## Presentation

Attention is separate from record storage. After a normal 300 ms coalescing
window (critical attention bypasses it), the host sends:

```json
{"t":"present","session":123,"generation":7,"id":42,"remaining_ms":10000,"urgency":1}
```

`generation` is positive 31-bit, monotonically increasing within `session`.
`id` must name a cached, non-hidden record. `remaining_ms` is an integer from
-1 through 2147483647: -1 is persistent, 0 ends this presentation, positive is
its remaining lease. Time spent waiting for a gesture or transport is deducted.
Same-generation messages may shorten a lease, never extend it. Older
generations cannot move focus or end newer presentations. Full sync carries
no new presentation. The host cancels attention on link loss; reconnect restores
records at Home on cold boot and preserves valid local focus on ordinary sync.

Normal fallback is 10 seconds. Positive application timeouts are honored,
-1 uses configured fallback, 0 is persistent; critical fallback remains
separately configurable. Desktop `NotificationClosed` reason 1 completes
attention and retains the record. Reasons 2/3/other explicit close remove it.
Host fallback expiry retains text in grouped mode; legacy peers still receive
`close`. Native device lease expiry returns Home without deleting records.

Any deliberate navigation cancels automatic return and enters manual mode.
The device sends `{"t":"input","action":"browse","session":123,
"generation":7,"group":"notifications"}` (generation 0 before any visit).
Host accepts only the current session and the input generation expected for
this device boot, then clears its presentation. The expected input generation
starts at 0 on a new link or changed boot ID and advances when a presentation
is successfully sent. The daemon's generation counter remains monotonic;
cold-boot sync neither replays attention nor requires the device to know an
earlier boot's generation. Ordinary same-boot sync preserves the expected
generation. The UI remains manually browsable with no timer. Browsing back
to `group: "home"` also cancels the lease and permits subsequent normal arrivals.
Normal arrivals update the retained collection without stealing focus while
manual; a fresh critical presentation may take focus after settling.

## Removal and finite metadata

`×` hides locally immediately and sends `input/dismiss` with `id`, `session`,
and current `generation`. The host removes this retained record and echoes a
session-scoped `close`, so sync/replug cannot restore it. Local mode leaves the
desktop notification alone; propagate mode invokes desktop close only for a
currently valid association. Replacement of the same still-active desktop
notification can reintroduce its display card. No action targets archived or
reused desktop identity after monitor reset.

Retention is 32 records; oldest update order is evicted, with no desktop action.
Source outbox, deadline/correlation/association/suppression metadata and pending
Notify replies are individually bounded (32 retained associations, at most 64
pending replies; bounded monitor event queue 256). Eviction releases metadata;
late replies for forgotten records do not recreate associations. A queue
overflow resets monitor correlation and attention, archives surviving records,
and establishes a fresh monitor connection. Removal of the selected/captured
record cancels the gesture and chooses a surviving record or Home.

## Readback and ordering

`cards_status` keeps existing cache/deck fields and adds `grouped`:
`{enabled,session,group,manual,generation,present_id,remaining_ms}`.
`group` is `home` or `notifications`; `present_id` is null if no automatic visit.
The readback describes published UI state; it is not a panel capture.
Immediately after a cache mutation, the UI may still describe the previous
frame. If its IDs/counts contradict the committed cache, `cards_status` emits
the valid cache fields with `view_pending: true` and omits `deck`/`grouped`.
A later query after the UI tick includes a coherent published view. Consumers
wait for that view when asserting focus/group; they can use cache-only results
for cache inspection.

All writes use the existing wire lock. A begin/cards/commit sequence remains
atomic against deltas and presentation/input responses. Frames retain the
8192-byte hard line limit and 2048-byte soft chunk target. Invalid staged
metadata leaves the committed cache unchanged and requests one recovery sync.

## Navigation geometry

The left 160 px rail remains fixed. Right-side groups occupy 480 px. Home is
left of Notifications; horizontal navigation is bounded, not circular. Inside
Notifications, vertical navigation is newest-to-oldest and bounded.
Notifications remains reachable even when its cache is empty; it shows a clear empty
state (or the uncached retained count) and a horizontal route back to Home.
Removing the last visible card still returns to Home. There is no vertical
action inside an empty group. One gesture
locks one axis after directional slop; ambiguous diagonal motion cancels.
One whole card snaps per gesture. Body tap is inert; `×` only dismisses on tap,
and a rail-start gesture never acquires a card. Existing renderer and fonts
remain unchanged. Distinct group and card-position cues stay visible.
The close control uses a 64 × 48 px target with a 28 px, 3 px stroke cross;
the header reserves its width, and a drag starting there cancels dismissal
without acquiring navigation.

The native capture comparison selected a 20 px group cue, 120 px foreground
card and 24 px next-title peek, with an 8 px gap (128 px vertical pitch).
At 464 px card width, the existing 22 px title and 20 px body fonts provide
two body lines. A singleton expands to 144 px and three body lines when there
is no uncached-count footer. Neighbor dismiss buttons are hidden. Vertical
commit distance is 48 px; deliberate flick needs 24 px and the existing
recent velocity threshold. Group commit distance remains 96 px / 36 px flick.
The initial geometry passed Starship's short gate on `be39d5e49`. The enlarged
close control subsequently passed its focused Starship tap check on
`604fd70da`; [acceptance evidence](grouped-ui-acceptance.md) records both.
