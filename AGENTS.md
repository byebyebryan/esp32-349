# Repository guidance

## Scope and layout

This repository targets the Waveshare ESP32-S3-Touch-LCD-3.49 **V2**.
`projects/notification-panel` and `projects/render-bench` are standalone
ESP-IDF applications; run firmware builds from the selected project directory.
The repository root is not an ESP-IDF application.

Keep application UI, fonts, host tools and integrations with their project.
Shared board support belongs in `components/display_349`. The root `host/`
symlinks support existing installations; preserve them and the `349d`/`349ctl`
configuration and pairing identities.

## Changes and validation

Read the relevant project guide and design record before changing behavior.
[Development](docs/development.md) describes toolchain setup and shared checks;
[application testing](projects/notification-panel/docs/testing.md) lists host,
provider, firmware and native checks. Run checks appropriate to the changed
paths, and preserve unrelated work and checkout-specific caches.

Keep READMEs as concise entry points. Update focused guides alongside behavior
changes, and preserve dates and build identities in historical acceptance
records. Use [STATUS.md](projects/notification-panel/STATUS.md) as a dated
checkpoint rather than a claim about current deployment or connection state.

## Hardware and presentation

Only flash firmware or change live host services when the user authorizes it.
Identify the exact board through its stable USB serial path, retain a backup,
and follow the documented daemon pause/flash/resume procedure.

Native and serial checks do not establish physical rendering or touch
acceptance. Label synthetic media accordingly. Follow the
[media guide](docs/media/README.md) to regenerate assets when a manifest-listed
input changes; keep raw captures and build output in ignored cache directories.
