# Architecture and design decisions

[Project overview](../README.md) · [Wire protocol](protocol.md) · [UI design](ui.md)

This guide records the implemented structure and the reasons for it. User
behavior and configuration are in [behavior](behavior.md) and
[configuration](configuration.md); dated validation is in [STATUS.md](../STATUS.md).

## Responsibilities and ordering

The Linux daemon owns desktop notification identity, retained history,
telemetry sampling, configuration and serial connection lifecycle. Firmware
owns its bounded cache, local clock, rendering, gestures, offline expiry and
[backlight policy](backlight.md). Keeping cards on the board allows browsing
while disconnected without requiring another host round trip per swipe.

The host's model lock covers both mutations and their wire output. Locking
individual writes alone would let a new arrival reach the board before an
older snapshot, then disappear when that snapshot committed. A full sync and
its display-control replay are serialized against incremental updates.
The firmware stages a snapshot separately from committed state and publishes
it atomically. UI gestures copy the card identity and text they capture;
they never keep a pointer into a cache that can be swapped or removed.

History is RAM-only, bounded to the newest 32 cards, with independent monotonic
retention and presentation deadlines. A desktop popup expiring need not erase
recent history. Genuine replacements renew history; browsing, telemetry,
action metadata and reconnect snapshots do not. A daemon restart creates a
new session and empty history. A board reconnect within that session restores
cards without treating the snapshot as a new arrival or restarting retention
and boost deadlines. An unexpired presentation can resume with its remaining
time.

Host implementation is split by responsibility:

| Module | Owns |
| --- | --- |
| [daemon.py](../host/src/status349/daemon.py) | Construction, shared state/locks, worker lifecycle, capability negotiation, sync, IPC and scheduling |
| [daemon_cards.py](../host/src/status349/daemon_cards.py) | Retention, peer text projection, card callbacks and presentation attention |
| [daemon_link.py](../host/src/status349/daemon_link.py) | Pairing, verified session ownership, transport, readback and keepalive |
| [daemon_actions.py](../host/src/status349/daemon_actions.py) | Activation correlation, pending requests and dispatch results |
| [daemon_telemetry.py](../host/src/status349/daemon_telemetry.py) | Serialized source sampling, dashboard smoothing and monitor-power following |

The four implementation mixins are assembled by `Daemon`; they share its
model and locks rather than introducing separate state owners. Cross-worker
operations retain the same lock boundaries, cancellation and shutdown order.
The CLI remains `349d`/`349ctl`, and configuration, pairing and wire identities
are unchanged by this split.

Telemetry has its own one-second cadence. Notification/action wakeups do not
resample it. Slow commands run in background workers; after collection the
next sample is due one tick later, avoiding catch-up bursts. Full sync uses
the latest coherent sample. CPU accounting excludes duplicate Linux guest
time and uses a three-second exponential average. Memory amount and usage
share one `/proc/meminfo` read. Network rates use a two-second counter window
and actual monotonic elapsed time across unique physical default-route
interfaces. Missing/reset counters invalidate the baseline rather than
fabricating a zero, spike or partial total.

## USB session ownership

