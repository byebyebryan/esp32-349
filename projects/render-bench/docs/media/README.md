# Render-bench presentation media

These previews compile the unchanged application's `main/main.c` with its
locked LVGL dependency. They show the production scene, fonts and bouncing-ball
timer with native replacements for board calls. Time is virtual and the
performance overlay is disabled; no board, USB port or live daemon is opened.

| Asset | Content |
| --- | --- |
| [benchmark-demo.gif](benchmark-demo.gif) | 6.4-second scene with horizontal and vertical reflections |
| [benchmark.png](benchmark.png) | An unframed 640 × 172 native screen render |

The GIF is sampled every 40 ms of virtual time. Playback does not establish
panel frame rate, CPU use, touch latency or transfer behavior. The recorded
on-device results are in the [rendering findings](../rendering.md).

## Regenerate

Run from the repository root after an IDF build of render-bench has fetched its
locked LVGL. Native authoring needs CMake, a host C/C++ compiler and uv. Use a
shell without EIM activation so host compilation uses the host assembler:

```sh
uv run --no-project --with pillow==12.0.0 \
  python projects/render-bench/tools/render_presentation.py
python3 tools/check_docs.py
```

Pillow is an optional authoring dependency in uv's temporary environment. The
runner asserts that the ball stays inside the screen and reflects on both
axes. Export also checks that rendered screen pixels change across the story.
Raw PPMs, motion CSV and native build caches remain under the project's ignored
`.cache/presentation/` directory; caches from another source directory are
rejected before rebuilding.

Use `--build-type Release --output <ignored-export-directory>` for a second
configuration. Debug and Release PNG/GIF bytes should match. CI regenerates
both configurations and compares them with the committed outputs.

[manifest.json](manifest.json) records tracked source/output hashes, the native
binary hash, Pillow version, timing and the locked LVGL component/font identity.
The revision is the base checkout; individual hashes cover authoring changes
made before a presentation commit.
The shared docs gate verifies tracked inputs and outputs; regeneration checks
the installed component's version and hash against `dependencies.lock`.
Fonts are the project's built-in LVGL Montserrat fonts; their original source
and notices remain in the locked LVGL dependency.
