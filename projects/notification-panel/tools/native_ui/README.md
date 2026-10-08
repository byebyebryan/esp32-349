# Native UI checks

Run from `projects/notification-panel/` after an IDF build has fetched the pinned LVGL
dependency. Install CMake, a C compiler, pkg-config, SDL2 development headers,
Python, uv and RTK (used by the check script). Use a shell without EIM
activation so host GCC selects the host assembler; EIM's `PATH` can select
the ESP ULP assembler instead. Supply the SDK's cJSON directory directly:

```sh
native_idf_root="${EIM_ROOT:-$HOME/.espressif}/${IDF_VERSION:-v5.5.3}/esp-idf"
python tools/check_native_ui.py --cjson-include "$native_idf_root/components/json/cJSON"
```

This builds Debug and Release with assertions enabled and runs thirteen CTests
per configuration. The default CTest gate includes the production USB line
receiver and legacy renderer safety checks. Override `--debug-build-dir` and
`--release-build-dir` to choose build/artifact directories. The default directories are
the checkout's ignored `.cache/native-ui/debug` and `.cache/native-ui/release`
directories, independently of the current working directory. An override that
contains a CMake cache from another checkout is rejected before either build.
The cache is preserved; choose fresh override paths to continue.

## Targets and evidence

| Target | Production path | Substitutions |
|---|---|---|
| `native_ui` | LVGL, `ui_deck.c`, deck/input policy and font fallback/assets | Direct state fixture, virtual clock, link and dismissal capture |
| `native_groups` | Same UI with grouped gesture/lifecycle cases | Same direct fixture |
| `native_protocol` | `proto.c`, `state.c`, dashboard parser and IDF cJSON | Native allocation, locks, clock/RTC, USB output and descriptor identity |
| `native_rtc` | Production `rtc.c` register decoding and timer fallback | Simulated I2C registers/errors and elapsed timer; stopped oscillator, BCD/range/calendar validity and recovery; UBSan |
| `native_link_receiver` | Production `link.c` task and USB read loop | Fragmented native USB reads, captured task startup and overflow callback |
| `native_legacy_renderer` | Production `ui.c` progress/media widgets plus real `state.c` | Native LVGL display, clock/RTC and protocol hooks; UBSan and float-cast-overflow |
| `native_ui_composed` | Real parser/state plus the same LVGL fixture | Native platform services and pointer/time adapter |
| `host_composed` CTest | Real Python daemon/protocol → production parser/state → LVGL → daemon input | Controlled host clock and transport to `native_ui_composed` |
| `host_actions_composed` CTest | Notification source → daemon/provider action request → production parser/state/LVGL → correlated host result | Controlled source/provider adapter and native transport; no desktop IPC |
| `host_history_composed` CTest | Negotiated 10-minute history → production parser/state/LVGL → host browse/idle/dismiss input | Virtual time and transport; popup/archive, body limits, no-renewal sync/reconnect and replacement renewal |
| `host_body_composed` CTest | Host conversion/projection → parser/state → production-font LVGL spans | Synthetic text and virtual transport; bounded styles, fallback, ellipsis and replacement/gesture coherence |
| `smoke_recorder` CTest | Isolated production host policy → parser/state/LVGL, actual × round trip, timed retention and fresh process recovery | Native transport; fake IPC checks finish/error/pause ownership cleanup |

CTest also runs serialized replay and an SDL dummy-driver smoke. If uv is
unavailable, `host_composed`, `host_actions_composed`, `host_history_composed`,
`host_body_composed` and `smoke_recorder` are explicitly skipped; such a run does not satisfy
the composed acceptance gate. These programs do not test ESP32 scheduling,
capacitive touch, RTC peripherals, DMA or panel performance.

Captures are full-size 640 × 172 RGB PPM files under `artifacts/`. Tests retain
traces there. Debug and Release legacy captures were compared byte-for-byte
with the pre-change at-rest and mid-drag baseline. The grouped captures show
production fonts and virtual data; they are not board photos. Notification
action captures cover the fixed foreground Open slot in ready, pending and
disabled states, a long English title, a CJK title, and the legacy geometry.
The grouped fixture also captures arrival, dismissal and last-card return
to Home in 15 ms samples. It checks immediate cache removal, frozen outgoing
geometry, inert touch during transitions and concurrent-update cancellation;
see [the motion evidence](../../design/card-motion.md).
History captures cover the notification-only empty/single/multiple layouts,
smaller text, age metadata, disconnected browsing, and arrival/removal motion.
The historical grouped/action scenarios project only the new history capability
out of the real firmware hello, keeping their old-peer compatibility assertions.
The history scenario uses the complete hello.

