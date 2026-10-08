#!/usr/bin/env python3
"""Render gallery media from the unchanged benchmark main.c and locked LVGL.

Board calls are replaced by native stubs. Time is virtual; the performance
overlay is disabled. No USB, live daemon or physical display is opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parents[1]
FIXTURE = PROJECT / "tools/native_presentation"
LVGL = PROJECT / "managed_components/lvgl__lvgl"
FRAMES = 160
SAMPLE_MS = 40


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-type", choices=["Debug", "Release"], default="Debug")
    parser.add_argument("--output", type=Path, default=PROJECT / "docs/media")
    args = parser.parse_args()
    if not (LVGL / "CMakeLists.txt").is_file():
        parser.error("run an IDF build of render-bench to fetch its locked LVGL first")
    block = (PROJECT / "dependencies.lock").read_text().split("  lvgl/lvgl:\n", 1)[1]
    component_hash = re.search(r"    component_hash: (\S+)", block).group(1)
    version = re.search(r"    version: (\S+)", block).group(1)
    if (LVGL / ".component_hash").read_text().strip() != component_hash:
        parser.error("installed LVGL component hash differs from the project lock")
    installed_version = (LVGL / "lv_version.h").read_text()
    for field, expected in zip(["MAJOR", "MINOR", "PATCH"], version.split(".")):
        if not re.search(rf"#define LVGL_VERSION_{field}\s+{expected}\b", installed_version):
            parser.error("installed LVGL version differs from the project lock")

    build = PROJECT / ".cache/presentation" / args.build_type.lower()
    cache = build / "CMakeCache.txt"
    if cache.exists():
        source_entry = re.search(r"(?m)^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$", cache.read_text())
        if not source_entry or Path(source_entry.group(1)).resolve() != FIXTURE.resolve():
            parser.error("native cache belongs to another source directory; choose a fresh checkout")
    subprocess.run(["cmake", "-S", str(FIXTURE), "-B", str(build),
                    f"-DCMAKE_BUILD_TYPE={args.build_type}",
                    f"-DLVGL_SOURCE_DIR={LVGL}"], check=True)
    subprocess.run(["cmake", "--build", str(build), "--target",
                    "render_bench_presentation", "--parallel", "4"], check=True)
    raw = build / "frames"
    raw.mkdir(parents=True, exist_ok=True)
    executable = build / "render_bench_presentation"
    run = subprocess.run([str(executable), str(raw)], check=True, capture_output=True)
    (build / "motion.csv").write_bytes(run.stdout)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    frames = []
    screen_hashes = set()
    for index in range(FRAMES):
        with Image.open(raw / f"frame-{index:03d}.ppm") as source:
            screen = source.convert("RGB")
        assert screen.size == (640, 172)
        screen_hashes.add(hashlib.sha256(screen.tobytes()).hexdigest())
        if index == 0:
            screen.save(output / "benchmark.png", optimize=True)
        frame = Image.new("RGB", (640, 226), "#161b19")
        frame.paste(screen, (0, 0))
        draw = ImageDraw.Draw(frame)
        draw.text((12, 180), "Rendering benchmark: continuous motion",
                  font=ImageFont.load_default(size=17), fill="#edf0e9")
        draw.text((12, 207), "Native LVGL / Virtual time / Not a panel FPS measurement",
                  font=ImageFont.load_default(size=10), fill="#a4b1a7")
        frames.append(frame)
    assert len(screen_hashes) > FRAMES // 2, "preview does not show enough distinct rendered frames"
    atlas = Image.new("RGB", (640, 226 * FRAMES))
    for index, frame in enumerate(frames):
        atlas.paste(frame, (0, 226 * index))
    palette = atlas.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    indexed[0].save(output / "benchmark-demo.gif", save_all=True,
                    append_images=indexed[1:], duration=SAMPLE_MS,
                    loop=0, optimize=True, disposal=1)
    sources = [PROJECT / "main/main.c", PROJECT / "main/CMakeLists.txt",
               PROJECT / "dependencies.lock",
               PROJECT / "sdkconfig.defaults", Path(__file__).resolve()]
    sources.extend(p for p in sorted(FIXTURE.rglob("*"))
                   if p.is_file() and (p.suffix in {".c", ".h"} or p.name == "CMakeLists.txt"))
    assets = ["benchmark.png", "benchmark-demo.gif"]
    manifest = {
        "description": "Unchanged benchmark main.c rendered with locked LVGL, native board stubs and virtual time; performance overlay disabled.",
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sources},
        "lvgl": {"version": version, "component_hash": component_hash,
                 "font_sha256": {name: digest(LVGL / "src/font" / name)
                                  for name in ["lv_font_montserrat_16.c", "lv_font_montserrat_28.c"]}},
        "native_executable_sha256": digest(executable),
        "pillow_version": pillow_version, "screen_size": [640, 172],
        "animation": {"frames_sampled": FRAMES, "distinct_screens": len(screen_hashes),
                      "duration_ms": FRAMES * SAMPLE_MS, "sample_ms": SAMPLE_MS,
                      "timing": "virtual timer samples; not measured panel performance"},
        "files": {name: {"sha256": digest(output / name), "bytes": (output / name).stat().st_size}
                  for name in assets},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Rendered {len(assets)} benchmark assets ({sum((output / name).stat().st_size for name in assets):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
