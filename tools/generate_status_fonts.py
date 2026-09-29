#!/usr/bin/env python3
"""Generate status text and clock fonts from locked LVGL assets."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LVGL = PROJECT / "managed_components/lvgl__lvgl"
LVGL_FONT_SOURCES = LVGL / "src/font"
FONT_GLYPH = re.compile(r"/\* U\+([0-9A-F]{4,6})")

SOURCE_CJK = "managed_components/lvgl__lvgl/src/font/lv_font_source_han_sans_sc_16_cjk.c"
SOURCE_HASHES = {
    "managed_components/lvgl__lvgl/scripts/built_in_font/Montserrat-Medium.ttf":
        "421f26b23e2be6b98373d32acd3cb2897b154d4bf0a77d26534ce476e4cbed53",
    "managed_components/lvgl__lvgl/scripts/built_in_font/DejaVuSans.ttf":
        "3fdf69cabf06049ea70a00b5919340e2ce1e6d02b0cc3c4b44fb6801bd1e0d22",
    "managed_components/lvgl__lvgl/scripts/built_in_font/SourceHanSansSC-Normal.otf":
        "1ee89e1669362dee13851129c0a8a791a87521eb4148e5efbf5d26596738e25b",
    "managed_components/lvgl__lvgl/scripts/built_in_font/FontAwesome5-Solid+Brands+Regular.woff":
        "f4e42f6cd69e5dbdcccc0f2f5be136cebde0e427641e45403bf9173a92da95f4",
    SOURCE_CJK:
        "4caaa2aff00bb59d1155227c838ced106ab4d61717afcaa66328cb0b3136785b",
}

MONTSERRAT_RANGES = "0x20-0x7E,0xA0-0x17F,0x2000-0x206F,0x20A0-0x20CF,0x2100-0x214F"
DEJAVU_RANGES = "0x2190-0x23FF,0x25A0-0x27BF"
CLOCK_RANGES = "0x2D,0x30-0x39,0x3A"
ASCII = set(range(0x20, 0x80))
PRIVATE_USE = range(0xE000, 0xF900)
MAX_LINE_HEIGHT = {16: 22, 20: 27, 22: 30}
CLOCK_GLYPHS = {ord(char) for char in "0123456789:-"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def glyphs(path: Path) -> set[int]:
    return {int(value, 16) for value in FONT_GLYPH.findall(path.read_text())}


def ranges_for(codepoints: set[int]) -> str:
    if not codepoints:
        raise ValueError("cannot create an empty font range")

    intervals: list[list[int]] = []
    for codepoint in sorted(codepoints):
        if intervals and codepoint == intervals[-1][1] + 1:
            intervals[-1][1] = codepoint
        else:
            intervals.append([codepoint, codepoint])

    return ",".join(
        f"0x{start:X}" if start == end else f"0x{start:X}-0x{end:X}"
        for start, end in intervals
    )


def verify_sources() -> None:
    for relative_path, expected in SOURCE_HASHES.items():
        actual = sha256(PROJECT / relative_path)
        if actual != expected:
            raise SystemExit(
                f"source changed: {relative_path}\n"
                f"  expected SHA-256 {expected}\n"
                f"  found    SHA-256 {actual}"
            )


def legacy_repertoire() -> set[int]:
    paths = [
        LVGL_FONT_SOURCES / f"lv_font_montserrat_{size}.c"
        for size in (14, 16)
    ]
    paths += [
        LVGL_FONT_SOURCES / f"lv_font_source_han_sans_sc_{size}_cjk.c"
        for size in (14, 16)
    ]
    paths += [
        PROJECT / f"main/fonts/status_symbol_{size}.c"
        for size in (14, 16)
    ]
    return set().union(*(glyphs(path) for path in paths))


def bitmap_bytes(source: str) -> int:
    match = re.search(r"glyph_bitmap\[\]\s*=\s*\{(.*?)\n\};", source, re.S)
    if match is None:
        raise ValueError("generated font has no glyph_bitmap array")
    return len(re.findall(r"\b0x[0-9A-Fa-f]+\b", match.group(1)))


def maximum_clock_width(source: str) -> tuple[int, str]:
    codepoints = [int(value, 16) for value in FONT_GLYPH.findall(source)]
    match = re.search(
        r"static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc\[\] = \{(.*?)\n\};",
        source,
        re.S,
    )
    if match is None:
        raise ValueError("generated clock font has no glyph descriptor array")

    advances = [int(value) for value in re.findall(r"\.adv_w = (\d+)", match.group(1))]
    if len(advances) != len(codepoints) + 1 or set(codepoints) != CLOCK_GLYPHS:
        raise ValueError("generated clock glyph descriptors do not match its selection")

    # LVGL rounds each 1/16 px glyph advance to the nearest whole pixel.
    width_by_codepoint = {
        codepoint: (advance + 8) >> 4
        for codepoint, advance in zip(codepoints, advances[1:])
    }
    choices = (
        (
            sum(width_by_codepoint[ord(char)] for char in f"{hour:02d}:{minute:02d}"),
            f"{hour:02d}:{minute:02d}",
        )
        for hour in range(24)
        for minute in range(60)
    )
    return max(choices)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text-sizes",
        type=int,
        nargs="+",
        choices=(16, 20, 22),
        help=(
            "generate only these composite text fonts; without this option, "
            "generate all composite text fonts and the status clock"
        ),
    )
    args = parser.parse_args()

    verify_sources()

    cjk_reference = glyphs(PROJECT / SOURCE_CJK)
    han_codepoints = cjk_reference - ASCII - set(PRIVATE_USE)
    fontawesome_codepoints = cjk_reference.intersection(PRIVATE_USE)
    if not han_codepoints or not fontawesome_codepoints:
        raise SystemExit("Source Han CJK reference no longer has the expected repertoire")

    smoke_sample = {ord(char) for char in "東京が日本語你好世界"}
    if not smoke_sample <= han_codepoints:
        missing = ", ".join(f"U+{value:04X}" for value in sorted(smoke_sample - han_codepoints))
        raise SystemExit(f"Source Han reference is missing smoke glyphs: {missing}")

    old_repertoire = legacy_repertoire()
    print(
        f"Source selection: {len(han_codepoints)} Source Han CJK glyphs, "
        f"{len(fontawesome_codepoints)} existing FontAwesome glyphs; "
        f"legacy union {len(old_repertoire)} glyphs"
    )

    text_sizes = args.text_sizes if args.text_sizes is not None else (16, 20, 22)
    for size in text_sizes:
        name = f"status_text_{size}"
        output = PROJECT / f"main/fonts/{name}.c"
        command = [
            "npx", "--yes", "lv_font_conv@1.5.3",
            "--bpp", "4", "--size", str(size), "--no-compress", "--no-kerning",
            "--font", "managed_components/lvgl__lvgl/scripts/built_in_font/Montserrat-Medium.ttf",
            "-r", MONTSERRAT_RANGES,
            "--font", "managed_components/lvgl__lvgl/scripts/built_in_font/DejaVuSans.ttf",
            "-r", DEJAVU_RANGES,
            "--font", "managed_components/lvgl__lvgl/scripts/built_in_font/SourceHanSansSC-Normal.otf",
            "-r", ranges_for(han_codepoints),
            "--font", "managed_components/lvgl__lvgl/scripts/built_in_font/FontAwesome5-Solid+Brands+Regular.woff",
            "-r", ranges_for(fontawesome_codepoints),
            "--format", "lvgl", "-o", f"main/fonts/{name}.c",
            "--lv-font-name", name, "--lv-include", "lvgl.h",
        ]
        subprocess.run(command, cwd=PROJECT, check=True)

        source = output.read_text().rstrip() + "\n"
        output.write_text(source)
        actual_glyphs = glyphs(output)
        if actual_glyphs != old_repertoire:
            missing = old_repertoire - actual_glyphs
            extra = actual_glyphs - old_repertoire
            raise SystemExit(
                f"{name} differs from the legacy 14/16 union: "
                f"{len(missing)} missing, {len(extra)} extra"
            )

        if (
            "Bpp: 4" not in source
            or not re.search(r"\.bpp\s*=\s*4\b", source)
            or "--no-compress" not in source
            or "--no-kerning" not in source
        ):
            raise SystemExit(f"{name} does not record the expected 4bpp, uncompressed settings")

        line_height = int(re.search(r"line_height = (\d+),", source).group(1))
        base_line = int(re.search(r"base_line = (\d+),", source).group(1))
        if line_height > MAX_LINE_HEIGHT[size]:
            raise SystemExit(
                f"{name} line_height {line_height} exceeds card budget "
                f"{MAX_LINE_HEIGHT[size]}"
            )

        print(
            f"{name}: {len(actual_glyphs)} glyphs, line_height={line_height}, "
            f"base_line={base_line}, bitmap={bitmap_bytes(source):,} B, "
            f"source={output.stat().st_size:,} B"
        )

    if args.text_sizes is not None:
        return 0

    clock_name = "status_clock_80"
    clock_output = PROJECT / f"main/fonts/{clock_name}.c"
    clock_command = [
        "npx", "--yes", "lv_font_conv@1.5.3",
        "--bpp", "4", "--size", "80", "--no-compress", "--no-kerning",
        "--font", "managed_components/lvgl__lvgl/scripts/built_in_font/Montserrat-Medium.ttf",
        "-r", CLOCK_RANGES,
        "--format", "lvgl", "-o", f"main/fonts/{clock_name}.c",
        "--lv-font-name", clock_name, "--lv-include", "lvgl.h",
    ]
    subprocess.run(clock_command, cwd=PROJECT, check=True)
    clock_source = clock_output.read_text().rstrip() + "\n"
    clock_output.write_text(clock_source)
    if glyphs(clock_output) != CLOCK_GLYPHS:
        raise SystemExit(f"{clock_name} does not contain exactly digits, colon, and hyphen")
    if (
        "Bpp: 4" not in clock_source
        or not re.search(r"\.bpp\s*=\s*4\b", clock_source)
        or "--no-compress" not in clock_source
        or "--no-kerning" not in clock_source
    ):
        raise SystemExit(f"{clock_name} does not record the expected 4bpp, uncompressed settings")

    clock_line_height = int(re.search(r"line_height = (\d+),", clock_source).group(1))
    clock_base_line = int(re.search(r"base_line = (\d+),", clock_source).group(1))
    clock_width, widest_time = maximum_clock_width(clock_source)
    print(
        f"{clock_name}: {len(CLOCK_GLYPHS)} glyphs, line_height={clock_line_height}, "
        f"base_line={clock_base_line}, max valid HH:MM={clock_width} px ({widest_time}), "
        f"bitmap={bitmap_bytes(clock_source):,} B, source={clock_output.stat().st_size:,} B"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