The grouped fixture also checks CPU/MEM utilization bars at zero, half and full
using rendered pixels, independent missing usage/detail readings, stale dimming
and recovery, and gestures starting on a bar. Its seven `rail-bars-*` captures
cover normal, zero, half, full, unavailable, stale and empty-history views.

## Inspect the shared UI

```sh
.cache/native-ui/debug/native_ui --viewer --grouped
```

Mouse input supplies the same LVGL pointer path as replay. Window performance
does not measure device performance. The existing legacy demo uses `--viewer`
without `--grouped`.

## Replay and round trips

```sh
.cache/native-ui/debug/native_ui \
  --replay tools/native_ui/replay_smoke.json --artifacts /tmp/349-ui-replay
uv run --project host --frozen python tools/test_grouped_composed.py \
  --native .cache/native-ui/debug/native_ui_composed \
  --artifacts /tmp/349-host-composed
uv run --project host --frozen python tools/test_notification_actions_composed.py \
  --native .cache/native-ui/debug/native_ui_composed \
  --artifacts /tmp/349-host-actions-composed
uv run --project host --frozen python tools/test_notification_history_composed.py \
  --native .cache/native-ui/debug/native_ui_composed \
  --artifacts /tmp/349-host-history-composed
```

The direct replay JSON has explicit pointer/time/state/capture commands; its
example is [replay_smoke.json](replay_smoke.json). The composed runner accepts
one JSON object per stdin line and emits one flushed response per command:

```json
{"type":"wire","message":{"t":"hello"}}
{"type":"pointer","action":"press","x":350,"y":100,"ms":1}
{"type":"advance","ms":200}
{"type":"capture","name":"example"}
{"type":"readback"}
```

Start it with `native_ui_composed --jsonl /tmp/349-composed`. Responses contain
actual outgoing protocol messages in `outbound` and fixture diagnostics in
`readback`. Acceptance assertions use the actual `cards_status` envelope,
validated by the host parser. The composed Python scenario returns actual
`input` messages to daemon handling; its held replacement check compares
captured title/body pixels as well as state.

Physical checks are reduced to the brief checklist in
[grouped-ui-execution.md](../../design/grouped-ui-execution.md). Serial board
probes are separate and require a paused daemon; they do not certify pixels
or physical touch.

## Opt-in physical recorder

Run only when the observer is ready at Starship's board:

```sh
uv run --project host --frozen python tools/run_grouped_smoke.py \
  --port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_28:84:85:92:C2:20-if00 \
  --expected-build-sha 604fd70da --artifacts /tmp/349-physical-smoke
```

The tool checks the firmware identity, takes its own sticky pause, and uses an
isolated instance of the production daemon policy with synthetic data. It does
not start sources or an IPC server, send desktop notifications, change config,
or restart the normal daemon. That daemon keeps its RAM history while paused.
Initial state is Home with three persistent cards cached.

| Operator command | Effect |
|---|---|
| `cards` | Present the newest test card persistently for the look/touch phase |
| `timeout` | Reset the synthetic session to Home and start one announced 10 s presentation; the card stays retained afterward |
| `replug` | Arm USB removal/return logging; restore surviving IDs at Home with no replayed attention |
| `status` | Print the latest stable device readback |
| `finish` | Clear synthetic state, close the port, resume the normal daemon and check its readback |

Actual browse/dismiss inputs are handled by the production host. Ctrl-C, EOF
and setup exceptions perform the same cleanup. An existing pause is refused;
a lost response to the tool's own pause still triggers restoration. The
session has a 20-minute limit. Trace, console and structured results are saved
in the chosen artifact directory. Human observations are recorded separately;
the tool never marks visual/touch acceptance passed by itself.

The routine CTest executes **only** `--self-test --native ...`. It does not open
the hardware or contact the real daemon. It validates the controller through
the composed native target and uses fake IPC for cleanup/ownership checks.

For the notification-only physical check, add `--history`. This negotiates the
new mode and seeds three cards with expanded text and the default 10-minute
retention. Browse vertically, check inert horizontal/body input, then use × to
remove the cards and inspect `No recent notifications`. `status` records the
current view, and `finish` restores the normal daemon. No timed Home phase or
held-touch sequence is needed. The self-test also checks this history fixture's
seed, actual × round trip and last-card empty state through the native runner.
