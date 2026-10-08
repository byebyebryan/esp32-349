# Notification-panel presentation media

The notification-panel README uses the production native LVGL fixture
to show the current UI without requiring a connected board. The screen pixels
come from the firmware parser/state/UI and checked-in fonts. The scenario uses
synthetic notifications, telemetry, action availability, time and pointer input.

| Asset | Content |
| --- | --- |
| [hero.png](hero.png) | Exact screen render at 2× integer scale, framed with project typography |
| [notification-demo.gif](notification-demo.gif) | Empty pane → English notification → Chinese notification → vertical browse → touch dismissal → empty pane |
| [notification.png](notification.png) | Styled English body with simulated Open availability |
| [multilingual.png](multilingual.png) | Common Simplified Chinese text and a next-card peek |
| [empty.png](empty.png) | Empty history with clock and telemetry |
| [stale.png](stale.png) | Stale readings with a cached card still visible |

These are native renders. The hero's border is a graphic frame, rather than a
model of the physical enclosure. Gesture frames follow virtual pointer input;
caption holds are arranged for readability. GIF playback does not establish
touch latency or panel frame rate. Actual observations belong to the project's
[validation and acceptance records](../../STATUS.md).

## Regenerate

Run from the repository root, after an IDF build has fetched the locked LVGL
dependency. Native builds need CMake, a host C compiler, pkg-config and SDL2
development headers. Use a shell without EIM activation:

```sh
native_idf_root="${EIM_ROOT:-$HOME/.espressif}/${IDF_VERSION:-v5.5.3}/esp-idf"
python3 projects/notification-panel/tools/check_native_ui.py \
  --cjson-include "$native_idf_root/components/json/cJSON"
uv run --project projects/notification-panel/host --frozen --with pillow==12.0.0 \
  python projects/notification-panel/tools/render_presentation.py
python3 tools/check_docs.py
```

Pillow is only an authoring dependency supplied through uv's temporary overlay;
it is not added to the host daemon's dependency lock. The generator builds the
selected `native_ui_composed` target before rendering. Its assertions require
actual history negotiation, the selected card after swiping, dismiss inputs,
the final empty deck, and stale readback. It opens no board, desktop bus or live
daemon connection. Raw PPMs and command traces stay under the project's ignored
`.cache/presentation/` directory.

For a second rendering configuration, pass `--native`, `--output` and
`--artifacts` paths to the generator. Debug and Release exports should have
identical PNG/GIF bytes. [manifest.json](manifest.json) records source hashes,
the native executable hash, Pillow version, output hashes and animation timing.
The source revision identifies the base checkout; the individual source hashes
also cover changes made before a presentation commit.

Hero and caption typography uses the bundled Montserrat assets. Their source
and notices remain in the project's [font records](../../main/fonts/README.md).

Presentation assets stay deliberately small. CI checks their hashes and source
freshness alongside local Markdown links and anchors; regenerate the assets
when a listed renderer input changes.

## Adding device photography

A landscape desk photo with the whole enclosure and readable screen would
complement the native hero. A short clip showing a vertical swipe and ×
dismissal would show the physical interaction and reflections. Use a short
demo notification, a steady camera and enough ambient light to retain both
the screen and enclosure detail.

Keep photographs/video separate from these generated files, and record the
board revision, firmware build and capture date in their captions. Publish
selected, compressed assets; keep original footage outside Git.
