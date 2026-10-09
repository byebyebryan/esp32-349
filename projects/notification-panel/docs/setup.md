# Setup and daily commands

Run the commands below from `projects/notification-panel/`, including when
reading this guide from its `docs/` directory. Shared toolchain and checkout
migration details are in the [development guide](../../../docs/development.md).
Return to the [project overview](../README.md).

## Build and flash

Install the [ESP-IDF Installation Manager CLI](https://docs.espressif.com/projects/idf-im-ui/en/latest/)
and [uv](https://docs.astral.sh/uv/getting-started/installation/) first.
The host daemon requires Linux and a desktop D-Bus session.

```sh
../../scripts/setup.sh
. ../../scripts/env.sh
idf.py build
board_port='/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_<serial>-if00'
idf.py -p "$board_port" flash
```

Replace `<serial>` with the exact board's identity from `/dev/serial/by-id/`.
If `349d` owns it, follow the
[pause/resume procedure](#flashing-while-the-daemon-runs).

Activation puts ESP-IDF's executable tools on `PATH` as well as loading EIM's
shell functions, so subprocess invocations such as `rtk proxy idf.py build`
work in the same shell.

Existing local `sdkconfig` files created before the CJK fallback need the
Source Han Sans 14/16 px font options enabled; `sdkconfig.defaults` selects
them for a fresh configuration. The new rail also requires Montserrat 40 px.
The generated 16/20/22 px text and 80 px clock fonts are checked in.

## Flashing while the daemon runs

The daemon holds the serial port. Flashing resets the chip; use the
sticky pause so a `Restart=always` unit cannot grab the port mid-flash.

Retain a rollback application and copies of configuration and saved pairing
before updating. Verify a retained application against the selected board or
read its current flash before writing; a local build alone does not identify
the running firmware. Keep device identities, backup paths and flash logs with
the local rollback artifacts.

```sh
349ctl pause          # releases the tty (flag file survives daemon restarts)
# Back up or verify the selected board while the daemon is paused.
idf.py -p "$board_port" flash  # exact path selected above
# If host code changed, run the update commands below before resuming.
349ctl resume         # verifies and reconnects without an extra device reset
```

For an application-only update with an unchanged partition table, use
`idf.py -p "$board_port" app-flash` in place of `flash`; it writes the application
without replacing the bootloader or partition table.

If host source or dependencies changed, load the updated code after flashing
and before `349ctl resume`, while the sticky pause is still set:

```sh
uv sync --project host --frozen
systemctl --user restart 349d
```

A configuration reload does not load changed Python code. Restarting the
daemon clears its retained in-memory cards; ordinary pause/resume preserves
them. Neither operation changes saved USB pairing.

## Host setup

```sh
uv sync --project host --frozen   # installs the locked host and test dependencies
```

The daemon remains `349d`, the CLI remains `349ctl`, and configuration and saved
USB pairing stay under the existing `349d` paths. The repository's root `host/`
compatibility links preserve older installations and local environments.
When updating an existing service link,
run the symlink and `daemon-reload` commands below from this project directory;
the next service start uses the new working directory.

## Running

```sh
uv run --project host 349d -v           # foreground, debug logging
uv run --project host 349ctl status     # talk to the running daemon
uv run --project host 349ctl pair       # one-time setup; needs a running daemon
```

As a user service:

```sh
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/host/349d.service" ~/.config/systemd/user/349d.service
systemctl --user daemon-reload
systemctl --user enable --now 349d
journalctl --user -u 349d -f
```

The unit resolves `uv` from the user manager's `PATH` and needs
`DBUS_SESSION_BUS_ADDRESS` there too (normal on a graphical login; check both
with `systemctl --user show-environment`).

## 349ctl

```
349ctl status                 daemon/link state, revision, notification count
349ctl pair                   discover and save the display's USB identity once
349ctl pair --replace         explicitly discover and save a replacement
349ctl device-cards           device cache, deck focus and optional backlight readback
349ctl text "hello"           send a text message to the device
349ctl notify "summary" [body]  inject a test notification
349ctl pause | resume         release/reconnect the serial port
349ctl reload                 reload ~/.config/349d/config.toml
349ctl log [-n N]             recent device log lines
```

Only one `349d` can own an `XDG_RUNTIME_DIR` at a time. `349ctl status` keeps
`port` as the effective configured target and reports the open serial path as
`active_port` (`null` while disconnected). Reloading a changed target reconnects
and syncs retained in-memory cards; a daemon `--port` override stays in effect.

When the daemon is stopped, use `349ctl --port "$board_port" hello` for direct
device access. `ping`, `text` and `listen` are also available with that port.

## Serial discovery

Run `349ctl pair` once with the daemon running and the display connected. Setup
enumerates Espressif native USB Serial/JTAG interfaces with VID/PID `303a:1001`,
then requires a compatible hello and a fresh ping/pong response. It skips busy
ports and tries each eligible device once. It saves the verified USB serial to
`$XDG_STATE_HOME/349d/device.json` (default `~/.local/state/349d/device.json`).
The generic USB product, VID/PID and MAC address prefix cannot distinguish the
349 from another ESP32-S3 board; the handshake makes that distinction.

Boot and reconnect resolve only the saved serial, verify it again and sync
retained host state through the same open handle. They do not reset the board
or probe other devices. An absent paired display stays disconnected until it
returns. With no saved binding, the daemon waits for explicit setup.
`349ctl status` reports the pairing state, saved serial, last setup result and
link errors; `active_port` remains null until verification succeeds.

Repeating `349ctl pair` with a valid binding is idempotent, including when the
display is absent. Use `349ctl pair --replace` to search again. Failed setup
keeps the old binding; an invalid state file requires explicit replacement.
Pairing is rejected while paused. A CLI wait timeout leaves the daemon's setup
operation running; inspect status or pause to cancel it.

An explicit `[link].port` or daemon `--port` overrides the saved binding and
validates only that target, with no fallback. To migrate an existing pin, run
`349ctl pair` while connected, verify the saved serial in status, then remove
the pin and reload. Initial pairing reuses the verified session and confirms
a fresh pong. PTY targets remain supported for development but require USB
metadata to become a persistent binding. See
[USB ownership](architecture.md#usb-session-ownership) for persistence and
cancellation decisions, [handshake](protocol.md#handshake-and-capabilities)
for verification bounds, and [STATUS.md](../STATUS.md) for dated validation.

## Project layout

```
main/               ESP-IDF app: link, proto, state, ui, rtc
host/               Python daemon (349d) and control CLI (349ctl)
tools/              firmware checks, native UI runner and test fixtures
docs/               user guides, architecture, protocol, UI and backlight design
integrations/        optional desktop notification action bridge
../../components/display_349/   shared panel/LVGL/touch component
../../scripts/       common ESP-IDF v5.5.3 setup and environment activation
```
