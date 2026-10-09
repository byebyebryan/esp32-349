# Notification-panel tools

Run commands from `projects/notification-panel/`. The
[testing guide](../docs/testing.md) describes the routine host/provider and
native checks; [setup](../docs/setup.md) covers installation and serial ownership.
Raw traces, captures and results belong in ignored `.cache/` directories.

## Routine checks and authoring

| Entry point | Purpose |
| --- | --- |
| [check_native_ui.py](check_native_ui.py) | Build/run production native checks in Debug and Release; [individual targets](native_ui/README.md) |
| [check_font_coverage.py](check_font_coverage.py) | Audit generated font repertoire and metrics |
| [generate_status_fonts.py](generate_status_fonts.py) | Regenerate fonts; sources, prerequisites and licenses are in the [font guide](../main/fonts/README.md) |
| [render_presentation.py](render_presentation.py) | Reproduce current native gallery media and provenance; [media guide](../docs/media/README.md) |

`test_*.c` and composed `test_*.py` files are automated fixtures. The `run_`
recorders and board probes below are operator tools. Routine checks do not
run their live modes, open hardware or reload a desktop plugin.

## Serial board probes

Select the exact board through its stable USB path and use the documented
[pause/probe/resume procedure](../docs/setup.md#flashing-while-the-daemon-runs).
These probes reset the board and replace its test cache; resume the daemon
afterward to restore normal state. They establish protocol/readback behavior,
not panel pixels or physical touch.

| Probe | Scope |
| --- | --- |
| [check_grouped_board.py](check_grouped_board.py) | Older grouped-mode compatibility: cache ordering, presentation generations, atomic staging failure and session recovery |
| [check_telemetry_board.py](check_telemetry_board.py) | Host telemetry against device readback; optional `--stress-readback` and `--history-stress` cover numeric bounds, link-stack headroom and offline retention |

```sh
board_port='/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_<serial>-if00'
349ctl pause
uv run --project host --frozen python tools/check_grouped_board.py \
  --port "$board_port" --artifacts .cache/board-grouped
349ctl resume
```

## Physical recorders

[run_grouped_smoke.py](run_grouped_smoke.py) owns its sticky pause and runs an
isolated host policy with synthetic cards. Its [native guide](native_ui/README.md#opt-in-physical-recorder)
explains setup, `--history`, operator commands and cleanup. It retains normal
daemon history while paused and resumes it on finish. Its offline `--self-test`
checks ownership cleanup through native transport.

[run_notification_actions_smoke.py](run_notification_actions_smoke.py) uses
the normal active daemon and DMS bridge. With an observer ready, `--live`
sends three owned desktop cards with/without a default action, records real
callbacks and device readback, and closes remaining fixtures on finish.
Operator commands are `cards`, `status` and `finish`; the session is bounded
to 15 minutes. No automatic activation or configuration change occurs.

```sh
uv run --project host --frozen python tools/run_notification_actions_smoke.py \
  --live --artifacts .cache/open-smoke
```

Its `--self-test` exercises callback-owner/ID filtering and cleanup failures
without contacting the desktop or board. Neither recorder marks physical
acceptance passed automatically; record the observer's findings separately.

## Desktop Open proof

[run_notification_actions_bridge.py](run_notification_actions_bridge.py) is
a live integration proof for the installed [DMS provider](../integrations/dms/349NotificationActions/README.md).
It sends owned desktop fixtures, binds/invokes their default actions, checks
replacement/close/release/duplicate behavior and reloads only that plugin.
It requires `--live` and closes remaining owned fixtures on exit. Because it
uses a synthetic bridge session and performs a reload, pause the normal daemon
for the trial and resume it afterward. Window focus and device touch are
separate acceptance gates.

```sh
349ctl pause
uv run --project host --frozen python tools/run_notification_actions_bridge.py \
  --live --artifacts .cache/bridge-proof
349ctl resume
```

The provider's [controlled notification producer](../integrations/dms/349NotificationActions/tools/controlled_notification.py)
is an interactive alternative for inspecting callbacks one notification at a
time; it does not activate an action itself.

## Connected soak recorder

[soak_recorder.py](soak_recorder.py) passively samples normal `349d` IPC and
its service journal. It does not open serial, reset the board, send fixtures
or change the service. Supply the retained ELF, expected build/SHA prefix,
exact device path, duration and output path. It checks continuity of service,
device uptime/heartbeats and link health, writing private JSONL.

```sh
mkdir -m 700 -p .cache/soak
uv run --project host --frozen python tools/soak_recorder.py \
  --output .cache/soak/run.jsonl --duration 3600 --interval 15 \
  --expected-build '<firmware-build>' --expected-sha-prefix '<ELF-prefix>' \
  --firmware-elf build/349-status.elf --device-path "$board_port"
```

The output directory must be owned by the current user and mode 0700, and the
output file must be new. Successful recording establishes only the observed
duration and conditions; it does not establish rendering, touch, suspend/wake
or a longer soak. Dated validation belongs in [STATUS.md](../STATUS.md).
