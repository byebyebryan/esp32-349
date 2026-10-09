# Notification-panel implementation and validation

Checkpoint: **2026-10-08**, source revision
[`b9046f4`](https://github.com/byebyebryan/esp32-349/commit/b9046f4417a0bed22ff5c07cc6f2ca101ef2b87d).
This is a dated validation record, not a live deployment inventory. Supported
behavior is in [behavior](docs/behavior.md), implementation contracts in
[architecture](docs/architecture.md), [protocol](docs/protocol.md),
[UI design](docs/ui.md) and [backlight policy](docs/backlight.md).

## Implemented behavior

| Area | Contract at this checkpoint |
| --- | --- |
| Display | V2 hardware, 640 × 172 landscape, fixed clock/telemetry rail and notification-only right pane |
| Telemetry | One-second sampling; CPU frequency/usage and memory amount/usage with spaced bars; physical-uplink UP/DN traffic; independent unavailable and stale states |
| History | Newest 32 RAM records; default 600-second limit from genuine arrival/replacement; popup timeout, viewing and sync do not renew cards |
| Touch/text | Vertical whole-card browsing, Open/×, inert body/horizontal input; three body lines, bounded conversion and Latin bold/italic with regular CJK fallback |
| Open | Optional DMS bridge; owner/PID and per-notification identity proof; archived cards keep text with Open disabled |
| USB | Explicit pairing, exact saved-serial selection, hello/pong verification and same-handle reconnect without reset |
| Backlight | 50% default; host-screen following, five-minute daemon-loss timeout, four local levels/manual off, 30-second fresh-notification boost |
| Compatibility | Capability-gated extensions; older grouped/active-card and generic-zone paths remain available |

## Recorded automated validation

The published boost source passed **417 host/tooling tests**, with three
optional live-desktop checks skipped, and **15/15 native CTests in Debug and
Release**. The notification-panel build passed with ESP-IDF v5.5.3. Shared
support and render-bench were unchanged from the preceding successful builds.
Boost publication review repeated all 24 host display tests and the native
backlight/protocol checks in both configurations.

Earlier review source
[`15b718e`](https://github.com/byebyebryan/esp32-349/commit/15b718ee1254da4fb0c10ba1ca283c85cc034762)
passed 392 host/tooling tests (three live checks skipped), 10 desktop-provider
checks, 14/14 native CTests in Debug/Release and both firmware builds. Shared
framebuffer/font audits and actionlint passed; all five jobs in its
[published CI run](https://github.com/byebyebryan/esp32-349/actions/runs/37731734184)
succeeded. Private-broker notification-server restart regressions are required
in CI. That review also bounded JSON container depth, validated RTC reads with
timer fallback, and removed duplicate guest CPU accounting.

Native checks execute production parser/state, LVGL, gesture and RTC code with
platform substitutions. They do not establish ESP32 scheduling, touch hardware
or panel transfer. Current reproducible gallery assets are
[native demos](docs/media/README.md) with virtual notifications, time and input.
See [testing](docs/testing.md) for the commands and physical checklists.

## Recorded physical and serial checks

These rows retain the original dates and source/firmware identities; each is a
short checkpoint for that revision. Later revisions do not inherit additional
physical acceptance merely by passing automated tests.

| Date | Identity | Scope and result |
| --- | --- | --- |
| 2026-09-28 | Firmware ELF `b9be56530` | User confirmed readable notification-only cards, vertical browsing, traffic, dismissal and empty state; predates the fixed-height/control refinement |
| 2026-09-30 | Firmware ELF `b2794933e` | User confirmed text formatting and controls, then actual Kitty/Codex default-action focus and dismissal; desktop markup used owned fixtures |
| 2026-10-02 | Host `95420ed38c105d0883eb4bda5ca7bdead9a6be2b` | Persistent pairing, mixed-device rejection, physical hub recovery and pause/resume retention; no firmware update |
| 2026-10-07 | Firmware ELF `a48cf47a5` | User accepted revised CPU/MEM utilization-bar spacing on the panel |
| 2026-10-08 | Corrected dimming ELF `dc3c6bc6b`; published controls source `133abd4` | User confirmed four visible levels, preferred 50%, Power off and RESET restart. Serial readback separately showed 50% at 299.004 s and off at 300.704 s |
| 2026-10-08 | Boost ELF `ff75ea07a`; published source `b9046f4` | User confirmed visible boost/return and immediate Brightness cancellation to 75%. A quiet serial trial separately showed 100% at 29.700 s and selected 65% at 30.200 s; later serial button trial preserved 75% through pause/resume |

At `15b718e`, a device serial probe accepted nesting depth 16, rejected depth
128 with `parse_error`, then returned a fresh pong without reboot. It did not
repeat visual/touch acceptance. Electrical current and lux were not measured.
The old horizontal-deck swipe trial measured roughly 16–20 updates/s; this is
historical device evidence, not a current-layout performance guarantee.

The superseded plans, trial logs and captures were retired after extracting
current contracts. Their dated originals remain in
[repository history](https://github.com/byebyebryan/esp32-349/tree/b8c1f64cfeb622182d4a024689cc74914987ed3c/projects/notification-panel/design).
Machine-specific identities, rollback paths and live deployment notes belong
with local backup artifacts rather than the project guides.

## Remaining scope

Body-text scrolling, disk history, clear-all and additional content groups are
deferred. The optional 24-hour soak, actual host suspend/wake, physical host
reboot, automatic monitor-off/on observation and held-finger wake acceptance
remain unverified. Full live desktop-shell restart is separate from isolated
broker recovery tests. Real default-action/focus observations cover selected
terminal cases, including Ghostty with a local compositor compatibility setting;
they do not establish broad application or compositor compatibility.
