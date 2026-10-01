#!/usr/bin/env python3
"""Audit legacy font coverage and the generated 16 px/20 px/22 px composites."""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LVGL_FONTS = PROJECT / "managed_components/lvgl__lvgl/src/font"
FONT_GLYPH = re.compile(r"/\* U\+([0-9A-F]{4,6})")
BODY_STYLE_FILES = {
    "bold": "status_text_16_bold.c",
    "italic": "status_text_16_italic.c",
    "bold_italic": "status_text_16_bold_italic.c",
}
BODY_STYLE_RANGES = "0x20-0x7E,0xA0-0x17F,0x2000-0x206F,0x20A0-0x20CF,0x2100-0x214F"
BODY_STYLE_REQUIRED = (
    set(range(0x20, 0x7F))
    | (set(range(0xA0, 0x180)) - {0x00AD})
    | {
        ord(char)
        for char in "‘’“”–—…·•‰′″°±×÷€£¥₹₽©®™§¶†‡"
    }
)


def glyphs(path: Path) -> set[int]:
    return {int(code, 16) for code in FONT_GLYPH.findall(path.read_text())}


def selected_codepoints(ranges: str) -> set[int]:
    codepoints: set[int] = set()
    for interval in ranges.split(","):
        parts = interval.split("-")
        start = int(parts[0], 16)
        end = int(parts[-1], 16)
        codepoints.update(range(start, end + 1))
    return codepoints


def style_font_metrics(path: Path) -> tuple[int, int, list[tuple[int, tuple[int, ...]]]]:
    source = path.read_text()
    line_height = int(re.search(r"\.line_height = (\d+),", source).group(1))
    base_line = int(re.search(r"\.base_line = (\d+),", source).group(1))
    codepoints = [int(value, 16) for value in FONT_GLYPH.findall(source)]
    descriptor_block = re.search(
        r"static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc\[\] = \{(.*?)\n\};",
        source,
        re.S,
    )
    if descriptor_block is None:
        raise ValueError(f"{path.name} has no glyph descriptors")
    descriptors = [
        tuple(map(int, values))
        for values in re.findall(
            r"\{\.bitmap_index = (\d+), \.adv_w = (\d+), "
            r"\.box_w = (\d+), \.box_h = (\d+), "
            r"\.ofs_x = (-?\d+), \.ofs_y = (-?\d+)\}",
            descriptor_block.group(1),
        )
    ]
    if len(descriptors) != len(codepoints) + 1:
        raise ValueError(f"{path.name} glyph descriptors do not match its glyph comments")
    return line_height, base_line, list(zip(codepoints, descriptors[1:]))


def overhang_candidates(
    glyph_metrics: list[tuple[int, tuple[int, ...]]],
) -> tuple[tuple[int, int], tuple[int, int]]:
    left = max(
        ((max(0, -values[4]), codepoint) for codepoint, values in glyph_metrics),
        default=(0, 0),
    )
    right = max(
        (
            (
                max(0, values[4] + values[2] - ((values[1] + 8) >> 4)),
                codepoint,
            )
            for codepoint, values in glyph_metrics
        ),
        default=(0, 0),
    )
    return left, right


def legacy_repertoire(size: int) -> set[int]:
    return (
        glyphs(LVGL_FONTS / f"lv_font_montserrat_{size}.c")
        | glyphs(LVGL_FONTS / f"lv_font_source_han_sans_sc_{size}_cjk.c")
        | glyphs(PROJECT / f"main/fonts/status_symbol_{size}.c")
    )


REQUIRED = {
    "ASCII": set(range(0x20, 0x7F)),
    "Latin-1 and Latin Extended-A": set(range(0xA0, 0x180)),
    "common punctuation and symbols": {
        ord(char)
        for char in "‘’“”–—…·•‰′″°±×÷−≈≠≤≥∞€£¥₹₽©®™§¶†‡←↑→↓↔✓✔✗✘✕★☆♥❤⚠⚙☑☒☐"
    },
    "CJK smoke sample": {ord(char) for char in "東京が日本語你好世界"},
}
CLOCK_GLYPHS = {ord(char) for char in "0123456789:-"}


def report_required(label: str, available: set[int]) -> bool:
    failed = False
    for name, required in REQUIRED.items():
        missing = sorted(required - available)
        if missing:
            failed = True
            details = ", ".join(
                f"U+{code:04X} {unicodedata.name(chr(code), '?')}"
                for code in missing
            )
            print(f"  {label} {name}: missing {details}")
    if not failed:
        print(f"  {label}: all required samples covered")
    return failed


