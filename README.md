# esp32-349

[![Project checks](https://github.com/byebyebryan/esp32-349/actions/workflows/checks.yml/badge.svg)](https://github.com/byebyebryan/esp32-349/actions/workflows/checks.yml)

Projects, examples and shared board support for the
**Waveshare ESP32-S3-Touch-LCD-3.49 V2**, built with ESP-IDF and LVGL.

## Projects

| Project | Highlights | Explore |
| --- | --- | --- |
| **render-bench** | 640 × 172 bouncing-ball benchmark; recorded trials reached **about 45 fps** with low CPU use. Dirty-rectangle transpose, a shadow framebuffer and GDMA staging drive the shared display pipeline. | [Build and rendering findings](projects/render-bench/README.md) |
| **notification-panel** | Clock and CPU/MEM/network telemetry beside recent desktop notifications. Vertical swipe browsing, touch dismissal, optional Open, English and common Simplified Chinese text, styled Latin body text and remembered USB pairing. | [Demo, setup and behavior](projects/notification-panel/README.md) |

Each is a standalone ESP-IDF application. One firmware project runs on the
board at a time; both share [display_349](components/display_349/README.md).

### Notification-panel preview

![Native demo: notifications arrive, a vertical swipe selects an earlier card, and touch dismissal returns to the empty pane](projects/notification-panel/docs/media/notification-demo.gif)

*Native LVGL render with demo data and virtual pointer input/time.
[Media provenance and regeneration](projects/notification-panel/docs/media/README.md).*

Full guides, additional previews and validation records live with each project.

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
- [Presentation media](docs/media/README.md): collection artwork and project-owned demos.
