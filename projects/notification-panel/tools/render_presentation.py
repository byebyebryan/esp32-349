#!/usr/bin/env python3
"""Render README media through the production protocol/state/LVGL fixture.

Uses synthetic messages, virtual time and pointer input. No USB, desktop bus,
daemon IPC or live notifications are opened. Pillow is an optional authoring
dependency; see docs/media/README.md at the repository root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parents[1]
sys.path.insert(0, str(PROJECT / "host/src"))

from status349 import proto
from status349.notification_text import convert_body
from check_native_ui import check_cache_source
from test_grouped_composed import Native

SESSION = 349
EPOCH = 1791479400
FONT = PROJECT / "main/fonts/assets/montserrat-cc8daf2/Montserrat-Bold.ttf"
BACKGROUND = "#161b19"
FOREGROUND = "#edf0e9"
MUTED = "#a4b1a7"
ACCENT = "#bdceab"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def make_hero(screen: Image.Image) -> Image.Image:
    hero = Image.new("RGB", (1408, 680), BACKGROUND)
    draw = ImageDraw.Draw(hero)
    draw.text((64, 42), "ESP32-349", font=font(22), fill=ACCENT)
    draw.text((60, 88), "Your desktop, at a glance.", font=font(54), fill=FOREGROUND)
    draw.text((64, 168), "Notifications, host telemetry, and touch controls.",
              font=font(25), fill=MUTED)
    # This is a graphic frame around an exact UI render, not a device mockup.
    draw.rounded_rectangle((48, 236, 1360, 612), radius=18,
                           fill="#0d100f", outline="#39433d", width=2)
    hero.paste(screen.resize((1280, 344), Image.Resampling.NEAREST), (64, 252))
    draw.text((64, 637), "Waveshare ESP32-S3-Touch-LCD-3.49 V2", font=font(17), fill=MUTED)
    note = "640 x 172 / Native LVGL render / Demo data"
    draw.text((1344 - draw.textlength(note, font=font(15)), 639),
              note, font=font(15), fill=MUTED)
    return hero


def render(native: Native, output: Path) -> tuple[list[Image.Image], list[int]]:
    frames: list[Image.Image] = []
    durations: list[int] = []
    cards: set[int] = set()
    dashboard = {
        "cpu": .18, "cpu_freq_mhz": 3600, "mem": .43,
        "mem_used_bytes": 9019431322, "network": True,
        "rx_bytes_per_s": 102400, "tx_bytes_per_s": 24576,
    }
    snapshot = {
        "rev": 1, "bar": proto.bar([], 1), "clock": proto.clock(EPOCH, -7 * 3600),
        "media": None, "limit": 32, "overflow": 0, "notifs": [],
        "dashboard": dashboard,
    }
    native.wire(proto.hello())
    hello = next(m for m in native.outbound if m.get("t") == "hello")
    assert proto.notification_body_style_capable(hello)
    for message in proto.card_sync_messages(
        snapshot, 1, include_dashboard=True, grouped_session=SESSION,
        include_actions=True, include_history=True, include_body_styles=True,
    ):
        native.wire(message)
    native.command("advance", ms=1000)
    assert native.status()["grouped"]["history"]

    def screen(name: str) -> Image.Image:
        native.command("capture", name=name)
        with Image.open(native.artifacts / f"{name}.ppm") as image:
            assert image.size == (640, 172)
            return image.convert("RGB")

    def still(name: str) -> Image.Image:
        image = screen(name)
        image.save(output / f"{name}.png", optimize=True)
        return image

    def record(caption: str, duration: int) -> None:
        image = Image.new("RGB", (640, 226), BACKGROUND)
        image.paste(screen(f"frame-{len(frames):03d}"), (0, 0))
        draw = ImageDraw.Draw(image)
        draw.text((12, 180), caption, font=font(17), fill=FOREGROUND)
        draw.text((12, 207), "Native LVGL / Demo data / Virtual pointer input",
                  font=font(10), fill=MUTED)
        frames.append(image)
        durations.append(duration)

    def advance(ms: int) -> None:
        native.wire({"t": "ping"})
        native.command("advance", ms=ms)

    def hold(caption: str, ms: int = 1800) -> None:
        advance(ms)
        record(caption, ms)

    def settle(caption: str) -> None:
        for _ in range(8):
            advance(30)
            record(caption, 30)

    def notify(nid: int, app: str, title: str, body: str) -> None:
        text, runs = convert_body(app, title, body, {})
        message = proto.notify(nid, app, title, text, 1, 10000, EPOCH,
                               session=SESSION, body_runs=runs)
        message["history"] = {"rev": 1, "age_ms": 0, "remaining_ms": 600000}
        message["open"] = {"rev": 1, "state": "unavailable"}
        cards.add(nid)
        native.wire(message)
        native.wire(proto.present(SESSION, nid, nid, 10000, 1))

    def dismiss() -> None:
        native.outbound.clear()
        native.command("press", x=600, y=110, ms=20)
        record("Tap x to dismiss the selected card", 100)
        native.command("release", x=600, y=110, ms=20)
        inputs = [m for m in native.outbound
                  if m.get("t") == "input" and m.get("action") == "dismiss"]
        assert len(inputs) == 1, inputs
        nid = inputs[0]["id"]
        cards.remove(nid)
        native.wire(proto.close(nid, total=len(cards), session=SESSION))
        settle("Tap x to dismiss the selected card")

    still("empty")
    hold("The clock and telemetry stay visible", 1500)
    notify(1, "Build", "Ready for review",
           "<b>Build complete.</b>\nReview the changes when you're ready.\n<i>No rush.</i>")
    settle("A notification arrives")
    native.wire(proto.card_action(SESSION, 1, 1, "ready"))
    advance(200)
    make_hero(still("notification")).save(output / "hero.png", optimize=True)
    hold("Readable text, with bold and italic support")

    notify(2, "Notes", "任务完成", "代码检查已完成。\n现在可以查看更改。")
    settle("English and Simplified Chinese text")
    still("multilingual")
    hold("English and Simplified Chinese text")
    native.command("press", x=350, y=110, ms=20)
    record("Swipe vertically to browse recent cards", 20)
    for y in [90, 70, 50, 20]:
        native.command("move", x=350, y=y, ms=60)
        record("Swipe vertically to browse recent cards", 60)
    native.command("release", x=350, y=20, ms=20)
    settle("Swipe vertically to browse recent cards")
    assert native.status()["deck"]["focus_id"] == 1
    hold("The earlier notification is still here")
    dismiss()
    assert native.status()["ids"] == [2]
    hold("Other cards stay in the deck", 1400)
    dismiss()
    assert native.status()["count"] == 0
    hold("Back to an empty notification pane", 1600)

    # A separate stale-reading view; do not splice it into the gesture demo.
    notify(3, "Build", "Ready for review",
           "<b>Build complete.</b>\nReview the changes when you're ready.")
    advance(250)
    native.command("advance", ms=12000)
    assert native.status()["deck"]["stale"]
    still("stale")
    return frames, durations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path,
                        default=PROJECT / ".cache/native-ui/debug/native_ui_composed")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/media")
    parser.add_argument("--artifacts", type=Path, default=PROJECT / ".cache/presentation")
    args = parser.parse_args()
    executable = args.native.resolve()
    if not executable.is_file():
        parser.error("build native_ui_composed first; see tools/native_ui/README.md")
    try:
        check_cache_source(executable.parent, PROJECT / "tools/native_ui")
    except ValueError as exc:
        parser.error(str(exc))
    subprocess.run(["cmake", "--build", str(executable.parent),
                    "--target", "native_ui_composed"], check=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    native = Native(executable, args.artifacts.resolve())
    try:
        frames, durations = render(native, output)
    finally:
        native.close()

    atlas = Image.new("RGB", (640, 226 * len(frames)))
    for index, frame in enumerate(frames):
        atlas.paste(frame, (0, 226 * index))
    palette = atlas.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    indexed[0].save(output / "notification-demo.gif", save_all=True,
                    append_images=indexed[1:], duration=durations, loop=0,
                    optimize=True, disposal=1)

    sources = [
        "main/ui_deck.c", "main/ui_theme.h", "main/ui_fonts.c", "main/proto.c",
        "main/state.c", "main/dashboard.c", "main/deck.c", "main/deck_input.c",
        "main/group_input.c", "tools/native_ui/test_ui.c", "tools/native_ui/test_composed.c",
        "tools/test_grouped_composed.py", "tools/check_native_ui.py", "tools/render_presentation.py",
        "tools/native_ui/CMakeLists.txt", "tools/native_ui/lv_conf.h", "dependencies.lock",
        "host/src/status349/proto.py", "host/src/status349/notification_text.py",
    ]
    sources.extend(str(Path(p).with_suffix(".h")) for p in list(sources)
                   if p.startswith("main/") and (PROJECT / Path(p).with_suffix(".h")).is_file())
    sources.extend(str(p.relative_to(PROJECT)) for p in sorted((PROJECT / "main/fonts").glob("*.c")))
    for directory in ["tools/native_protocol/stubs", "tools/native_ui/stubs"]:
        sources.extend(str(p.relative_to(PROJECT)) for p in sorted((PROJECT / directory).rglob("*"))
                       if p.suffix in {".c", ".h"})
    sources.append(str(FONT.relative_to(PROJECT)))
    assets = ["hero.png", "notification-demo.gif", "notification.png",
              "multilingual.png", "empty.png", "stale.png"]
    manifest = {
        "description": "Production protocol/state/LVGL with synthetic messages, virtual time and pointer input; no hardware or desktop session.",
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {str((PROJECT / p).relative_to(ROOT)): digest(PROJECT / p) for p in sources},
        "native_executable_sha256": digest(executable),
        "pillow_version": pillow_version,
        "screen_size": [640, 172], "hero_screen_scale": 2,
        "animation": {"frames_sampled": len(frames), "duration_ms": sum(durations),
                      "transition_sample_ms": 30, "timing": "virtual pointer/time samples with caption holds; not measured panel performance"},
        "files": {name: {"sha256": digest(output / name), "bytes": (output / name).stat().st_size}
                  for name in assets},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Rendered {len(assets)} assets to {output} ({sum((output / n).stat().st_size for n in assets):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