def main() -> int:
    failed = False
    legacy = {size: legacy_repertoire(size) for size in (14, 16)}
    old_union = legacy[14] | legacy[16]

    for size in (14, 16):
        print(f"{size} px legacy font chain: {len(legacy[size])} distinct glyphs")
        failed |= report_required(f"{size} px", legacy[size])
    print(f"14/16 px legacy repertoire union: {len(old_union)} distinct glyphs")

    for size in (16, 20, 22):
        path = PROJECT / f"main/fonts/status_text_{size}.c"
        available = glyphs(path)
        print(f"{size} px generated composite: {len(available)} distinct glyphs")

        missing = sorted(old_union - available)
        extra = sorted(available - old_union)
        if missing:
            failed = True
            details = ", ".join(
                f"U+{code:04X} {unicodedata.name(chr(code), '?')}"
                for code in missing
            )
            print(f"  old repertoire parity: missing {details}")
        if extra:
            failed = True
            details = ", ".join(
                f"U+{code:04X} {unicodedata.name(chr(code), '?')}"
                for code in extra
            )
            print(f"  old repertoire parity: unexpected new glyphs {details}")
        if not missing and not extra:
            print("  old repertoire parity: exact")

        failed |= report_required(f"{size} px", available)

    regular_source = (PROJECT / "main/fonts/status_text_16.c").read_text()
    regular_line_height = int(re.search(r"\.line_height = (\d+),", regular_source).group(1))
    regular_base_line = int(re.search(r"\.base_line = (\d+),", regular_source).group(1))
    allowed_style_glyphs = selected_codepoints(BODY_STYLE_RANGES)
    for style, filename in BODY_STYLE_FILES.items():
        path = PROJECT / "main/fonts" / filename
        source = path.read_text()
        available = glyphs(path)
        line_height, base_line, descriptors = style_font_metrics(path)
        missing = sorted(BODY_STYLE_REQUIRED - available)
        outside = sorted(available - allowed_style_glyphs)
        if missing:
            failed = True
            print(
                f"  {style} Latin/punctuation: missing "
                + ", ".join(f"U+{value:04X}" for value in missing)
            )
        if outside:
            failed = True
            print(
                f"  {style} selected repertoire: unexpected "
                + ", ".join(f"U+{value:04X}" for value in outside)
            )
        if line_height != regular_line_height or base_line != regular_base_line:
            failed = True
            print(
                f"  {style} metrics {line_height}/{base_line} differ from regular "
                f"{regular_line_height}/{regular_base_line}"
            )
        if ".fallback = &status_text_16," not in source:
            failed = True
            print(f"  {style} fallback: does not point to status_text_16")

        min_y = min((values[5] for _, values in descriptors), default=0)
        max_y = max((values[5] + values[3] for _, values in descriptors), default=0)
        if min_y < -base_line or max_y > line_height - base_line:
            failed = True
            print(f"  {style} vertical ink bounds [{min_y}, {max_y}] exceed its font metrics")

        left, right = overhang_candidates(descriptors)
        ascii_metrics = [
            (codepoint, values)
            for codepoint, values in descriptors
            if 0x20 <= codepoint < 0x7F
        ]
        ascii_left, ascii_right = overhang_candidates(ascii_metrics)
        if left[0] > 5 or right[0] > 5:
            failed = True
            print(f"  {style} horizontal ink overhang exceeds the 5 px inset budget")
        print(
            f"16 px {style}: {len(available)} styled glyphs, "
            f"line_height={line_height}, base_line={base_line}, "
            f"ink_y=[{min_y}, {max_y}], "
            f"ink_overhang=left {left[0]} px U+{left[1]:04X}/"
            f"right {right[0]} px U+{right[1]:04X}, "
            f"ASCII_overhang=left {ascii_left[0]} px U+{ascii_left[1]:04X}/"
            f"right {ascii_right[0]} px U+{ascii_right[1]:04X}"
        )
        valid_metrics = (
            line_height == regular_line_height
            and base_line == regular_base_line
            and min_y >= -base_line
            and max_y <= line_height - base_line
        )
        valid_fallback = ".fallback = &status_text_16," in source
        if not missing and not outside and valid_metrics and valid_fallback:
            print("  styled coverage, regular fallback, and vertical bounds: valid")

    clock = glyphs(PROJECT / "main/fonts/status_clock_80.c")
    print(f"80 px status clock: {len(clock)} distinct glyphs")
    if clock != CLOCK_GLYPHS:
        failed = True
        missing = ", ".join(f"U+{code:04X}" for code in sorted(CLOCK_GLYPHS - clock))
        extra = ", ".join(f"U+{code:04X}" for code in sorted(clock - CLOCK_GLYPHS))
        print(f"  clock glyph selection: missing [{missing}], extra [{extra}]")
    else:
        print("  clock glyph selection: exactly digits 0–9, colon, and hyphen")

    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
