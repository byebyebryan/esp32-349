# Telemetry rail refinement

Implementation checkpoint, 2026-09-28. The grouped UI is a functional prototype;
this checkpoint refines the persistent left rail before further work on the
notification area. It follows the [grouped direction](grouped-ui-plan.md).
The implementation, automated checks and brief Starship physical check are
complete on corrected firmware `4c9005831`. Snap remains on the earlier
candidate and needs the follow-up. Exact rollout and evidence are in the
[acceptance record](telemetry-rail-acceptance.md).

## Findings before implementation

- The 160 px rail shows `HOST` on Home and a compact clock in Notifications.
  Metric rows also move between those views. The label identifies no useful
  host detail and spends the clock's reserved space.
- CPU and memory already have host sources. CPU uses a three-second EMA.
- `NET LINKED` means an active default route with a local link. It measures
  neither traffic nor Internet reachability.
- Battery is conditional today. The intended desk use does not need a battery
  row; its space is more useful for a second network direction.
- At planning time, Snap had both Ethernet and Wi-Fi default routes. Traffic collection
  must define its interface scope and avoid counting an interface twice when
  it appears in both the IPv4 and IPv6 route lists.
- The host's default `tick_s` is already 1.0, but notification/deadline wakeups
  also run telemetry sampling. A consistent one-second cadence needs its own
  sampling deadline rather than sampling on every event-loop wakeup.

## Agreed layout

Keep one fixed layout in Home and Notifications:

```text
       15:23

CPU          12%
MEM          41%
DN      2.3 MiB/s
UP       84 KiB/s
```

- Pin local `HH:MM` at the top. Remove `HOST`; no seconds or hostname heading.
  The user confirmed this means an always-visible clock, not a report of an
  incorrect displayed time.
- Remove the battery row from this rail on every host.
- Give download and upload their own rows. Keep CPU/MEM first, then download,
  then upload; label them `DN` and `UP`, matching the uppercase CPU/MEM labels.
  Rate units provide the network context without repeating `NET` on both rows.
  Right-align values to one stable edge.
- Use 40 px time, 20 px values and existing smaller CPU/MEM labels as the
  starting geometry, with the same label font for DN/UP. Reserve more value
  width for rates than the current 92 px column; check the longest formatted
  rates in production-font captures.
- Keep the four metric baselines fixed at approximately y=58, 82, 106, 130.
  A small footer can report unavailable/stale readings without moving them.
- Use quiet text updates rather than animated digits, activity bars or graphs.
  A zero rate is useful information; it should not become `LINKED` again.

The large Home clock/date remains part of the current group design. A
persistent small clock establishes the rail's stable position; any later
Home redesign is a separate layout decision.

## Update cadence

The user requested a common **one-second (1 Hz)** cadence for the rail.

- Sample CPU, memory, download and upload together once per second, using a
  monotonic deadline. Publish one coherent dashboard update when values change.
  Cache the sample and update dashboard/zones under the same state lock so a
  concurrent full sync cannot mix readings from different samples.
- Notification arrivals, action responses and expiry deadlines remain prompt;
  their event-loop wakeups do not trigger extra telemetry sampling or reset
  the next sampling deadline. Full sync uses the latest sample, with an initial
  baseline allowed at startup.
- Measure network rates from actual elapsed time between counters, including
  delayed ticks. Skip missed ticks rather than collecting a burst of catch-up
  samples. Schedule the next sample one tick after collection finishes; slow
  sources may reduce the refresh rate, rather than leaving a deadline overdue.
- Keep smoothing separate from refresh cadence: the existing CPU EMA and
  short network window still produce a new reading each second.
- Evaluate the clock once per second. With `HH:MM`, its visible text changes
  at the minute boundary. Touch processing and animation keep their existing
  faster cadence.

## Traffic semantics

Label meaning: **uplink-interface receive/transmit bytes per second**.
This includes local-network traffic carried by those interfaces. It is not an
Internet speed test or application-level throughput measure.

- Sample cumulative RX/TX byte counters on the host, using monotonic elapsed
  time. Send numeric rates; formatting belongs to the firmware UI.
- Start with unique active physical interfaces named by default routes,
  deduplicated across IPv4/IPv6. Sum Ethernet and Wi-Fi once each when both
  qualify. Do not add their VPN/bridge/container counters as additional traffic.
  If no qualifying physical uplink can be identified, report unavailable.
- Use a short two-second measurement window, refreshed once per second, to
  make ordinary bursts readable. Do not animate between readings.
- Show binary `B/s`, `KiB/s`, `MiB/s`, `GiB/s` (1,024 per step), with a space
  before the unit and precision chosen so the numeric
  value and unit fit without an ellipsis. Promote units when rounding would
  otherwise produce `1024`. The supported rate limit is 10^12 B/s, which
  displays as `931 GiB/s` and fits the existing traffic column.
- Show `--` while a new interface lacks a baseline or counters are unreadable.
  Show `0 B/s` only after a valid measurement yields no traffic.
- On an interface change or counter reset, start a fresh baseline and suppress
  a fabricated spike. If one required interface is unreadable, do not present
  an incomplete total as a complete measurement.
- Keep link loss and host disconnection distinct. An offline uplink can be
  reported quietly in the rail; host disconnection still marks readings stale.

## Execution checkpoint

1. Settle the clock interpretation and review rail geometry with normal,
   zero, high-rate, unavailable and stale samples.
2. Establish the shared one-second sampling deadline, separate from prompt
   notification/action events. Extend `NetworkSource` with bounded counter
   sampling and deterministic fixtures for dual-stack deduplication, multiple
   uplinks, reset, delayed ticks, event wakeups and failure.
3. Extend the dashboard wire/parser with optional nullable numeric RX/TX
   rates. Keep existing link state and older-peer decoding compatible; verify
   that an older firmware parser ignores the added fields. New firmware must
   show unavailable rates with an older host instead of inventing zero traffic.
4. Implement the fixed rail in `ui_deck.c`. Battery collection/schema may
   remain available to legacy clients; removing its row does not require a
   breaking protocol removal.
5. Check production-font native captures for Home and Notifications, including
   rate boundaries and stale state. Run focused source/parser tests and the
   integrated native suite; ESP-IDF build verifies the firmware boundary.
6. Finish with one brief on-device readability check of the clock and four rows.
   Verify live traffic against the chosen host counters through an automated
   readback/log probe. Do not repeat the held-gesture manual sequences.

The initial rollout was on Snap; the user resumed the final readability check
on Starship. That check passed after the automated USB probe exposed and
verified the correction for a link-task stack overflow.

The implemented geometry uses 36 px label columns and 96 px value columns.
The original production-font checks covered decimal unit rounding and the
bounded `>999GB/s` value. The 2026-09-30 binary-unit refinement is recorded in
the [number/unit pass](rail-number-unit-pass.md), including conversion and
width checks through `931 GiB/s`.
Older hosts leave the optional rate fields unavailable; the previous firmware
dashboard parser accepts the added fields. Legacy battery collection/schema
remains available, while the rail has no battery row.
