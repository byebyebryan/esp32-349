# Notification Open button — design and execution plan

Approved design, 2026-09-27; implementation and short Snap acceptance completed
2026-09-28 with the local desktop compatibility trial recorded below.
The baseline is the committed grouped UI and its enlarged close control;
see [grouped acceptance](grouped-ui-acceptance.md). This document specifies
behavior; it does not establish an on-device acceptance result.
See the [acceptance record](notification-actions-acceptance.md) for completed
checks, desktop configuration and application-compatibility limits.

On 2026-09-28, the user resumed this loop and moved its remaining rollout and
physical/application checks to **Snap**, where the second board is connected.
Snap uses an isolated preview of the reviewed source while its older dirty
checkout is preserved. Starship's earlier deployment remains historical evidence.

## Intended interaction

Add an explicit **Open** button immediately to the left of **×** on the
foreground notification. Open invokes the notification's desktop default
action. The application decides whether that focuses a window, opens a tab,
or performs another default action.

| Surface | Interaction |
|---|---|
| Card body / ordinary header text | Tap does nothing; horizontal and vertical swipes keep their current navigation behavior |
| Open | Deliberate tap requests the live desktop default action |
| × | Deliberate tap removes this device card using the existing local/propagate dismissal setting |
| Either button, including disabled Open | A drag cancels the button; it cannot become navigation or the other button's action |
| Neighbor peek | Existing navigation; no Open or × control |
| Left rail | Existing persistent information; cannot acquire a card action |

Open is available only for a currently live desktop notification with an
explicit action key `default`. Do not select the first arbitrary action:
it could be Reply, Delete, or another operation. Initial scope is one default
action, without an action menu, inline reply, or a generic launch/focus fallback.
Applications without that action remain readable and dismissible.

