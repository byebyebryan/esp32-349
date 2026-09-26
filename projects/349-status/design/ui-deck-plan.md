# Side-peek UI implementation checkpoint

The [UI brief](ui-brief.md) is the design basis. This goal replaces the
prototype's top bar and two thin notification strips on **Starship's board**.
The active-card cache and host-owned notification lifetime remain the basis.

## Implementation

- A 160 px rail holds CPU, memory, local network link, and conditional battery.
  CPU has a three-second time-aware exponential average and readings are
  quantized to whole percentages. A linked default route is not a claim of
  Internet reachability. An unavailable probe is distinct from zero or offline.
- The remaining 480 px shows a large idle clock/date. With a visible card,
  the clock moves to the rail. Stale host readings are marked; the clock and
  connection message stay in the content area.
- A single card is 464 × 156 px. Multiple cards use a 392 × 156 foreground
  and a 64 × 140 next-card peek. Source text is secondary; title is 22 px,
  body is 20 px with three 26 px lines. The foreground's × target is
  48 × 36 px. Tapping body text has no dismiss action.
- The deck retains a focused **ID**, never a pointer into the swappable PSRAM
  cache. Tapping the peek advances circularly. Closing the focus selects its
  surviving successor. Periodic sync and in-place replacement preserve focus.
- Normal arrivals settle for 300 ms after the last arrival; critical arrivals
  focus immediately and are protected against later normals in the same tick.
  User navigation cancels queued automatic focus. Local dismiss affects the
  board; replacing that desktop ID can restore it.
  Full sync recognizes newly seen arrivals after the newest retained card and
  newly seen critical cards, so reconnect recovery restores attention without
  letting an older normal-card refill move the selected foreground.
- `position / reachable` counts cached, locally visible cards. A separate
  `+N uncached` count describes host-held active cards that cannot be browsed.
- Volume/mute and Bluetooth count changes appear for four seconds. Their
  initial sample is quiet. Low battery and network loss use restrained amber;
  normal cards use a muted blue accent and critical cards use red.
- Font assets preserve the exact 2,602-codepoint union of the legacy 14/16 px
  chains at both 20/22 px. The larger clock is a 12-glyph Montserrat subset.
  See [sources and licenses](../main/fonts/README.md). Emoji outside the set
  remain visible placeholders.

## Compatibility and wire contract

The `dashboard-v1` capability is advertised alongside `card-sync-v1`. A
capable host adds a `dashboard` object to `sync_begin`, staged and committed
atomically with clock, bar, media, and cards. Change-only deltas have
`t: "dashboard"` plus the same fields:

```json
{
  "cpu": 0.12,
  "mem": 0.43,
  "network": true,
  "battery": null,
  "volume": {"level": 0.5, "mute": false},
  "bluetooth": 1
}
```

CPU/memory and network may be null. Battery/volume are null when unavailable,
or contain a finite level in 0–1 and a nullable charging/mute boolean.
Bluetooth is a nullable integer in 0–999. No interface names or Bluetooth
device identifiers are sent. Malformed staged dashboard data rejects the
transfer; an interleaved dashboard delta follows the existing resync rule.

Firmware selects the deck when a valid dashboard is committed. A host that
omits dashboard data selects the legacy bar/card UI. A new host sends the old
envelopes to older firmware. `[bar].preset` continues to mean horizontal
zones; its widths are not reused as vertical rail geometry.

`349ctl device-cards` includes optional deck diagnostics: mode, stale state,
reachable count, one-based position (zero when empty), focused ID, and next
ID. The LVGL timer publishes these fields, so they can lag a new cache event
by one 100 ms tick.

## Exit checks

1. Native tests run the firmware's actual focus policy and dashboard parser:
   burst settling, sync/replacement stability, simultaneous close/arrival,
   critical priority, touch cancellation, nullable/finite values, and malformed
   fields.
2. Host tests cover both capability combinations, typed source fixtures,
   configuration frame sizing, transactional wire ordering, and cache behavior.
3. Firmware build and four-size text coverage audit pass.
4. On Starship, verify idle geometry, no desktop battery placeholder, readable
   card/CJK/symbol text, body tap versus explicit dismiss, circular peek
   navigation, and truthful reachable/uncached counts. Keep test cards isolated
   from desktop mirroring and restore the original config afterward.
5. Check device readback for replacement, focused/unfocused close, cache swap,
   critical/normal bursts, overflow, stale recovery, and mixed host versions.

Record actual outcomes and build identity in [ACCEPTANCE.md](../ACCEPTANCE.md).
There is no soak or host-suspend gate in this goal.

**Completed on Starship (2026-09-25):** the short gates recorded in
[ACCEPTANCE.md](../ACCEPTANCE.md) passed, including the 118-test host suite,
native tests, firmware readback, and isolated human visual/touch/count checks.
The original notification config was restored and normal mirroring resumed.
The reviewed UI source is committed in `149f52e`; the accepted board image
predates the final peek-tap race fix, which has build and native-test coverage.
Snap remains on the preceding cache UI.
