# esp32-349

[![Project checks](https://github.com/byebyebryan/esp32-349/actions/workflows/checks.yml/badge.svg)](https://github.com/byebyebryan/esp32-349/actions/workflows/checks.yml)

An always-on desktop notification display, a rendering benchmark, and shared
board support for the **Waveshare ESP32-S3-Touch-LCD-3.49 V2**.

![ESP32-349 notification panel with a clock, host telemetry and a styled notification](docs/media/hero.png)

*Native LVGL render with demo data. [Media provenance](docs/media/README.md).*

The panel keeps a clock and host telemetry beside recent desktop notifications.
Swipe vertically to browse, tap × to dismiss, and optionally open a desktop
action through the DMS bridge. USB pairing remembers the specific board across
reconnects. It retains up to 32 cards for ten minutes and supports English and
common Simplified Chinese text, with Latin bold/italic body spans.

## Hardware

Built for the [Waveshare ESP32-S3-Touch-LCD-3.49](https://www.waveshare.com/esp32-s3-touch-lcd-3.49.htm),
**V2 revision**, with a 640 × 172 landscape UI. See the
[official hardware documentation](https://docs.waveshare.com/ESP32-S3-Touch-LCD-3.49)
for version identification, pinouts and schematics.

## Projects

| Project | Purpose | Guide |
| --- | --- | --- |
| **notification-panel** | Clock, host telemetry, recent notifications and touch actions | [Overview and setup](projects/notification-panel/README.md) |
| **render-bench** | Bouncing-ball benchmark and display pipeline experiments | [Build and findings](projects/render-bench/README.md) |

Each is a standalone ESP-IDF application. One firmware project runs on the
board at a time; both share [display_349](components/display_349/README.md).

## In motion

![Native demo: notifications arrive, a vertical swipe selects an earlier card, and touch dismissal returns to the empty pane](docs/media/notification-demo.gif)

*Native demo with synthetic notifications and virtual pointer input/time.
[Source and regeneration](docs/media/README.md).*

## Build

Install the [ESP-IDF Installation Manager CLI](https://docs.espressif.com/projects/idf-im-ui/en/latest/),
then run from the repository root:

```sh
./scripts/setup.sh
. scripts/env.sh
cd projects/notification-panel    # or projects/render-bench
idf.py build
```

Use the [notification-panel setup guide](projects/notification-panel/docs/setup.md)
for flashing, host installation and pairing. The host requires Linux and uv.
The benchmark does not implement the daemon's host protocol.

## Documentation

- [Development](docs/development.md): toolchain, repository layout, shared checks and checkout migration.
- [Validation checkpoint](projects/notification-panel/STATUS.md): automated results, deployment and observed device behavior.
- [Presentation media](docs/media/README.md): native image/animation sources and regeneration.
