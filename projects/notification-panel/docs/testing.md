# Tests and evidence

[Project overview](../README.md)

Run application commands from `projects/notification-panel/`. Use the
[development guide](../../../docs/development.md#shared-component-checks) for
repository-level checks. [STATUS.md](../STATUS.md) records the dated source and
physical-check baseline; the [native UI guide](../tools/native_ui/README.md) details
individual runners. The [tools index](../tools/README.md) covers the optional
serial probes, physical recorders, bridge proof and connected soak recorder.

```sh
env -u DBUS_SESSION_BUS_ADDRESS uv run --project host --frozen pytest -q host/tests tools/tests
```

Covers framing, protocol, state, composition, sources, notification handling,
and daemon/IPC integration against a pty fake device. Clearing the desktop bus
address skips the three live D-Bus tests; omit `env -u DBUS_SESSION_BUS_ADDRESS`
only when desktop notification tests are intended. Tooling tests also verify
that native checks preserve caches belonging to other checkouts.

Install `dbus-broker` to run the isolated notification-server restart
regressions. These tests create a private bus and never use the desktop bus.
Locally they skip if the broker is absent; set
`STATUS349_REQUIRE_DBUS_BROKER=1` to make a missing broker fail instead.

The [CI workflow](../../../.github/workflows/checks.yml) runs these isolated host/tooling
tests on Python 3.11 and 3.14 with the private broker required, plus the JavaScript
desktop-action provider tests. Its ESP-IDF 5.5.3 matrix builds both firmware
projects from their defaults and dependency locks. The notification-panel job
also runs the shared framebuffer regression, font audit, and production native
checks in Debug and Release using the build's locked LVGL and SDK cJSON sources.
Physical acceptance remains a separate gate; a green CI run does not establish
device rendering or touch behavior.

From this project directory, native checks execute the same deck policy and
dashboard parser compiled into firmware using the host toolchain. Run them
in a shell without EIM activation: its `PATH` can select the ESP ULP assembler
for host GCC. Set the SDK location for header access only:

```sh
native_idf_root="${EIM_ROOT:-$HOME/.espressif}/${IDF_VERSION:-v5.5.3}/esp-idf"
cc -std=c11 -Wall -Wextra -Werror -I main \
  main/deck.c tools/test_deck.c -o /tmp/349-deck-tests
/tmp/349-deck-tests
cc -std=c11 -Wall -Wextra -Werror -I main \
  main/deck.c main/deck_input.c tools/test_deck_input.c -o /tmp/349-deck-input-tests
/tmp/349-deck-input-tests
cc -std=c11 -Wall -Wextra -Werror -I main -I "$native_idf_root/components/json/cJSON" \
  main/dashboard.c tools/test_dashboard.c "$native_idf_root/components/json/cJSON/cJSON.c" \
  -lm -o /tmp/349-dashboard-tests
/tmp/349-dashboard-tests
python tools/check_font_coverage.py
```

The shared shadow-framebuffer regression belongs to the board component; run
it from the repository root using the [shared check command](../../../docs/development.md#shared-component-checks).

The native LVGL fixture exercises the production deck through pointer input,
including hit testing, animation, and cache changes during a gesture. It needs
the managed LVGL dependency installed by the firmware build and ESP-IDF's
cJSON headers. From the same host-toolchain shell:

```sh
python tools/check_native_ui.py --cjson-include "$native_idf_root/components/json/cJSON"
# Optional desktop inspection of the same production UI:
.cache/native-ui/debug/native_ui --viewer --grouped
```

The runner builds Debug and Release with assertions enabled, then runs the
legacy UI, grouped gestures, serialized replay, production parser/state, SDL
smoke, USB receiver framing, sanitized legacy bar/media rendering, and composed
host → parser/state → LVGL → host-input checks. Captures
and traces live under each build's `artifacts/` directory. Defaults are the
checkout's ignored `.cache/native-ui/debug` and `.cache/native-ui/release`
directories. Overrides with a cache from another checkout are rejected before
either build; choose fresh paths rather than removing an unknown cache.
UI and protocol adapters substitute allocation, mutexes, RTC time, USB transport
and device identity. A separate RTC fixture runs the production driver against
simulated I2C registers, including oscillator-stop, invalid BCD/calendar values
and fallback/recovery cases. These checks do not emulate ESP32 task scheduling,
touch hardware or panel transfer.
See [the native test guide](../tools/native_ui/README.md) for individual runners. The [tools index](../tools/README.md) covers the optional
serial probes, physical recorders, bridge proof and connected soak recorder.

The backlight fixture checks exact timeout boundaries, screen following,
reconnect control, local brightness/manual-off precedence, shared button
debounce and the touch gate. Production parser checks validate display commands,
button state and readback. Physical checks must separately observe screen-off/on,
manual off and wake after the full five-minute daemon-loss interval. Screen-state
snapshots and serial counters do not establish visible behavior. See
[backlight policy](backlight.md) and [recorded validation](../STATUS.md).

Notification-boost checks cover the exact 30-second boundary, subsequent
arrivals restarting it, same-value sync preserving it, Brightness/off/reload
cancellation, disabled configuration and absence of deferred boosts. Host checks
exercise capability negotiation and ensure cached replay carries no event
marker. Production parser checks cover legacy/grouped accepted arrivals,
invalid/uncached cards and inert snapshots. On the selected board, observe a new
notification raising brightness for 30 seconds and returning to the selected
level, then press Brightness during a second boost to verify immediate cancellation.
[STATUS.md](../STATUS.md) records the dated user-confirmed behavior. Serial
boost/restore readback remains separate from physical illumination and button
acceptance.

For physical button acceptance, start at 50% with host screens on.
Press/release brightness four times: 75 → 100 → 25 → 50%. Verify a held button
does not repeat. Press Power to go dark, then brightness to select 75% while
staying dark; Power should return to automatic mode at 75%. Leave a local
choice through at least one 60-second sync and a daemon pause/resume. With the
host screen off, Power off/on must remain dark. Finally press RESET: a new boot
should restore 50% (or an explicit configured host level). Observe actual light
output as well as serial click counts; readback alone does not establish button
or optical acceptance. Avoid holding BOOT during RESET unless entering download
mode is intended.
