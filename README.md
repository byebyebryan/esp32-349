# esp32-349

Projects and shared board support for the **Waveshare ESP32-S3-Touch-LCD-3.49 V2**.
The board has a 172 × 640 AXS15231B panel, used here in 640 × 172 landscape,
capacitive touch, 16 MiB flash and octal PSRAM. Firmware uses ESP-IDF v5.5.3
and LVGL 9.5.0.

## Projects

| Project | Purpose |
| --- | --- |
| [render-bench](projects/render-bench/README.md) | Bouncing-ball rendering benchmark, board bring-up and display performance experiments. Includes the optimization history and hardware findings. |
| [notification-panel](projects/notification-panel/README.md) | Always-on desktop notification panel with a clock, host telemetry, cached cards and touch actions. Includes the `349d` daemon, `349ctl`, desktop integration and acceptance records. |

Each project is a standalone ESP-IDF application with its own configuration,
partition table, dependency lock and build directory. Select the project by
working in its directory. One firmware project runs on the board at a time.

## Layout

```text
components/display_349/     shared panel, rendering pipeline, board I/O and touch
scripts/                   common ESP-IDF setup and environment activation
tools/                     shared board-component checks
projects/
  render-bench/            rendering benchmark and performance findings
  notification-panel/      notification firmware, host tools, design and tests
host/                      compatibility links for existing daemon installations
```

The shared [display component](components/display_349/README.md) contains the
optimized shadow-framebuffer and DMA pipeline used by both projects. Project
UI, host behavior and fonts stay with the application that uses them.

## Build a project

From the repository root, install and activate the common toolchain, then
choose a project:

```sh
./scripts/setup.sh
. scripts/env.sh
cd projects/notification-panel    # or projects/render-bench
idf.py build
```

Build outputs, `sdkconfig` and `managed_components/` are generated separately
inside each project. The checked-in `sdkconfig.defaults` and `dependencies.lock`
define that project's fresh configuration and dependency versions.

For flashing, use the intended board's serial path. If `349d` is running,
pause it first; see the [notification-panel flashing guide](projects/notification-panel/README.md#flashing-while-the-daemon-runs).
`render-bench` does not implement the daemon's host protocol.

## Shared component checks

Run from the repository root in a host-toolchain shell without EIM activation:

```sh
cc -std=c11 -Wall -Wextra -Werror -I components/display_349 \
  components/display_349/shadow_349.c tools/test_shadow_349.c \
  -o /tmp/349-shadow-tests
/tmp/349-shadow-tests
```

Application tests and hardware acceptance are documented in each project's
README. CI runs the notification-panel host and desktop-provider tests,
production native checks in Debug and Release, font audits, this shared
framebuffer regression, and ESP-IDF builds for both projects. Physical panel
and touch acceptance remain separate.

## Add a project

Create `projects/<name>/` with its own `main/`, `CMakeLists.txt`,
`sdkconfig.defaults`, partition table and README. Point `EXTRA_COMPONENT_DIRS`
at `${CMAKE_CURRENT_LIST_DIR}/../../components`, as the existing projects do.
Commit its generated dependency lock after the first successful build. Keep
application-specific assets, tools and integrations in that project; put code
in the shared components only when it provides board support or serves multiple
projects. Add the project to the table above.

## Existing checkout migration

The former root application is now `projects/notification-panel/`; the former
`examples/349-hello/` is now `projects/render-bench/`. Use the new project
directories for builds. Existing root build/configuration caches are left in
place and are not used by these projects.

The root [`host/` compatibility directory](host/README.md) preserves existing
service symlinks, editable Python installs and ignored local environments
during a Git update. New installations use the project's host directory. The
`349d`/`349ctl` names, configuration, saved USB pairing and wire protocol retain
their existing identities. Service migration commands are in the
[project setup guide](projects/notification-panel/README.md#host-setup).
