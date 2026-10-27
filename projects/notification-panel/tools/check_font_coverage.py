#!/usr/bin/env python3
"""Audit English/Simplified Chinese notification fonts and legacy UI chains."""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

from generate_text_presentation import OUTPUT as TEXT_PRESENTATION_OUTPUT, generated_source
from generate_status_fonts import TEXT_METRICS as GENERATED_TEXT_METRICS, verify_sources
from status_font_repertoire import (
    BLOCK_ELEMENTS,
    BOX_DRAWING,
    BRAILLE_PATTERNS,
    CHINESE_PUNCTUATION,
    notification_profile,
    profile_groups,
    simplified_chinese,
    status_text_source_assignments,
)


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
}
NOTIFICATION_SAMPLES = {
    "English notification": {
        ord(char) for char in "Claude / Codex: fixed → tests passed ✓ — no regressions…"
    },
    "Simplified Chinese notification": {
        ord(char)
        for char in "测试完成，代码已修改。编译错误已修复！任务通知：等待确认，连接网络失败。"
    },
    "box-drawing notification": {ord(char) for char in "── │ ┌─┬─┐ └─┴─┘ ══ ╭─╮"},
}
CLOCK_GLYPHS = {ord(char) for char in "0123456789:-"}
TEXT_METRICS = GENERATED_TEXT_METRICS
MAX_SMALL_BITMAP_OFFSET = (1 << 20) - 1
# Styles retain U+2144 as a legacy extra; regular notification fonts lack it.
STYLED_LEGACY_EXTRAS = {0x2144}
NEW_GLYPH_Y_BOUNDS = {
    0x20BE: {16: (0, 12), 20: (0, 15), 22: (0, 16)},
    0x20BF: {16: (-2, 14), 20: (-3, 17), 22: (-3, 19)},
}


def maximum_bitmap_offset(path: Path) -> int:
    source = path.read_text()
    descriptor_block = re.search(
        r"static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc\[\] = \{(.*?)\n\};",
        source,
        re.S,
    )
    if descriptor_block is None:
        raise ValueError(f"{path.name} has no glyph descriptors")
    offsets = [
        int(value)
        for value in re.findall(r"\.bitmap_index = (\d+)", descriptor_block.group(1))
    ]
    if not offsets:
        raise ValueError(f"{path.name} has no bitmap offsets")
    return max(offsets)


