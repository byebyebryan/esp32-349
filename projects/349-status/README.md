# 349-status

Always-on 640x172 desk display for the ESP32-S3-Touch-LCD-3.49 V2: mirrors
host telemetry and desktop notifications over USB-Serial-JTAG; touch
browses the cached notification deck and dismisses cards. Design, milestones and hardware findings live in
[PLAN.md](PLAN.md).

## Layout

```
main/               ESP-IDF app: link, proto, state, ui, rtc
host/               Python daemon (349d) and control CLI (349ctl)
components/display_349/   shared panel/LVGL/touch component (see ../../components)
```

## Build and flash

```sh
. ../../scripts/env.sh
idf.py build
idf.py -p /dev/ttyACM0 flash
```

Existing local `sdkconfig` files created before the CJK fallback need the
Source Han Sans 14/16 px font options enabled; `sdkconfig.defaults` selects
them for a fresh configuration. The new rail also requires Montserrat 40 px.
The generated 20/22 px text and 80 px clock fonts are checked in.

## Host setup

```sh
cd host
uv sync                 # creates .venv, installs pyserial(-asyncio), dbus-next
```

## Running

```sh
uv run 349d -v                    # foreground, debug logging
uv run 349ctl status              # talk to the running daemon
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
349ctl device-cards           device cache and optional deck focus readback
349ctl text "hello"           send a text message to the device
349ctl notify "summary" [body]  inject a test notification
349ctl pause | resume         release/reconnect the serial port
349ctl reload                 reload ~/.config/349d/config.toml
349ctl log [-n N]             recent device log lines
```

Direct-to-device commands when the daemon is stopped: `349ctl --port /dev/ttyACM0
hello|ping|text|listen`.

## Config

`~/.config/349d/config.toml` (or `349d --config FILE`):

```toml
[link]
# port = "/dev/serial/by-id/..."   # default: auto-detect

[daemon]
tick_s = 1.0
sync_interval_s = 60.0

[notifications]
mode = "mirror"            # mirror | off (consume is not implemented)
device_dismiss = "local"   # local | propagate
max_visible = 3                 # legacy firmware snapshot cap
cache_limit = 32                # active cards cached by capable firmware
popup_timeout_ms = 5000           # fallback when an app requests server default (-1)
critical_popup_timeout_ms = 0     # 0 keeps critical cards until closed
ignore_apps = ["KeePassXC", "Bitwarden", "1Password"]

[bar]
# Horizontal zones for the legacy UI; the dashboard rail is typed.
preset = [
  { id = "clock", kind = "clock", w = 80, format = "%H:%M" },
  { id = "spacer", kind = "spacer", w = 0 },
  { id = "cpu", kind = "text", w = 60 },
  { id = "mem", kind = "progress", w = 70 },
  { id = "vol", kind = "progress", w = 70 },
  { id = "batt", kind = "text", w = 60 },
]
```

`max_visible` is limited to 0–8 for older firmware. `cache_limit` is limited
to 0–32 for firmware advertising `card-sync-v1`; its default is 32. A value
of zero keeps no cards on the device while still reporting their active count.
The bar preset can contain at most eight zones. The configured widths must
fit the 624 px content area including 8 px gaps; a nonspacer with `w = 0`
uses 60 px and a spacer uses flex space.
Invalid configuration is rejected rather than silently dropping zones or
sending a frame the device cannot accept. Reload after editing with
`349ctl reload` (or `systemctl --user reload 349d`).

V1 uses Montserrat with a bundled Source Han Sans CJK fallback and a generated
punctuation/symbol subset for bar and notification text. The subset covers
typographic quotes (including `’`), arrows, math signs, shapes, and dingbats;
see [font sources and licenses](main/fonts/README.md). Latin accents become
base letters, while nondecomposing Latin-1 and Latin Extended-A letters have
glyphs. Glyphs outside the font set show a visible placeholder. The
[coverage audit](PLAN.md) describes remaining script gaps. Nerd Font Private
Use icons and color emoji are not included.

`349ctl notify` cards expire after about five seconds by default. Mirrored
cards follow a positive app timeout; for the server-default timeout (`-1`),
normal/low cards use `popup_timeout_ms` and
critical cards use `critical_popup_timeout_ms`; an app timeout of `0` keeps
the card until explicit close. A timeout hides only the board card: the
desktop notification daemon keeps its notification-center history.
Desktop close and replacement still update the board immediately.

Capable firmware keeps up to the newest 32 active cards locally for the
side-peek deck. The host remains responsible for expiry and retains any cards
beyond the device cache. A chunked full sync restores the cache after a
reconnect; new cards, replacements, and closes remain incremental. An overflow
count describes active cards that are not cached and cannot yet be browsed.
See the [cache implementation plan](design/card-cache-plan.md). Use
`349ctl device-cards` to read the device's ordered cached IDs and overflow
count without resetting the USB link.

