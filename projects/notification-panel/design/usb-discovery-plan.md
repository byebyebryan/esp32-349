# Persistent USB pairing for the 349 display

Implementation and acceptance plan, 2026-10-02. The host implementation now
passes automated acceptance. Deployment and physical gates are recorded
separately; the plan below remains the behavior and ownership contract.
The [acceptance record](usb-pairing-acceptance.md) records deployed revisions,
observed timings and the remaining physical gates.

During setup, the host should discover a compatible 349 and save its stable
USB identity. Subsequent boots and reconnects should open only that paired
board, including recovery after a USB hub power cycle. The selected scope is
one 349 per host, using the existing USB Serial/JTAG transport and protocol.
Pairing is persistent host state; a hello and ping/pong exchange on each
connection verifies responsiveness and negotiates capabilities without
repeating pairing. The currently tested firmware supports this exchange.

## Evidence and current behavior

The [README validation record](../README.md#serial-discovery) establishes that
both 349s answer hello and a fresh ping/pong exchange in about 2 ms. Three
consecutive probe opens on each retained the same boot ID with DTR and RTS
asserted. Starship's RLCD did not answer the protocol and continued its
benchmark. Discarding pending output before closing avoided the roughly
30-second cleanup stall observed after its write timeout.

Before this implementation, `link.py` selected the first matching by-id path.
The daemon published its writer and active port before hello and deliberately
reset the device. The verified-session path now publishes a writer only after
identification and reuses the existing capability negotiation and full sync.
`link.py` retains the deliberately resetting direct diagnostic path.

PySerial-asyncio flushes output in its connection-loss callback. Wrapping
`wait_closed()` in an asyncio timeout cannot interrupt a synchronous flush
that blocks the event loop. Teardown must prevent that blocking operation.

USB metadata checked on Snap and Starship shows VID/PID `303a:1001` and the
same generic USB product on both 349s and the RLCD. This narrows initial
discovery to Espressif USB Serial/JTAG interfaces, but the handshake must
identify the compatible display. Enumerating metadata does not send commands
to the devices being listed.

## Intended behavior

- With `link.port` omitted and a pairing record present, locate its exact USB
  serial through metadata and open only that device. Its current tty number
  and resolved path may change without changing the binding.
- With neither a pairing record nor an explicit target, keep the daemon
  running and report that setup requires `349ctl pair`. Do not start a
  background scan that opens unpaired boards.
- `349ctl pair` performs initial discovery, verifies the compatible display,
  persists its USB identity, and adopts the verified connection. A valid
  existing binding makes this command idempotent; it does not rescan.
- `349ctl pair --replace` explicitly searches for a replacement and commits
  its identity only after verification and successful persistence. Failure
  or cancellation before that commit preserves the previous binding.
- With a configured port or daemon `--port` override, probe only that target.
  A missing, occupied or incompatible target stays disconnected; it does
  not fall back to a different device. CLI overrides keep their precedence
  across reloads and do not silently overwrite saved pairing state. A setup
  command respects the effective override, allowing existing pins to seed
  pairing after their device identity is verified.
- If the paired board is absent, busy or incompatible, wait and retry that
  board. Do not probe the RLCD or automatically switch to another 349.
- Startup, reconnect and resume use the same nonresetting handshake against
  the selected target. After verification, retain the open connection and
  sync the host's retained cards and telemetry through existing negotiation.
- While probing, `status.link` is false and `active_port` is null. Publish a
  live writer only after hello and the challenge response are validated.
  Status also exposes pairing state, the saved serial and setup failures.
- Pairing commands are rejected while the daemon is paused. They do not
  implicitly resume it or compete with a flashing operation.
- Pause, shutdown and a changed target cancel pending work, release any open
  candidate, and prevent a stale attempt from becoming active or replacing
  the saved binding.

Multiple simultaneous 349s are outside the automatic-selection contract;
an explicit port remains available to select one. No device-side pairing
state, firmware change or new configuration knobs are required.

## Persistent pairing state

Store a versioned record at `$XDG_STATE_HOME/349d/device.json`, falling back
to `~/.local/state/349d/device.json`. The record contains schema version 1,
VID `303a`, PID `1001`, and the USB serial obtained from metadata after the
handshake succeeds. Store the serial as a stable opaque identifier; do not
use its prefix, the tty number, firmware version or changing boot ID as the
binding. No manually copied serial or firmware pairing token is needed.

Write the record atomically with a temporary file in the same directory and
rename, using normal user-owned state permissions. Do not expose an
unpersisted successful setup as a completed pairing. Failed persistence
closes the candidate and preserves the previous binding; a truncated,
invalid or unsupported record reports a setup error rather than triggering
automatic discovery. An explicit replacement command can repair that state.
The atomic replacement is the commit point. A disconnect or shutdown after
that point keeps the new valid record for the next reconnect; it does not
silently restore the older identity.

The daemon owns pairing writes and serial connections. Send setup commands
through its existing IPC interface, with one pairing operation at a time.
Keep the operation asynchronous and let the CLI observe completion through
status so a scan across several candidates cannot block ordinary IPC or
silently exceed the current request timeout. Repeated requests must not
start overlapping scans. Recheck the target generation before committing
the record and adopting the connection.

When an explicit target is already connected and verified, initial pairing
can reuse that session and its current USB metadata, confirming a fresh pong
before saving the identity. Do not reopen a tty already owned by the daemon
or send a discovery hello that could interrupt an in-progress sync. An
explicit replacement scan releases the daemon's current session first;
failure reconnects through the unchanged old binding or explicit override.

Pairing requires a stable USB serial. Explicit PTY paths remain usable for
daemon development, but a path alone cannot become a persistent pairing.
Tests of pairing supply USB metadata separately from the PTY protocol.

## Candidate selection and identification

1. During an explicit setup scan, enumerate serial interfaces whose actual
   USB metadata matches VID/PID `303a:1001` and the existing USB Serial/JTAG
   interface. Prefer stable by-id paths and deduplicate aliases by resolved
   device path. During normal operation, filter metadata further by the
   saved exact serial before opening any port. Duplicate matches for a
   saved identity report ambiguity rather than choosing arbitrarily.
2. Check for an existing port owner before opening. Use a nonblocking
   exclusive acquisition and Linux tty exclusivity for the lifetime of the
   connection. PySerial's advisory lock alone does not identify every other
   serial client; process-owner checks are best effort and must not be
   represented as a universal protection against noncooperating clients.
3. Preconfigure DTR and RTS asserted before opening, with bounded serial
   reads and writes. Never call the reset helper during discovery.
4. Send a newline followed by one newline-terminated `@349 {"t":"hello"}`.
   The initial newline ends any unfinished application frame left by a prior
   disconnect. Ignore ordinary logs and bounded malformed input while waiting
   within the attempt deadline.
5. Require an object with `t = "hello"`, an integer protocol version equal to
   1, a firmware string, and a list of string capabilities containing `link`
   and `bar`. Reject booleans masquerading as integers and echoed requests.
   Do not require newer optional capabilities or a boot ID, so older
   compatible peers retain their existing fallback behavior.
6. Send a fresh positive 31-bit numeric ping challenge. Accept only a pong
   echoing that challenge after it was sent. Silence, stale pong, wrong
   challenge and incompatible hello reject the candidate.
7. Return the open handle, validated hello, USB identity and any remaining
   received bytes together. For setup, persist the identity before reporting
   success. Adopt that exact connection; do not close, reopen, reset or send
   a second discovery hello after success.

Reuse the existing capability and full-sync logic during adoption. Refactor
hello handling enough to install the writer and negotiated capabilities
together under the existing state lock, then send one initial full sync.
Background telemetry must not race ahead using uninitialized capabilities.
Preserve bytes read past the handshake so boot logs and later frames are
not lost. Use the hello validator for subsequent hello frames as well; an
incompatible hello must not renegotiate an active link.

## Timing and resource ownership

Initial constants, with cold-start hardware acceptance recorded separately:

| Operation | Proposed bound or cadence |
| --- | --- |
| Serial write during discovery | 0.5 seconds, capped by remaining attempt time |
| Hello and ping/pong together | 2 seconds per candidate |
| Probe read cancellation polling | At most 50 ms between reads |
| Cleanup completion target | 0.5 seconds after the probe worker stops |
| Waiting for the saved USB serial to appear | Existing 0.5-second metadata scan cadence |
| Retrying a failed handshake with the selected target | Existing bounded reconnect backoff |
| Active keepalive | Existing 4-second ping cadence |
| Active serial write | 2 seconds |
| Silent active link | Disconnect after 12 seconds without a matching keepalive pong |

An explicit setup command takes a bounded snapshot of eligible candidates
and attempts each physical device at most once, continuing past a silent
RLCD. An unsuccessful scan finishes with a useful error; another setup
command can retry after devices finish booting or become available. Normal
operation never repeats this broad scan. It waits for the saved serial and
uses existing reconnect backoff for handshake failures with that target.

Use one bounded probe worker at a time for serial open and synchronous
identification, keeping those operations off the daemon event loop. Reads,
writes and the complete attempt use monotonic deadlines. A cancellation
signal makes the worker close its candidate and finish; cancelling the
coroutine alone does not stop a Python worker thread. Reap the worker and
dispose of any late successful result before starting another attempt.

A single internal serial-session helper owns rejection, cancellation and
disconnect cleanup. Stop future writes, discard pending transport and serial
output, then close and release exclusivity. The adopted async connection
must use cleanup that avoids a blocking flush in the event-loop callback.
Do not fork the dependency or rely solely on a timeout around its current
`wait_closed()` behavior. Exercise this helper with stalled output in tests.

Bound active writes too, so a verified device that stops consuming data
cannot hold the wire lock indefinitely. The keepalive watchdog tracks fresh
pong responses, rather than treating debug logs as proof of a working
command parser. Disconnect on expired liveness and reconnect to the saved
identity or effective override, without opening other candidates.

## Implementation checkpoints

1. Add reusable hello validation in `host/src/status349/proto.py`, metadata
   filtering and serial-session ownership in `serial_session.py`, and a focused
   discovery helper in `host/src/status349/discovery.py`. Establish bounded
   probe and cleanup tests before changing the daemon.
2. Add pairing record validation and atomic persistence in
   `host/src/status349/pairing.py`. Resolve the saved serial through metadata
   without probing other devices. Test restart persistence, failed writes,
   corrupt records and replacement preserving the old binding on failure.
3. Integrate selected-target handshakes and asynchronous setup operations
   into `daemon.py`; add `pair` and `pair --replace` to `__main__.py` through
   IPC. Delay publication of the writer, process the validated hello once,
   preserve read buffering, and centralize release paths. Keep generations,
   pause wakeups and reconnect settings effective while work is in flight.
   Add write deadlines and keepalive expiry.
4. Update `fake.py` to provide a complete default device hello and fresh pong
   replies. Preserve explicitly supplied malformed fixtures for rejection
   tests, and update older peer fixtures with the common mandatory fields.
   Extend reload, lifecycle, pairing IPC and fake-device integration coverage.
5. Document initial setup, saved identity, replacement and overrides in the
   README. Record actual connection and recovery timings and the deployed
   revision in a separate acceptance record after physical validation.

Direct `349ctl --port` diagnostics keep their existing explicit target and
deliberate reset behavior in this pass. Their transport helper must not be
used by discovery. The CLI addition is limited to host pairing and its status.
USB descriptor changes, a firmware model field, device-side pairing and
additional board transports are outside this scope.

## Automated acceptance

| Scenario | Required result |
| --- | --- |
| Unrelated USB device | Excluded by metadata; never opened or written |
| Wrong Espressif device sorts before the 349 during setup | Wrong device rejected; compatible device paired |
| Alias paths point to the same tty | Physical candidate attempted once per setup scan |
| Occupied setup candidate | Skip without writing; another setup command can retry |
| Paired record survives daemon and host restart | Same serial selected without probing other boards |
| Tty number or by-id alias changes | Resolve the same saved USB serial |
| Paired 349 absent while another compatible 349 is attached | Remain disconnected; do not switch or rewrite the record |
| No pairing record and no override | Report unpaired; no ports opened |
| Invalid, truncated or unsupported pairing record | Visible setup error; no automatic scan or overwrite |
| Pair called with an existing valid record | Return existing binding without rescanning |
| Replacement, failed persistence or cancellation before commit | Old binding preserved until a new record is committed |
| Disconnect or shutdown after replacement commit | New valid binding retained for reconnect |
| Pairing an already connected explicit target | Reuse verified session; no reopen, reset or interleaved hello |
| Pairing while paused | Reject command without touching serial devices |
| Concurrent setup requests or config change during pairing | One operation; no stale adoption or stale record write |
| Setup selected device has no stable serial | Clear failure; no path-only record persisted |
| Logs, malformed hello, wrong protocol, echo or stale pong | No active writer or application frames installed |
| Silent device or stalled writes | Bounded rejection; event loop and IPC remain responsive |
| Pending output during cleanup | No blocking flush, leaked handle or surviving probe worker |
| Fragmented hello and buffered trailing data | Correct validation and no lost trailing frames |
| Config target changes during probe or adoption | Old target disposed; only current target can become active |
| Pause or shutdown during any stage | Candidate released; no late adoption or orphan reader |
| Only reconnect timing changes | Current valid link retained; retry policy updated |
| Explicit incompatible or missing target | No automatic fallback |
| Reconnect to a still-running 349 | No reset; capabilities renegotiated and retained cards synced |
| No pong despite continuing debug logs | Watchdog disconnects and only the selected target is retried |
| Legacy compatible capability set | Existing legacy snapshot behavior preserved |

Use fake serial objects for control-line and cleanup assertions, PTY devices
with injected USB metadata for complete daemon behavior, and isolated
`XDG_STATE_HOME` directories for pairing tests. Test isolation must cover
persistent state as well as the existing runtime sockets and pause flags.
PTYs do not prove USB reset behavior; that remains a physical acceptance
check. Run focused discovery and pairing tests, then the existing
`host/tests` and `tools/tests` suite. Current CI covers Python 3.11 and 3.14.
No font or firmware rebuild is required for this change.

## Physical acceptance and rollout

After automated checks, review, commit and push the candidate implementation.
Sync that exact revision to Starship and first validate the existing explicit
target. Back up its port override before changing it. Use `349ctl pair` to
seed the persistent identity from that verified explicit target.

Remove the override only after the pairing record is verified. Restart the
daemon and confirm that it opens the saved serial without probing the RLCD.
Verify startup while the board boots, pause/resume, unplug/replug, and removal
of the 349 while the RLCD remains. The daemon must stay responsive and
disconnected in that last case, then recover automatically when the paired
349 returns. Confirm stable 349 boot IDs for host reconnects and an
undisturbed RLCD benchmark. Repeat setup and paired operation on Snap after
Starship passes.

Exercise an explicit filtered replacement scan with both boards attached,
including the wrong-device-first case. Also attempt replacement with only
the RLCD present: it must finish without changing the saved 349 identity.
Reconnect the original 349 and confirm normal recovery. These scans are
explicit setup tests; subsequent ordinary reconnects must not probe the RLCD.

The final hardware check is the original failure case: turn the Starship
hub off and back on, allow USB enumeration order to change, and confirm that
the daemon reacquires its saved 349 without re-pairing, a manual reload or
opening the RLCD. Record this physical check separately from simulated
reconnect tests. Confirm persistence across a host reboot when that check is
available; a daemon restart alone does not establish physical reboot recovery.

Record physical acceptance separately from automated results; fixes repeat
the checks relevant to their changes. Loading changed daemon code requires
a service restart, which clears its RAM-only notification history.
Subsequent USB reconnects must preserve the daemon's remaining notification
ages and retained records. Compare deployed host files, saved identity and
daemon status after rollout.

Rollback restores the previous host revision and backed-up port overrides,
then reloads that daemon code. Preserve the pairing record; the older code
does not use it. Board firmware remains compatible throughout.

## Automated results

The complete `host/tests` and `tools/tests` gate passed on Python 3.11 and
3.14: 339 passed and 3 skipped on each. The skips are the existing live desktop
notification checks, disabled by the CI command's unset session bus. All ten
desktop action-provider tests passed. No firmware or font files changed.

New coverage includes metadata filtering, alias deduplication, strict hello
and fresh-pong validation, atomic persistence, cancellation before and after
the commit point, exact-serial reconnect, setup through the real daemon IPC,
same-handle adoption, active explicit-target pairing, failed replacement,
pause/resume, target reload, bounded writes and keepalive expiry. Actual PTY
unplug tests verify handling of Linux `tcflush` errors, already-lost async
transports, released tty exclusivity and absence of callback failures.
An unfinished application frame must not swallow the next discovery hello;
the framing regression and a physical Snap reproduction verify first-attempt
recovery without resetting the board.
