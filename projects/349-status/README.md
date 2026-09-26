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
cache_limit = 32                # newest retained cards; active cards on older firmware
popup_timeout_ms = 10000          # fallback when an app requests server default (-1)
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
of zero keeps no cards on the device while still reporting their retained
count (active count for older firmware).
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

`349ctl notify` uses a five-second test presentation by default. Mirrored
notifications follow a positive app timeout; for the server-default timeout (`-1`),
normal/low cards use `popup_timeout_ms` and
critical cards use `critical_popup_timeout_ms`; an app timeout of `0` keeps
automatic presentation until explicit close. With `grouped-ui-v1`, timeout
returns to Home and retains the notification for browsing; desktop expiry
also retains it. Explicit desktop close or × removes the record. Older peers
keep the active-card behavior, where timeout removes the board card.

Grouped firmware and the host retain the newest 32 records in daemon memory.
A smaller configured device cache leaves older retained records uncached;
evicted records are not included in the overflow count. A chunked full sync restores the cache after a
reconnect; new cards, replacements, and closes remain incremental. An overflow
count describes retained cards that are not cached and cannot be browsed.
See the [cache implementation plan](design/card-cache-plan.md). Use
`349ctl device-cards` to read the device's ordered cached IDs and overflow
count without resetting the USB link.

Firmware advertising `grouped-ui-v1` uses a persistent 160 px telemetry rail
and a 480 px content area. Swipe horizontally between Home (large clock/date)
and Notifications; swipe vertically within Notifications to snap one whole
card. Both directions have bounds. Empty Notifications remains reachable and
shows a clear empty state. Short drags return to the original view,
and deliberate flicks advance one card. A multi-card view provides two body
lines and a next-title peek; a singleton expands to three body lines. Tapping
the bottom peek advances. Deliberate navigation cancels automatic return;
normal arrivals do not interrupt manual browsing, while critical attention
waits for the gesture to settle. Tap × to dismiss
locally; tapping the body does nothing. The position count includes only locally visible,
cached cards; `+N uncached` is a separate count, not a navigation target.
CPU, memory, local network link, and conditional battery appear in the rail;
volume and Bluetooth changes appear briefly. Bluetooth probing is optional
and failure leaves that reading unavailable. Details and acceptance scope are
in the [grouped direction](design/grouped-ui-plan.md) and
[wire contract](design/grouped-ui-protocol.md). The previous horizontal deck
remains the fallback for older hosts. Its recorded swipe trials measured
about 16–20 updates/s; the user reports clean, responsive motion. The initial
25 updates/s tuning target was not reached. Exact build and trial scope are
recorded in [ACCEPTANCE.md](ACCEPTANCE.md).

Rendering uses a full PSRAM buffer in DIRECT mode and the existing rotated
PSRAM shadow, then sends one complete panel frame. LVGL drawing runs
synchronously inside the display lock. The small internal-buffer experiment
was reverted: faster conversion was largely offset by slower drawing, while
using scarce internal RAM. Basic debug timing reports frame period and
transfer time; the health log includes heap and LVGL stack headroom.

The grouped UI has passed automated host, production parser/state, real LVGL
replay, Starship serial and short physical gates on firmware `be39d5e49`;
the enlarged close control passed a focused follow-up on `604fd70da`;
see [acceptance evidence](design/grouped-ui-acceptance.md) and
[the execution plan](design/grouped-ui-execution.md).

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
python tools/check_native_ui.py --cjson-include "$IDF_PATH/components/json/cJSON"
# Optional desktop inspection of the same production UI:
/tmp/349-native-ui-build/native_ui --viewer --grouped
```

The runner builds Debug and Release with assertions enabled, then runs the
legacy UI, grouped gestures, serialized replay, production parser/state, SDL
smoke, and composed host → parser/state → LVGL → host-input checks. Captures
and traces live under each build's `artifacts/` directory. Platform adapters
substitute allocation, mutexes, RTC time, USB transport and device identity;
they do not emulate ESP32 task scheduling, touch hardware or panel transfer.
See [the native test guide](tools/native_ui/README.md) for individual runners.
