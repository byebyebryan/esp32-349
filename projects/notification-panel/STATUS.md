# Notification-panel implementation and validation

Checkpoint: **2026-10-08**, source revision
[`b9046f4`](https://github.com/byebyebryan/esp32-349/commit/b9046f4417a0bed22ff5c07cc6f2ca101ef2b87d).
This is a dated implementation and validation record. Setup commands are in the
[setup guide](docs/setup.md); supported behavior is in the
[behavior reference](docs/behavior.md).

## Implemented behavior

| Area | Current contract |
| --- | --- |
| Display | V2 hardware, 640 × 172 landscape, fixed clock/telemetry rail and a notification-only right pane |
| Telemetry | One-second sampling; CPU frequency/usage and memory amount/usage with spaced bars; UP/DN physical-uplink traffic; independent unavailable and stale states |
| History | Newest 32 retained cards by default; 600-second age limit from genuine arrival/replacement; desktop popup timeout, viewing and sync do not renew a card |
| Touch | Vertical whole-card browsing, explicit Open/× controls, inert body taps and horizontal drags; offline cached browsing |
| Text | English and 3,500 common Simplified Chinese characters, bounded host conversion and capability-gated bold/italic spans; visible placeholders outside the selected repertoire |
| Open | Optional DMS bridge; fresh owner/PID and per-notification identity proof; archived cards keep their text with Open disabled |
| USB | Explicit one-time pairing, exact saved-serial selection, hello/pong verification and same-handle adoption; normal reconnect/resume does not reset the board |
| Backlight | 50% default; host-screen following and five-minute daemon-loss timeout; local 25/50/75/100% brightness and manual off; fresh notifications boost to 100% for 30 seconds, then restore the selection |
| Compatibility | Capability-gated history, styles, actions, dashboard and backlight controls/boost; older grouped/active-card and generic-zone paths remain available |

The latest UI refinement is the
[accepted CPU/MEM bar spacing](design/telemetry-rail-usage-bars.md).
The October review also bounded JSON container depth to 16 before cJSON
recursion, validated RTC oscillator/calendar reads with timer fallback, and
removed double-counted guest time from CPU accounting. Notification-server
restart recovery was introduced in the
[October 5 host checkpoint](design/notification-actions-acceptance.md#notification-server-restart-recovery-2026-10-05).

## Recorded automated validation

The published boost source passed **417 host/tooling tests**, with three optional
live-desktop checks skipped, and **15/15 native CTests in both Debug and Release**.
Its notification-panel build passed with ESP-IDF v5.5.3; the shared component
and render-bench were unchanged from the preceding successful builds. See the
[boost checkpoint](design/backlight-boost-acceptance.md) for the full scope.

### Earlier review checkpoint (`15b718e`)

These results belong to
[`15b718e`](https://github.com/byebyebryan/esp32-349/commit/15b718ee1254da4fb0c10ba1ca283c85cc034762).

| Gate | Result at this checkpoint |
| --- | --- |
| Host and tooling | 392 passed, 3 optional live-desktop tests skipped; isolated private-broker restart regressions required in CI |
| Desktop action provider | 10 checks passed |
| Production native checks | 14/14 in Debug and Release, also repeated in a fresh Ubuntu environment |
| Firmware | Both notification-panel and render-bench build with ESP-IDF v5.5.3 and their locked dependencies |
| Shared component and fonts | Framebuffer regression and font coverage/metrics audit passed |
| Workflow | Actionlint passed; all five jobs in the [published CI run](https://github.com/byebyebryan/esp32-349/actions/runs/37731734184) succeeded |

Native checks execute the production parser, state, LVGL, gesture policy and
RTC driver with platform substitutions. They establish those code paths;
ESP32 scheduling, actual touch input and panel transfer require device evidence.
The later presentation pass adds a documentation/media CI gate and reproducible
[native demo assets](docs/media/README.md).

## Recorded device validation

Physical observations confirmed four visible brightness levels, the 50%
default, Power turning the backlight off and RESET restarting the device.
The notification boost visibly returned to the selected level, and pressing
Brightness during a boost canceled it immediately. The detailed
[button](design/backlight-buttons-acceptance.md) and
[boost](design/backlight-boost-acceptance.md) checkpoints distinguish those
observations from native assertions and serial counters.

At the earlier `15b718e` review checkpoint, a device serial probe accepted
container depth 16, rejected a 128-level nested-array frame with `parse_error`,
and returned a fresh pong without changing boot ID. The spaced-bar layout
already had user-confirmed panel acceptance; that backend review did not
repeat visual/touch acceptance.

## Evidence map and remaining scope

| Topic | Detailed record |
| --- | --- |
| Notification brightness boost (October 8) | [Boost acceptance](design/backlight-boost-acceptance.md) |
| Backlight policy (October 8) | [Backlight acceptance](design/backlight-acceptance.md) |
| 50% default and physical button controls (October 8) | [Button acceptance](design/backlight-buttons-acceptance.md) |
| Current bar layout and panel feedback | [CPU/MEM utilization bars](design/telemetry-rail-usage-bars.md) |
| Paired reconnect, mixed devices and hub power cycle | [USB pairing acceptance](design/usb-pairing-acceptance.md) |
| Retention and notification-only UI | [History acceptance](design/notification-history-acceptance.md) |
| Text conversion, styles and real terminal Open | [Body-formatting acceptance](design/notification-body-acceptance.md) |
| Open and notification-server identity recovery | [Action acceptance](design/notification-actions-acceptance.md) |
| Prototype/swipe trials and rendering measurements | [Historical acceptance](ACCEPTANCE.md) |

Body-text scrolling, disk history, clear-all and additional content groups are
deferred. The optional 24-hour soak was deferred; actual host suspend/wake and
a physical host-reboot gate are unverified. A full live desktop-shell restart
remains separate from the isolated broker recovery tests.