def report_required(
    label: str, available: set[int], required_groups: dict[str, set[int]] = REQUIRED,
) -> bool:
    failed = False
    for name, required in required_groups.items():
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
    verify_sources()
    legacy = {size: legacy_repertoire(size) for size in (14, 16)}
    old_union = legacy[14] | legacy[16]
    cjk_reference = glyphs(LVGL_FONTS / "lv_font_source_han_sans_sc_16_cjk.c")
    old_cjk = cjk_reference - set(range(0x20, 0x80)) - set(range(0xE000, 0xF900))
    chinese = simplified_chinese()
    preserved_legacy = (
        (old_union - old_cjk) | chinese | CHINESE_PUNCTUATION | BOX_DRAWING
    )
    required_profile = notification_profile()
    expected = preserved_legacy | required_profile
    assignments = status_text_source_assignments()
    assigned_codepoints = [
        codepoint for source_codepoints in assignments.values()
        for codepoint in source_codepoints
    ]
    exact_assignments = set(assigned_codepoints) == required_profile - preserved_legacy
    if len(assigned_codepoints) != 355 or len(set(assigned_codepoints)) != 355 or not exact_assignments:
        failed = True
        print("Font source assignments must partition exactly the 355 new profile codepoints")
    if len(preserved_legacy) != 4972 or len(required_profile) != 4365 or len(expected) != 5327:
        failed = True
        print(
            "Profile cardinalities changed: expected 4972 preserved, 4365 required, "
            f"and 5327 combined; found {len(preserved_legacy)}, {len(required_profile)}, "
            f"and {len(expected)}"
        )
    notification_required = {
        **REQUIRED,
        **NOTIFICATION_SAMPLES,
        "Simplified Chinese level 1": chinese,
        "Chinese punctuation": CHINESE_PUNCTUATION,
        "box drawing": BOX_DRAWING,
        "block elements": BLOCK_ELEMENTS,
        "braille patterns": BRAILLE_PATTERNS,
    }
    notification_required.update(profile_groups())

    for size in (14, 16):
        print(f"{size} px legacy font chain: {len(legacy[size])} distinct glyphs")
        failed |= report_required(f"{size} px", legacy[size])
    print(
        f"Notification selection: {len(chinese)} TGH Han characters, "
        f"{len(required_profile)} independent profile codepoints, "
        f"{len(expected)} combined with {len(preserved_legacy)} preserved legacy glyphs"
    )

    for size in (16, 20, 22):
        path = PROJECT / f"main/fonts/status_text_{size}.c"
        available = glyphs(path)
        print(f"{size} px generated composite: {len(available)} distinct glyphs")

        missing = sorted(expected - available)
        extra = sorted(available - expected)
        if missing:
            failed = True
            details = ", ".join(
                f"U+{code:04X} {unicodedata.name(chr(code), '?')}"
                for code in missing
            )
            print(f"  notification repertoire: missing {details}")
        if extra:
            failed = True
            details = ", ".join(
                f"U+{code:04X} {unicodedata.name(chr(code), '?')}"
                for code in extra
            )
            print(f"  notification repertoire: unexpected glyphs {details}")
        if not missing and not extra:
            print("  required profile and preserved legacy repertoire: exact")

        metrics = (
            int(re.search(r"\.line_height = (\d+),", path.read_text()).group(1)),
            int(re.search(r"\.base_line = (\d+),", path.read_text()).group(1)),
        )
        if metrics != TEXT_METRICS[size]:
            failed = True
            print(
                f"  metrics {metrics[0]}/{metrics[1]} differ from locked "
                f"{TEXT_METRICS[size][0]}/{TEXT_METRICS[size][1]}"
            )
        bitmap_offset = maximum_bitmap_offset(path)
        if bitmap_offset > MAX_SMALL_BITMAP_OFFSET:
            failed = True
            print(
                f"  bitmap offset {bitmap_offset:,} exceeds the LV_FONT_FMT_TXT_LARGE=0 "
                f"limit {MAX_SMALL_BITMAP_OFFSET:,}"
            )
        print(
            f"  metrics={metrics[0]}/{metrics[1]}, "
            f"maximum bitmap offset={bitmap_offset:,}/{MAX_SMALL_BITMAP_OFFSET:,}"
        )

        _, _, glyph_metrics = style_font_metrics(path)
        min_y = min(values[5] for _, values in glyph_metrics)
        max_y = max(values[5] + values[3] for _, values in glyph_metrics)
        if min_y < -metrics[1] or max_y > metrics[0] - metrics[1]:
            failed = True
            print(f"  regular vertical ink bounds [{min_y}, {max_y}] exceed font metrics")
        by_codepoint = dict(glyph_metrics)
        for codepoint, expected_bounds in NEW_GLYPH_Y_BOUNDS.items():
            descriptor = by_codepoint.get(codepoint)
            if descriptor is None:
                failed = True
                print(f"  U+{codepoint:04X}: no generated glyph metrics")
                continue
            actual_bounds = (descriptor[5], descriptor[5] + descriptor[3])
            locked_bounds = expected_bounds[size]
            if actual_bounds != locked_bounds:
                failed = True
                print(
                    f"  U+{codepoint:04X} vertical bounds {actual_bounds} differ from "
                    f"pinned source metrics {locked_bounds}"
                )

        failed |= report_required(f"{size} px", available, notification_required)

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
        style_legacy_extras = available - expected
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
        if style_legacy_extras != STYLED_LEGACY_EXTRAS:
            failed = True
            expected_extras = ", ".join(
                f"U+{value:04X}" for value in sorted(STYLED_LEGACY_EXTRAS)
            )
            actual_extras = ", ".join(
                f"U+{value:04X}" for value in sorted(style_legacy_extras)
            )
            print(
                f"  {style} legacy-only glyphs: expected {expected_extras}; "
                f"found {actual_extras}"
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
        effective_coverage = available | glyphs(PROJECT / "main/fonts/status_text_16.c")
        effective_required = expected | STYLED_LEGACY_EXTRAS
        missing_effective = sorted(effective_required - effective_coverage)
        extra_effective = sorted(effective_coverage - effective_required)
        if missing_effective or extra_effective:
            failed = True
            print(
                f"  {style} effective coverage differs from the required profile: "
                f"{len(missing_effective)} missing, {len(extra_effective)} extra"
            )

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
            print(
                "  styled required-profile coverage, preserved U+2144, regular fallback, "
                "and vertical bounds: valid"
            )

    presentation_current = (
        TEXT_PRESENTATION_OUTPUT.exists()
        and TEXT_PRESENTATION_OUTPUT.read_text() == generated_source()
    )
    if not presentation_current:
        failed = True
        print("  generated Unicode text-presentation table is stale")
    else:
        print("  generated Unicode text-presentation table: current")

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
