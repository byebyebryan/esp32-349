# Notification-only right pane

Implementation checkpoint, 2026-09-28. The user parks horizontal app navigation
and dedicates the right 480 px to recent notifications. The accepted fixed
160 px telemetry rail remains visible. This supersedes the Home/presentation
behavior of the grouped UI only when both peers negotiate the new mode.
The 2026-09-30 refinement reduces the default retention from 30 to 10 minutes.

## Product behavior

- Empty state: `No recent notifications`; no duplicate large clock/date.
- Retain newest-first cards for a default 600 seconds after genuine arrival
  or replacement, with a maximum of 32 records. Busy bursts can evict earlier.
- Desktop popup timeout ends attention, not retained text. Device dismiss and
  explicit desktop dismissal/close remove the corresponding card immediately.
- Viewing, swiping, full sync and reconnect do not renew retention. Apply the
  age bound to normal, critical and persistent desktop notifications alike.
- New arrivals take focus while idle. Browsing preserves the selected ID;
  after 30 seconds without interaction, later arrivals can take focus again.
  Held gestures and settlement own their captured text and identities.
- Keep vertical one-card snapping, arrival/dismissal motion, inert body taps
  and large explicit Open/close targets. Horizontal drags do nothing.
- Use app name plus relative age, a 22 px title and a 16 px body with the same
  Unicode repertoire as the current text fonts. Reclaim the group-cue space.
- Keep Open enabled only for a currently valid desktop action. Archived text
  is still readable after that action becomes unavailable.
- Bound capable-peer bodies to 511 UTF-8 bytes plus the C terminator. Legacy
  projections remain at 159 bytes. Indicate truncation with an ellipsis.
- History stays in daemon RAM; board replug recovers its remaining lifetime.
  Daemon/host restart starts empty. Disk persistence and clear-all are deferred.

## Wire and ownership

Capability `notification-history-v1` requires the existing complete grouped,
card-sync and dashboard capability set. Negotiated `sync_begin.grouped` adds
`history: true`; without it, existing Home/grouped and legacy behavior remains.

Each history card carries:

```json
{"history":{"rev":1,"age_ms":0,"remaining_ms":600000}}
```

All three fields are integers: `rev` is positive 31-bit; `age_ms` is
nonnegative 31-bit; `remaining_ms` is positive 31-bit. The host assigns a fresh
revision on each accepted arrival/replacement and derives age/remaining time
from its monotonic receipt time at serialization. Expired records are excluded.
The firmware derives local monotonic timestamps; a same-revision sync can
shorten but cannot extend its existing deadline. A newer revision can renew it.
Local expiry must remove cached text/action eligibility even while disconnected
and must not let ordinary full sync restore expired or dismissed content.

Host retained metadata is bounded with text, and expiry releases correlation
and action state. The extended body/history metadata is only sent to capable
peers. Existing framing, chunk limits, session/action identity checks, atomic
publication and generation-scoped attention remain in use. A presentation in
history mode selects eligible content but never returns to a clock on timeout.

Before the first device hello, startup cards and desktop associations use the
same 32-record bound. A replacement refreshes association order as well as
retained-card order. After an older peer selects the legacy mode, its full
active collection and overflow count remain supported; retained-history
eviction must preserve desktop close/popup-expiry tracking and injected-card
expiry until the legacy active card itself closes.

## Acceptance

1. Deterministic host tests prove 10-minute expiry, replacement renewal,
   popup-versus-retention independence, bounded metadata, no sync/view/replug
   renewal and old-peer projection limits.
2. Production C/native tests prove history parsing, offline and same-revision
   expiry, stable browsing, inert horizontal drags, safe held removal and valid
   Open/close identity. Composed tests verify host/device agreement.
3. Production-font captures cover empty, single/multiple, long English/CJK,
   age labels, unavailable Open and arrival/removal states. Debug/Release pass.
4. ESP-IDF build and USB readback verify source/binary provenance and stack
   headroom with the expanded cached text. One short Starship visual/touch
   check verifies readability, vertical browsing and smooth removal.
5. Restore mirroring and clear owned fixtures. Record exact evidence without
   claiming long-duration stability or new rendering performance.