Pairing stores schema version 1, VID/PID `303a:1001` and the opaque USB serial
in `$XDG_STATE_HOME/349d/device.json`, defaulting to
`~/.local/state/349d/device.json`. A serial is an identity, not a MAC-prefix
classification. Firmware build, boot ID and a transient tty path are not
pairing keys. See [setup](setup.md#serial-discovery) for operator commands.

An unpaired daemon waits for explicit setup. Normal reconnect resolves only
the saved serial; it never scans unrelated boards. A configured port or CLI
override verifies only that target, with no fallback. A valid saved binding
makes pairing idempotent; replacement is explicit. Invalid saved state is an
error, not permission to rediscover. PTYs work for development but cannot
become a saved USB binding without matching USB metadata.

Discovery filters native USB Serial/JTAG metadata, prefers stable `by-id`
paths, deduplicates aliases and rejects ambiguous identities. Linux opens use
exclusive/nonblocking access, advisory ownership and best-effort busy-port
inspection. DTR/RTS are prepared before opening to avoid an unintended reset.
A leading newline terminates a leftover partial application frame. A
compatible hello and fresh challenged pong establish the session; product
strings or old buffered messages alone do not.

The verifier returns the same open handle and any unread bytes. Adoption
publishes the writer and negotiated capabilities under the state lock, then
syncs. Reopening would discard buffered input and risk another reset. One
probe worker runs outside the event loop; cancellation reaps it and closes
late handles before another attempt. Pause, shutdown or a changed target
invalidate stale results. Pairing is rejected while paused.

Pairing commits through a same-directory temporary file and atomic rename.
Verification or persistence failure preserves the prior binding. A
disconnect after successful persistence does not roll back the identity.
Probe reads/writes and active writes are bounded; cleanup drops pending
output before close instead of blocking on a flush. A matching-pong watchdog
detects silent sessions independently of console logs. Relevant code:
[discovery](../host/src/status349/discovery.py),
[pairing](../host/src/status349/pairing.py),
[serial ownership](../host/src/status349/serial_session.py) and
[link lifecycle](../host/src/status349/daemon_link.py).

## Desktop Open identity

Open is an optional explicit default-action invocation through the
[DMS integration](../integrations/dms/349NotificationActions/README.md).
The bridge lives in the owning Quickshell process so it can validate and
invoke the notification object in the same event-loop turn. Body text,
URLs, shell commands and the first arbitrary action are not substitutes for
a default action. The host keeps original desktop metadata separately from
the converted display text.

A binding combines notification-server owner/PID, the `Notify` reply sender
and desktop ID, source/action revision, the exact server object, a binding
token and provider epoch. Numeric desktop IDs alone cannot rebind archived
cards after server restart or ID reuse. Local card IDs are not reused within
a host session. The bridge's released-ID fence distinguishes temporary
revocation from final retirement, preserving valid replacement lineage
without allowing another object to claim an old card.

The ordinary control connection subscribes to owner changes before its
initial identity query; a monitor connection alone does not establish that
subscription on dbus-broker. Either connection failing invalidates the shared
correlation state. Bounded queries retry discovery; late replies and lookups
cannot revive an obsolete owner. Server replacement archives old text and
revokes Open. Fresh notifications may become actionable after discovery
recovers. Provider health and notification-source identity are reported
separately in `349ctl status`; recovery never invokes an action.

Resources are bounded: 32 retained desktop associations/bridge bindings,
64 pending `Notify` replies, a 256-message monitor queue and 64 action pairs
per source notification. Monitor overflow resets correlation rather than
continuing with incomplete identity evidence. Bridge JSON is bounded to
8,192 bytes and IPC to two seconds. One activation is in flight; the host
reserves it and takes manual focus ownership under the model lock, releases
the lock during IPC, then revalidates the result. An ambiguous timeout is
never automatically retried.

The host and bridge retain a request high-water mark and 64 recent results
for the active session/boot. Ordinary USB reconnect preserves that ledger.
Duplicate requests return their recorded result; older requests outside the
window cannot invoke again. Counter exhaustion disables activation rather
than wrapping. Closing a card or losing USB clears pending UI state, but
cannot undo an invocation already accepted by the desktop.

There is one remaining provider boundary: an identical desktop replacement
can arrive before its monitor event. Without a server revision hook, Open
invokes the object's current default action rather than a frozen per-`Notify`
callback. Known revision/field changes are rejected, and a different server
object is never substituted. Dispatch acknowledgement also does not prove
window focus; application and compositor behavior still matter.

## Clock and rendering constraints

Firmware checks RTC oscillator state and BCD/calendar validity before using
external time, with an elapsed-timer fallback and recovery. Retention,
presentation, gesture and boost deadlines use monotonic time so wall-clock
corrections cannot renew them.

LVGL draws synchronously under the display lock into a full PSRAM RGB565
buffer in DIRECT mode. The shared component rotates it into a PSRAM shadow
and stages DMA through internal memory for a full QSPI panel transfer. Moving
a card viewport does not reduce transferred frame bytes. An internal partial
buffer experiment was discarded because faster conversion was largely
offset by slower drawing and consumed scarce internal RAM. Three reused card
widgets avoid per-frame object/framebuffer allocation. Health logs expose
heap and LVGL/link-task stack headroom; native timings do not measure ESP32
or panel performance. Shared implementation:
[display_349](../../../components/display_349/README.md).
