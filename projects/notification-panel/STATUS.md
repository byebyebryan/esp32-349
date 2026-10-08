# Notification-panel implementation and validation

Checkpoint: **2026-10-08**, source revision
[`15b718e`](https://github.com/byebyebryan/esp32-349/commit/15b718ee1254da4fb0c10ba1ca283c85cc034762).
This is a dated implementation/deployment record. Setup commands are in the
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
| Compatibility | Capability-gated history, styles, actions and dashboard; older grouped/active-card and generic-zone paths remain available |

The latest UI refinement is the
[accepted CPU/MEM bar spacing](design/telemetry-rail-usage-bars.md).
The October review also bounded JSON container depth to 16 before cJSON
recursion, validated RTC oscillator/calendar reads with timer fallback, and
removed double-counted guest time from CPU accounting. Notification-server
restart recovery was introduced in the
[October 5 host checkpoint](design/notification-actions-acceptance.md#notification-server-restart-recovery-2026-10-05).

## Recorded automated validation

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
[native demo assets](../../docs/media/README.md).

## Recorded Starship deployment

Starship's paired V2 board received the `15b718e` application at **22:26 PDT on
2026-10-07**. The application write was verified; configuration and saved
pairing remained unchanged. Resume restored a fresh link without an extra
reset, and host/device telemetry matched with a non-stale, empty deck.

| Artifact | Identity |
| --- | --- |
| Application | 3,822,624 bytes; SHA-256 `2aa4d9818b18420ba0c817e29988e6f24bf84b7478d984827729c4ffb6b0bb04` |
| ELF | SHA-256 `1147137877ff70be54262535fd485c786cc8372d490c9551c156e21236bce916` |
| Device hello | Build `15b718e`, ELF prefix `114713787` |

A physical serial probe accepted container depth 16, rejected a 128-level
nested-array frame with `parse_error`, and returned a fresh pong without
changing boot ID. Verified previous-application/partition artifacts and raw
deployment records are retained in the operator's local backup directory,
`~/.local/share/esp32-349/backups/review-fixes-starship-20261008T052022Z/`.

The earlier spaced-bar layout has user-confirmed panel acceptance. This
backend-fix deployment did not repeat the visual/touch trial. Deployment
records describe that run, rather than the display's connection state today.

## Evidence map and remaining scope

| Topic | Detailed record |
| --- | --- |
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