The default key convention is defined by the
[desktop notification specification](https://specifications.freedesktop.org/notification/latest-single/).
This means availability depends on what Codex CLI, Claude Code, OpenCode,
Chrome/Slack and Calendar actually send; their compatibility is not yet tested.

## Geometry and visual states

Keep the accepted 160 px rail, 480 px group area and 464 px card width.
Both controls use the physically accepted **64 × 48 px** target size, with
an **8 px noninteractive gap**. Keep × at its current location. Use the word
**Open** in the existing 20 px font; retain the 28 px vector cross for ×.
Open has an accent outline/background; × keeps its neutral treatment.

Coordinates below are local to the foreground card:

| Element | x | y | Width | Height |
|---|---:|---:|---:|---:|
| App and title labels | 12 | Existing rows | 304 | Existing label heights |
| Open target | 328 | 0 | 64 | 48 |
| Gap between controls | 392 | 0 | 8 | 48 |
| × target | 400 | 0 | 64 | 48 |
| Body | 12 | 52 | 440 | Existing two/three-line budget |

```text
┌───────────────────────────────────────────────────────┐
│ APP                             ┌──────┐  ┌──────┐    │
│ Notification title…             │ Open │  │  ×   │    │
│                                 └──────┘  └──────┘    │
│ Body keeps the full existing width and line count.     │
│ 2 / 5                                                 │
└───────────────────────────────────────────────────────┘
```

This reserves 72 additional header pixels, reducing its text width from
376 to 304 px. Validate long English titles and CJK with production-font
native captures before flashing. Use ellipsis rather than smaller text.

When the extension is negotiated, keep the Open slot present on all foreground
cards so availability changes cannot move controls or reflow the header.
History-only cards show a muted, disabled Open. Disabled targets still own
their initial press. Neighbor cards keep full-width preview text and hide
both controls. Older peers keep their existing header geometry.

| Open state | Visual / feedback | Behavior |
|---|---|---|
| Ready | Accent Open | Tap once to request |
| Pending | Muted button with `…` | Suppress repeated taps; × remains usable |
| Dispatch accepted | Brief `Sent` beside the existing position counter | Do not claim that a window gained focus |
| Closed / no default / provider unavailable | Muted Open | Inert; body navigation and × still work |
| Definitive failure | Brief `Unavailable` or `Try again` beside the counter | Refresh availability before enabling another request |
| Response deadline reached | Brief `No confirmation` | Outcome is unknown; never resend automatically |

Feedback expires after two seconds. It uses the existing metadata row, without
covering body text or adding a modal. Feedback and pending state are keyed to
the requested card/revision, so a late result cannot label another card.
While a request is in flight, Open is temporarily disabled on any card reached
by browsing; navigation and × remain usable. The device response deadline is
three seconds, allowing the host's two-second IPC deadline to return a result.

A successful request does not itself remove the device card or return Home.
Cancel its automatic presentation lease as a deliberate interaction and keep
the current manual view. If invocation closes the desktop notification, use
the existing close handling to remove it. Resident notifications can remain.

## What the current desktop supports

Baseline read-only inspection on Starship, 2026-09-27, before this implementation:

- Installed packages are DMS `1.6.2-1` and Quickshell `0.3.1-1`.
- Running `dms ipc --help` exposes notification-center, dismissal and DND
  operations, with no arbitrary notification-action invocation command.
- Running `NotificationService.qml` owns tracked notification wrappers in
  `allWrappers`. Its popup timer hides a popup without necessarily closing
  the notification. Its persisted history text is not an invocable object.
- Running popup code calls `actions[0].invoke()` and then dismisses the
  notification. The proposed feature selects `default` explicitly and leaves
  dismissal to the actual invocation/server lifecycle.
- The baseline host's `sources/notifications.py` discards the incoming action
  list. Its desktop ID association supports close propagation, not Open.

Quickshell exposes an invocation method on the server-owned action object;
nonresident invocation can close the notification. See the pinned
[0.3.1 implementation](https://github.com/quickshell-mirror/quickshell/blob/v0.3.1/src/services/notifications/notification.cpp).
An accepted invocation is not proof of application focus. In particular,
Wayland activation-token behavior must be checked with a real target app.

## Architecture

```text
ESP32 Open tap
    → input/activate over the existing USB link
    → 349d validates session, card, revision and request identity
    → small DMS daemon plugin validates the live object/default action
    → that object's NotificationAction.invoke()
    → desktop application receives its default-action callback

349d → action_result and availability updates → ESP32
```

Add a narrowly scoped DMS daemon plugin under the
`integrations/dms/349NotificationActions/` directory. It is instantiated inside
the notification-owning Quickshell process and registers a dedicated IPC
target `349-notification-actions`. Do not create a second
notification server or patch DMS's extracted runtime files.

The implementation uses plugin ID `status349NotificationActions` and target
`349-notification-actions`; see its [API and installation notes](../integrations/dms/349NotificationActions/README.md).
The `release` payload includes `final`: temporary revocation preserves the
exact object/source-version lineage for safe rebind, while final removal
retires that local ID. A scalar released-ID fence relies on the host's
monotonically allocated, nonreused local IDs; it prevents late binds after
bounded lineage metadata has been released.

Use versioned `status`, `bind`, `activate` and `release` operations. Bind only
the host's retained associations, at most 32. The plugin assigns an epoch on
creation and an opaque binding token tied to a tracked object and default
action. `activate` validates the binding and calls `invoke()` in one QML
event-loop turn. It rejects an object that closed, was dropped, or was replaced
by a different object, rather than looking up a new target by reused numeric ID.

The host calls IPC asynchronously with argument arrays, bounded JSON input/
output and a two-second deadline. No shell interpolation, device-supplied
desktop IDs, arbitrary action keys, commands or URLs. A small bridge client
keeps this provider separate from notification monitoring and is disabled
until explicitly configured. Record provider health in `349ctl status`.
Cap each bridge message at 8192 encoded bytes and action parsing at 64 pairs.
Oversized or malformed input leaves Open unavailable while preserving the
ordinary display text. Retain bounded binding/validation metadata only; do
not add an unbounded copy of original bodies, hints or image data.
Plugin installation/configuration is a separate rollout checkpoint, including
chezmoi tracking if that is how the host configuration is maintained.

### Binding and replacement boundary

Keep desktop identity as server owner + desktop ID + live object binding.
Keep a separate host action revision; presentation generations do not identify
actions. Invalidate a binding immediately when the monitor observes any Notify
replacement, even if its text and action list are identical. Bind again only
after its assigned ID is confirmed and the bridge's live object matches the
untruncated source metadata. Display-normalized/truncated text is not identity.

The plugin must revalidate the current default action and expected live fields
at dispatch, and invalidate on object/property/action changes. Use the same
object for validation and invocation, with no deferred numeric-ID lookup.
An unknown mapping stays unavailable. A stale bind reply cannot enable a newer
host revision, and availability updates never reorder or re-present a card.

There is a concrete API limit to test first: Quickshell 0.3.1 updates a
replacement in place; identical properties need not emit a change signal, and
its new-notification signal is only emitted for newly allocated objects.
See the pinned [server implementation](https://github.com/quickshell-mirror/quickshell/blob/v0.3.1/src/services/notifications/server.cpp).
Also, the notification-received signal illustrated in
[DMS's daemon guide](https://github.com/AvengeMedia/DankMaterialShell/blob/master/.agents/skills/dms-plugin-dev/references/daemon-plugin-guide.md) is
absent from the inspected running service. Do not rely on either signal to
count every replacement. Observe actual list/property changes and reconcile
the bounded host bindings periodically, at most once per second while in use.

The initial feature means **invoke the current default action of this live
desktop notification at dispatch**. It cannot promise a frozen callback from
an earlier Notify call: an identical replacement may reach the server before
the host's monitor observes it. Reject known revision changes and visible
field mismatches. If a strict per-Notify atomic revision is required, that needs
a server revision hook; stop and revise the contract rather than claiming a
plugin token supplies that missing guarantee. This boundary does not permit
rebinding an old card to a different server/object after a restart or close.

## Lifetime rules

| Event | Retained card | Open eligibility |
|---|---|---|
| Device/host presentation timeout | Retained; automatic visit returns Home | Unchanged if the desktop object remains live |
| Desktop popup disappears without closing the object | Retained | Still eligible if its default action is live |
| Desktop `NotificationClosed` reason 1 | Retained history text | Disabled immediately |
| Desktop explicit close / object dropped | Existing removal behavior | Binding revoked immediately |
| Local × or retention eviction | Removed locally | Release its binding; cancel unsent requests |
| Replacement | Existing updated/newest record behavior | New action revision and fresh binding; old press/request rejected if observed |
| Monitor reset / overflow | Existing bounded archive behavior | Revoke all bindings; archived identities cannot become live again by numeric ID |
| DMS/plugin restart | Existing text can remain | Revoke the previous epoch; only fresh proven associations can bind |
| USB disconnect / cold boot | Existing recovery behavior | Drop unsent/pending device UI state; no action replay on reconnect |
| Daemon restart | Existing RAM history reset | New host session; reject all prior action requests |

Action dispatch already accepted by DMS cannot be undone by a subsequent × or
disconnect. Cancelling a pending UI indication must not report that the action
was cancelled. Successful dispatch may synchronously close the notification
before its result reaches the device; that ordering is valid.

## Protocol extension

Negotiate `notification-actions-v1` only alongside the existing grouped/cache
capabilities. Firmware advertises implementation support; host `sync_begin`
adds `actions: {"enabled": true}` to opt into this contract. This negotiation
is distinct from runtime bridge health. With the bridge unavailable, negotiated
cards remain disabled. Without negotiation, existing behavior/geometry applies.
When the feature is disabled by configuration, the host does not negotiate it.

Each cached/updated card adds:

```json
"open": {"rev": 12, "state": "ready"}
```

`rev` is a positive 31-bit host action revision. `state` is `ready` or
`unavailable`; changing eligibility or binding advances the revision, including
revocation. An unchanged periodic reconciliation is a no-op. A separate
session-scoped delta changes metadata without changing text/order/attention:

```json
{"t":"card_action","session":123,"id":42,"open":{"rev":13,"state":"unavailable"}}
```

On release of a valid Open tap:

```json
{"t":"input","action":"activate","session":123,"boot_id":456,"id":42,"open_rev":12,"request":7}
```

Host response:

```json
{"t":"action_result","session":123,"boot_id":456,"id":42,"open_rev":12,"request":7,"status":"dispatched"}
```

Result statuses: `dispatched`, `unavailable`, `stale`, `failed`, `unknown`.
`dispatched` means the bridge invoked the action, not that focus was confirmed.
`unknown` includes a deadline after invocation may have happened. Failures are
not successful acknowledgements. Result matching requires all identity fields.

Use the existing nonzero device boot ID (uint32) and a monotonically increasing
positive 31-bit request counter per boot. Session/card/revision use existing
31-bit conventions; reject booleans, malformed/range-invalid values and wrong
sessions/boots. Do not gate activation on presentation generation: an unrelated
arrival must not invalidate the action's identity.

Permit one action request in flight per connected device. Reserve its request
ID and enter manual browsing/end its presentation under the host state lock
before dispatch; do not depend on a separately delivered browse message to
cancel attention. Release that lock before awaiting bridge IPC so monitoring,
closes and heartbeat can continue. Revalidate before accepting a bind/result.
Keep a high-water mark and up to 64 recent results in host
and bridge for the active session/boot. A duplicate returns its recorded result;
an older request outside that result window is rejected without invoking again.
Keep the ledger across ordinary reconnect/full sync for the same device boot.
Bind deduplication to the bridge epoch; no retry crosses provider restart.
Exhausted counters disable activation pending a fresh identity rather than wrap.
No automatic retry or replay after timeout, sync or reconnect. A new deliberate
tap after refreshed availability may be another request; suppress rapid repeats
while pending and for a 500 ms cooldown after a terminal response.

Include metadata in atomic chunked sync, respecting existing line and capacity
limits. Unknown card deltas cannot create records. Stage-invalid metadata leaves
the committed cache unchanged. Late sync/delta/result messages cannot restore
revoked availability or a dismissed record. No desktop action metadata is
persisted to disk. Exact field names freeze at the host/protocol checkpoint.

## Gesture ownership

Extend the existing `group_input`/`deck_input` policy with explicit captured
control ownership: none, Open, or dismiss. Do not attach an independent
`CLICKED` handler that bypasses drag cancellation or generation validation.

- Capture foreground ID and button bounds at press. Open also captures its
  action revision; × remains usable independently of Open eligibility changes.
- Activate only on release in that same enabled target, within existing tap
  slop, with no intervening cancellation or identity change.
- Crossing between controls, leaving bounds, exceeding slop, input reset,
  card removal or a stale link cancels; a changed action revision also cancels
  Open. Returning inside does not re-arm. A body-start drag ending over a
  button cannot activate it.
- A disabled/pending button consumes its press without navigating. The gap is
  noninteractive and never expands either button's hit target.
- Any successful Open tap cancels the automatic lease through existing manual
  takeover; retain deferred-critical handling and copied-text capture behavior.
- Availability and feedback changes must not move the held card. Revalidate
  independently in firmware, host and bridge before each boundary accepts it.

## Execution checkpoints

| Checkpoint | Main files / responsibility | Exit gate |
|---|---|---|
| 1. Prove the desktop bridge | New `integrations/dms/349NotificationActions/`, controlled desktop test provider, bridge API notes | Default action callback works through server-owned invocation; no-default, close, replacement (including identical text), plugin reload and binding invalidation tested; real app focus result recorded separately |
| 2. Add host metadata and requests | `host/src/status349/sources/notifications.py`, a new bridge client, `state.py`, `proto.py`, `daemon.py`, config and host tests | Bounded metadata, revision/lifetime rules, asynchronous dispatch and dedup pass; old peers unchanged; exact wire contract frozen |
| 3. Add controls and native coverage | `main/ui_deck.c`, group/deck input, `proto.c`, `state.c/.h`, `tools/native_ui/` and composed fixtures | Production-font captures and real-LVGL pointer replay pass; parser/state + host composed races pass in Debug/Release; firmware builds |
| 4. Snap rollout and brief acceptance (target changed 2026-09-28) | Plugin/config install instructions, trace recorder and acceptance document | One short visual/touch/default-action check with trace plus actual desktop callback/focus evidence; normal mirroring restored |

First checkpoint resolves the provider dependency before UI plumbing. Use a
controlled notification producer that records received `ActionInvoked` and
can replace/close its own notification. A fake IPC success does not establish
real invocation. Install/reload the plugin and send controlled test
notifications at the execution checkpoint, after reviewing the concrete
provider and its tests.

For application compatibility, record whether each source offers `default`
and whether invoking it actually opens/focuses the expected destination. Start
with a real Chrome Slack/Calendar notification and inspect one CLI notification
source. Invoke the CLI default only if it exists; otherwise record that source
as unavailable without blocking the feature on a missing application action.
Do not infer CLI support from its app name.
Missing actions are a documented unavailable case; activation-token or app
callback failures are a separate integration gap, not firmware success.

### Automated acceptance

Reuse the existing native production UI/parser/state and composed host tooling;
no new ESP32/display emulator is needed for this feature.

- Geometry/captures: ready, disabled and pending states; one/many cards;
  long English/CJK headers; × size/position unchanged; full body width;
  invisible neighbor controls; legacy baseline unchanged.
- Pointer replay: both target centers and edges; gap; body tap; both swipe
  axes; button-start drag; body-start drag ending on a button; crossed controls;
  leave-and-return; disabled target; repeated tap; input reset/stale link.
- Lifecycle replay: replacement or close between press and release; pending
  response after navigation/removal; presentation expiry with a live action;
  genuine desktop expiry; arrival/critical/full-sync while pressed; DMS/monitor
  reset; late bind/availability replies; result after synchronous close.
- Host/bridge contract: malformed action arrays (only a valid, unique `default`
  is eligible); server ID reuse across epochs; unsupported/missing provider;
  wrong session/boot/revision; duplicate, old and exhausted request IDs;
  failure/timeout; bounded burst/eviction; unavailable changes during sync;
  mixed old/new host and firmware capabilities.
- Integration: controlled producer receives exactly one default callback per
  accepted request. Repeated/late requests never target another object. Keep
  action dispatch evidence separate from compositor/app focus observations.

### One short physical check

Prepare a few isolated, nonexpiring cards and complete the automated cases
before asking the user to look. Reserve approximately two to three minutes:

1. Confirm Open and × are readable, separate and easy to hit on the actual
   display. Try one swipe/body tap to confirm no accidental activation.
2. Tap Open on the controlled live notification, then on a chosen real app's
   actionable notification; record device input, host/bridge dispatch and app
   callback, plus whether the expected window/tab focuses.
3. Check one disabled history/no-action card; tap × on a remaining card and
   confirm normal navigation and idle recovery.

Automate replacements, held gestures, retries and restart races. No repeated
hold/inject choreography, rendering retuning or soak gate is part of this goal.
Restore normal mirroring and record actual source/build/provider versions.

## Completion boundary

Ship when the dedicated controls work, dispatch is bound to a live default
action with the documented replacement semantics, automated gates pass, and
Snap's short check confirms touch usability and an actual desktop action
(the user moved the target from Starship on 2026-09-28).
State which real apps were tested and which remain unverified.
This plan does not add generic app launching, arbitrary action buttons, new
fonts, renderer optimization or additional dashboard groups.
