# Development and repository layout

For the hardware collection and available projects, see the [repository README](../README.md).
Repository maintenance guidance is in [AGENTS.md](../AGENTS.md).
The commands below start at the repository root unless a project directory is
selected explicitly.

## Hardware and toolchain

Built for the [Waveshare ESP32-S3-Touch-LCD-3.49](https://www.waveshare.com/esp32-s3-touch-lcd-3.49.htm),
**V2 board revision**. The [official documentation](https://docs.waveshare.com/ESP32-S3-Touch-LCD-3.49)
includes version identification, pinouts, schematics and vendor examples.
V2 has a `Rev1.1` PCB marking or a `V2` case label; V1 has different backlight
and reset wiring. The shared component uses the V2 pin map.

| Hardware | Project configuration |
| --- | --- |
| AXS15231B IPS capacitive-touch panel | 172 × 640 native; **640 × 172 landscape** UI |
| ESP32-S3, 16 MiB flash, 8 MiB octal PSRAM | ESP-IDF **v5.5.3**, LVGL **9.5.0** |
| Native USB Serial/JTAG | Host connection, firmware flashing and logs |
| PCF85063 RTC | On-board clock available to applications |

## Build a project

Install the [ESP-IDF Installation Manager CLI (`eim`)](https://docs.espressif.com/projects/idf-im-ui/en/latest/)
first. For the notification panel's Linux host, install
[uv](https://docs.astral.sh/uv/getting-started/installation/); it manages the
Python 3.11+ environment. Host telemetry and notification mirroring use Linux
`/proc` and the desktop session's D-Bus.

From the repository root:

```sh
./scripts/setup.sh
. scripts/env.sh
cd projects/notification-panel    # or projects/render-bench
idf.py build
```

`sdkconfig.defaults` and `dependencies.lock` define each project's fresh
configuration and dependency versions. Build output, local `sdkconfig` and
`managed_components/` stay in that project and are ignored by Git.

For flashing, select the intended board's serial path and follow the selected
project's guide. If notification-panel's `349d` owns the port, use its
[daemon pause/resume procedure](../projects/notification-panel/docs/setup.md#flashing-while-the-daemon-runs).
Leave the daemon paused while running firmware that does not implement its
host protocol, including render-bench.

## Repository map

```text
components/display_349/     shared panel, rendering pipeline, board I/O and touch
projects/notification-panel/ firmware, Linux host, desktop bridge, docs and tests
projects/render-bench/      rendering benchmark and hardware findings
scripts/                   common ESP-IDF setup and environment activation
tools/                     shared framebuffer and documentation checks
docs/                      shared development guide and presentation media
host/                      compatibility links for existing daemon installations
```

The [display component](../components/display_349/README.md) owns the shadow
framebuffer and DMA pipeline used by both applications. UI, host behavior and
fonts stay with the project that uses them.

## Validation

[Project checks](../.github/workflows/checks.yml) build both firmware applications,
run isolated host tests on Python 3.11 and 3.14, test the desktop action provider,
and exercise production native UI/protocol/RTC paths in Debug and Release.
They also audit font coverage, shared framebuffer behavior, documentation
links and presentation asset provenance.

### Shared component checks

Run the shared checks from the repository root in a host-toolchain shell
without EIM activation:

```sh
python3 tools/check_docs.py
cc -std=c11 -Wall -Wextra -Werror -I components/display_349 \
  components/display_349/shadow_349.c tools/test_shadow_349.c \
  -o /tmp/349-shadow-tests
/tmp/349-shadow-tests
```

Application checks are in the
[notification-panel test guide](../projects/notification-panel/docs/testing.md).
The [validation checkpoint](../projects/notification-panel/STATUS.md) distinguishes
automated checks, firmware deployment and observed panel/touch behavior.

## Add a project

Give each application its own `main/`, `CMakeLists.txt`,
`sdkconfig.defaults`, partition table and README. Set `EXTRA_COMPONENT_DIRS` to
`${CMAKE_CURRENT_LIST_DIR}/../../components` and commit its dependency lock.
Add it to the root project catalog and the firmware CI matrix. Keep its guide,
behavior, tests and demos in its project directory. Shared code should provide
board support or serve multiple applications. The root README introduces the
hardware and collection; application features and acceptance records belong
with the application.

Keep machine-specific caches and captures under ignored `.cache/` directories.
Publish selected demos under `projects/<name>/docs/media/`, with regeneration
instructions and a `manifest.json` containing repository-relative
`source_sha256` inputs and media-relative `files` hashes/sizes. The shared
documentation check discovers each project's manifest. Generated fonts and
their source/license records stay with the owning project; see the
[notification-panel example](../projects/notification-panel/main/fonts/README.md).

## Existing checkout migration

The former root application is now `projects/notification-panel/`; the former
`examples/349-hello/` is now `projects/render-bench/`. Use those directories
for builds. Old root build/configuration caches are local migration artifacts.

The [`host/` compatibility directory](../host/README.md) preserves older service
symlinks, editable Python installs and ignored local environments. New
installations use the project host directory. `349d`, `349ctl`, configuration,
saved USB pairing and the wire protocol keep their existing identities.
