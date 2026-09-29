# Native UI checks

Run from `projects/349-status` after an IDF build has fetched the pinned LVGL
dependency. Install CMake, a C compiler, pkg-config, SDL2 development headers,
Python, uv and RTK (used by the check script). Load the IDF environment or supply its cJSON directory:

```sh
python tools/check_native_ui.py --cjson-include "$IDF_PATH/components/json/cJSON"
```

This builds Debug and Release with assertions enabled and runs ten CTests
per configuration. Override `--debug-build-dir` and `--release-build-dir` to choose
build/artifact directories. The default directories are
`/tmp/349-native-ui-build` and `/tmp/349-native-ui-release`.

## Targets and evidence

| Target | Production path | Substitutions |
|---|---|---|
| `native_ui` | LVGL, `ui_deck.c`, deck/input policy and font fallback/assets | Direct state fixture, virtual clock, link and dismissal capture |
| `native_groups` | Same UI with grouped gesture/lifecycle cases | Same direct fixture |
| `native_protocol` | `proto.c`, `state.c`, dashboard parser and IDF cJSON | Native allocation, locks, clock/RTC, USB output and descriptor identity |
| `native_ui_composed` | Real parser/state plus the same LVGL fixture | Native platform services and pointer/time adapter |
| `host_composed` CTest | Real Python daemon/protocol → production parser/state → LVGL → daemon input | Controlled host clock and transport to `native_ui_composed` |
| `host_actions_composed` CTest | Notification source → daemon/provider action request → production parser/state/LVGL → correlated host result | Controlled source/provider adapter and native transport; no desktop IPC |
| `host_history_composed` CTest | Negotiated 30-minute history → production parser/state/LVGL → host browse/idle/dismiss input | Virtual time and transport; popup/archive, body limits, no-renewal sync/reconnect and replacement renewal |
| `smoke_recorder` CTest | Isolated production host policy → parser/state/LVGL, actual × round trip, timed retention and fresh process recovery | Native transport; fake IPC checks finish/error/pause ownership cleanup |

CTest also runs serialized replay and an SDL dummy-driver smoke. If uv is
unavailable, `host_composed`, `host_actions_composed`, `host_history_composed` and `smoke_recorder` are explicitly skipped; such a run does not satisfy
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

## Inspect the shared UI

```sh
/tmp/349-native-ui-build/native_ui --viewer --grouped
```

Mouse input supplies the same LVGL pointer path as replay. Window performance
does not measure device performance. The existing legacy demo uses `--viewer`
without `--grouped`.

## Replay and round trips

```sh
/tmp/349-native-ui-build/native_ui \
  --replay tools/native_ui/replay_smoke.json --artifacts /tmp/349-ui-replay
uv run --project host --frozen python tools/test_grouped_composed.py \
  --native /tmp/349-native-ui-build/native_ui_composed \
  --artifacts /tmp/349-host-composed
uv run --project host --frozen python tools/test_notification_actions_composed.py \
  --native /tmp/349-native-ui-build/native_ui_composed \
  --artifacts /tmp/349-host-actions-composed
uv run --project host --frozen python tools/test_notification_history_composed.py \
  --native /tmp/349-native-ui-build/native_ui_composed \
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
new mode and seeds three cards with expanded text and the default 30-minute
retention. Browse vertically, check inert horizontal/body input, then use × to
remove the cards and inspect `No recent notifications`. `status` records the
current view, and `finish` restores the normal daemon. No timed Home phase or
held-touch sequence is needed. The self-test also checks this history fixture's
seed, actual × round trip and last-card empty state through the native runner.