Firmware advertising `dashboard-v1` uses a 160 px telemetry rail and a
480 px content area. Idle content is a large clock/date; an active notification
uses a full-height foreground card with up to three body lines. With several
cards, drag left/right to browse the next/previous card; release to snap one
card. Short drags return to the original card, and a deliberate flick can
advance one card. Tapping the right-hand peek also advances. Tap × to dismiss
locally; tapping the body does nothing. The position count includes only locally visible,
cached cards; `+N uncached` is a separate count, not a navigation target.
CPU, memory, local network link, and conditional battery appear in the rail;
volume and Bluetooth changes appear briefly. Bluetooth probing is optional
and failure leaves that reading unavailable. Details and acceptance scope are
in the [UI checkpoint](design/ui-deck-plan.md) and
[swipe plan](design/swipe-deck-plan.md). The recorded swipe trials measured
about 16–20 updates/s; the user reports clean, responsive motion. The initial
25 updates/s tuning target was not reached. Exact build and trial scope are
recorded in [ACCEPTANCE.md](ACCEPTANCE.md).

Rendering uses a full PSRAM buffer in DIRECT mode and the existing rotated
PSRAM shadow, then sends one complete panel frame. LVGL drawing runs
synchronously inside the display lock. The small internal-buffer experiment
was reverted: faster conversion was largely offset by slower drawing, while
using scarce internal RAM. Basic debug timing reports frame period and
transfer time; the health log includes heap and LVGL stack headroom.

The [proposed next UI](design/grouped-ui-plan.md) keeps the rail and introduces
horizontal Home/Notifications groups with vertical notification-card browsing.
It separates temporary presentation from bounded retention: timeout returns
to Home, and the card remains browsable. This is a design proposal; the current
expiry and horizontal card-navigation behavior above remain implemented.

The daemon sends a ping every four seconds even when the bar does not change.
While the board stays powered, it shows `host asleep` when USB activity stops
and `host disconnected` when USB is active but host messages stop for ten
seconds. If the host cuts USB power during sleep, the board turns off instead;
a full sync restores the display when it powers up and reconnects.
The dashboard UI keeps the clock visible and marks the rail readings stale
while showing its connection message in the right content area.

## Flashing while the daemon runs

The daemon holds the serial port, and opening it resets the chip anyway. Use the
sticky pause so a `Restart=always` unit cannot grab the port mid-flash:

```sh
349ctl pause          # releases the tty (flag file survives daemon restarts)
idf.py -p /dev/ttyACM0 flash
349ctl resume         # reconnects; costs one device reset
```

## Tests

```sh
cd host && uv run pytest -q
```

Covers framing, protocol, state, composition, sources, notification handling,
and daemon/IPC integration against a pty fake device.

From this project directory, native checks execute the same deck policy and
dashboard parser compiled into firmware (with the IDF environment loaded):

```sh
cc -std=c11 -Wall -Wextra -Werror -I main \
  main/deck.c tools/test_deck.c -o /tmp/349-deck-tests
/tmp/349-deck-tests
cc -std=c11 -Wall -Wextra -Werror -I main \
  main/deck.c main/deck_input.c tools/test_deck_input.c -o /tmp/349-deck-input-tests
/tmp/349-deck-input-tests
cc -std=c11 -Wall -Wextra -Werror -I main -I "$IDF_PATH/components/json/cJSON" \
  main/dashboard.c tools/test_dashboard.c "$IDF_PATH/components/json/cJSON/cJSON.c" \
  -lm -o /tmp/349-dashboard-tests
/tmp/349-dashboard-tests
python tools/check_font_coverage.py
cc -std=c11 -Wall -Wextra -Werror -I ../../components/display_349 \
  ../../components/display_349/shadow_349.c tools/test_shadow_349.c \
  -o /tmp/349-shadow-tests
/tmp/349-shadow-tests
```

The native LVGL fixture exercises the production deck through pointer input,
including hit testing, animation, and cache changes during a gesture. It needs
the managed LVGL dependency installed by the firmware build and ESP-IDF's
cJSON headers (`IDF_PATH`, or `-DCJSON_INCLUDE_DIR=/path/to/cJSON`):

```sh
cmake -S tools/native_ui -B /tmp/349-native-ui-build
cmake --build /tmp/349-native-ui-build --target native_ui -j 4
/tmp/349-native-ui-build/native_ui
```

These checks do not measure the panel's touch reliability or animation speed.
