# esp32-349

[![Project checks](https://github.com/byebyebryan/esp32-349/actions/workflows/checks.yml/badge.svg)](https://github.com/byebyebryan/esp32-349/actions/workflows/checks.yml)

Projects, examples and shared board support for the
**Waveshare ESP32-S3-Touch-LCD-3.49 V2**, built with ESP-IDF and LVGL.

[Build](#build) · [Hardware](#hardware) · [Development](docs/development.md)

## Project gallery

### Notification panel

![Native demo: notifications arrive, a vertical swipe selects an earlier card, and touch dismissal returns to the empty pane](projects/notification-panel/docs/media/notification-demo.gif)

Keep a clock and CPU/MEM/network readings beside recent desktop notifications.
Swipe through cards, dismiss them, or return to an app with the optional Open
action. English and common Simplified Chinese text, styled Latin bodies and
remembered USB pairing make it useful as an everyday desk display.

[Project and setup](projects/notification-panel/README.md) · [Display behavior](projects/notification-panel/docs/behavior.md) · [Media source](projects/notification-panel/docs/media/README.md)

### Rendering benchmark

![Native render-bench demo: a ball moves and reflects at the edges of the 640 by 172 landscape scene](projects/render-bench/docs/media/benchmark-demo.gif)

A small bouncing-ball scene that exercises the display pipeline. Recorded
board trials reached **about 45 fps** with low CPU use. Its shadow framebuffer,
transpose of changed regions and GDMA staging form the shared rendering path
used by both projects.

[Project and build](projects/render-bench/README.md) · [Rendering findings](projects/render-bench/docs/rendering.md) · [Media source](projects/render-bench/docs/media/README.md)

*Both previews use production LVGL code with virtual time; the notification
demo also uses synthetic messages and pointer input. Playback timing is
separate from measured panel performance.*

Each project is a standalone firmware application. One runs on the board at a
time; both use the shared [display component](components/display_349/README.md).

## Hardware

Built for the [Waveshare ESP32-S3-Touch-LCD-3.49](https://www.waveshare.com/esp32-s3-touch-lcd-3.49.htm),
**V2 revision**: ESP32-S3, 16 MiB flash, 8 MiB PSRAM and a 172 × 640
AXS15231B touch display. The current applications use 640 × 172 landscape. See the
[official hardware documentation](https://docs.waveshare.com/ESP32-S3-Touch-LCD-3.49)
for version identification, pinouts and schematics.

## Build

Install the [ESP-IDF Installation Manager CLI](https://docs.espressif.com/projects/idf-im-ui/en/latest/),
then run from the repository root:

```sh
./scripts/setup.sh
. scripts/env.sh
idf.py -C projects/render-bench build
# or: idf.py -C projects/notification-panel build
```

Follow the selected project's guide for flashing and any host dependencies.

## Documentation

- [Development](docs/development.md): toolchain, repository layout, shared checks and checkout migration.
- [Shared display component](components/display_349/README.md): panel, framebuffer, DMA and touch support.
- [Adding a project](docs/development.md#add-a-project): application boundaries and integration checks.
- [Presentation media](docs/media/README.md): gallery sources, regeneration and capture guidance.
