#!/usr/bin/env python3
"""Generate status text and clock fonts from locked LVGL assets."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from status_font_repertoire import CHINESE_PUNCTUATION, simplified_chinese


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
    "main/fonts/assets/montserrat-cc8daf2/Montserrat-Bold.ttf":
        "bc6e854971cea46b463be6f9eef4d9cd52f51cfc1fc0dd90c9d3e6483dc0ec61",
    "main/fonts/assets/montserrat-cc8daf2/Montserrat-MediumItalic.ttf":
        "b544641c1fde7c5228a26ddade08870a9c1dc7426446828fc8aa8acab2521a45",
    "main/fonts/assets/montserrat-cc8daf2/Montserrat-BoldItalic.ttf":
        "b4c121b337aaa977d711b6f397c5b9672d720af622555f1e3f9d3b87f2983665",
    "main/fonts/assets/montserrat-cc8daf2/OFL.txt":
        "8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e",
}

MONTSERRAT_RANGES = "0x20-0x7E,0xA0-0x17F,0x2000-0x206F,0x20A0-0x20CF,0x2100-0x214F"
BODY_STYLE_SOURCES = {
    "bold": "Montserrat-Bold.ttf",
    "italic": "Montserrat-MediumItalic.ttf",
    "bold_italic": "Montserrat-BoldItalic.ttf",
}
BODY_STYLE_REQUIRED = (
    set(range(0x20, 0x7F))
    | (set(range(0xA0, 0x180)) - {0x00AD})
    | {
        ord(char)
        for char in "‘’“”–—…·•‰′″°±×÷€£¥₹₽©®™§¶†‡"
    }
)
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


def font_dimensions(source: str) -> tuple[int, int]:
    line_height = int(re.search(r"\.line_height = (\d+),", source).group(1))
    base_line = int(re.search(r"\.base_line = (\d+),", source).group(1))
    return line_height, base_line


def selected_codepoints(ranges: str) -> set[int]:
    codepoints: set[int] = set()
    for interval in ranges.split(","):
        parts = interval.split("-")
        start = int(parts[0], 16)
        end = int(parts[-1], 16)
        codepoints.update(range(start, end + 1))
    return codepoints


def body_style_extents(source: str) -> tuple[int, int, int, int]:
    codepoints = [int(value, 16) for value in FONT_GLYPH.findall(source)]
    match = re.search(
        r"static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc\[\] = \{(.*?)\n\};",
        source,
        re.S,
    )
    if match is None:
        raise ValueError("generated body style font has no glyph descriptors")
    descriptors = [
        tuple(map(int, values))
        for values in re.findall(
            r"\{\.bitmap_index = (\d+), \.adv_w = (\d+), "
            r"\.box_w = (\d+), \.box_h = (\d+), "
            r"\.ofs_x = (-?\d+), \.ofs_y = (-?\d+)\}",
            match.group(1),
        )
    ]
    if len(descriptors) != len(codepoints) + 1:
        raise ValueError("body style glyph descriptors do not match its comments")

    glyph_metrics = list(zip(codepoints, descriptors[1:]))
    min_y = min(descriptor[5] for _, descriptor in glyph_metrics)
    max_y = max(descriptor[5] + descriptor[3] for _, descriptor in glyph_metrics)
    left_overhang = max(
        (max(0, -descriptor[4]) for _, descriptor in glyph_metrics), default=0
    )
    right_overhang = max(
        (
            max(
                0,
                descriptor[4] + descriptor[2] - ((descriptor[1] + 8) >> 4),
            )
            for _, descriptor in glyph_metrics
        ),
        default=0,
    )
    return min_y, max_y, left_overhang, right_overhang


def generate_body_style_fonts() -> None:
    regular_output = PROJECT / "main/fonts/status_text_16.c"
    if not regular_output.exists():
        raise SystemExit("status_text_16.c is required as the styled-font fallback")
    regular_line_height, regular_base_line = font_dimensions(regular_output.read_text())
    if (regular_line_height, regular_base_line) != (22, 5):
        raise SystemExit(
            "body style generation expects status_text_16 metrics line_height=22, base_line=5; "
            f"found {regular_line_height}, {regular_base_line}"
        )

    allowed = selected_codepoints(MONTSERRAT_RANGES)
    for style, filename in BODY_STYLE_SOURCES.items():
        name = f"status_text_16_{style}"
        output = PROJECT / f"main/fonts/{name}.c"
        command = [
            "npx", "--yes", "lv_font_conv@1.5.3",
            "--bpp", "4", "--size", "16", "--no-compress", "--no-kerning",
            "--font", f"main/fonts/assets/montserrat-cc8daf2/{filename}",
            "-r", MONTSERRAT_RANGES,
            "--format", "lvgl", "-o", f"main/fonts/{name}.c",
            "--lv-font-name", name, "--lv-include", "lvgl.h",
            "--lv-fallback", "status_text_16",
        ]
        subprocess.run(command, cwd=PROJECT, check=True)

        source = output.read_text().rstrip() + "\n"
        generated_line_height, generated_base_line = font_dimensions(source)
        if generated_base_line != regular_base_line:
            raise SystemExit(
                f"{name} baseline {generated_base_line} differs from regular font "
                f"baseline {regular_base_line}"
            )
        if generated_line_height > regular_line_height:
            raise SystemExit(
                f"{name} line_height {generated_line_height} exceeds regular font "
                f"line_height {regular_line_height}"
            )
        source, changed = re.subn(
            r"(?m)(^\s*\.line_height = )\d+",
            rf"\g<1>{regular_line_height}",
            source,
            count=1,
        )
        if changed != 1:
            raise SystemExit(f"{name} line_height was not updated to match status_text_16")
        output.write_text(source)

        actual_glyphs = glyphs(output)
        missing = BODY_STYLE_REQUIRED - actual_glyphs
        outside_selection = actual_glyphs - allowed
        if missing:
            details = ", ".join(f"U+{value:04X}" for value in sorted(missing))
            raise SystemExit(f"{name} is missing required Latin/punctuation glyphs: {details}")
        if outside_selection:
            details = ", ".join(f"U+{value:04X}" for value in sorted(outside_selection))
            raise SystemExit(
                f"{name} has glyphs outside the pinned Latin/punctuation ranges: {details}"
            )
        if "--no-compress" not in source or "--no-kerning" not in source:
            raise SystemExit(
                f"{name} does not record the expected uncompressed/no-kerning settings"
            )
        if ".fallback = &status_text_16," not in source:
            raise SystemExit(f"{name} does not fall back to status_text_16")

        min_y, max_y, left_overhang, right_overhang = body_style_extents(source)
        if min_y < -regular_base_line or max_y > regular_line_height - regular_base_line:
            raise SystemExit(
                f"{name} glyph extents [{min_y}, {max_y}] exceed the 22 px/5 px baseline"
            )
        if left_overhang > 5 or right_overhang > 5:
            raise SystemExit(
                f"{name} horizontal italic overhang exceeds the 5 px body inset budget: "
                f"left={left_overhang}, right={right_overhang}"
            )

        print(
            f"{name}: {len(actual_glyphs)} styled glyphs, line_height={regular_line_height}, "
            f"base_line={regular_base_line}, extents_y=[{min_y}, {max_y}], "
            f"x_overhang=left {left_overhang}/right {right_overhang} px, "
            f"bitmap={bitmap_bytes(source):,} B, source={output.stat().st_size:,} B"
        )


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
    parser.add_argument(
        "--body-styles",
        action="store_true",
        help="generate only the pinned 16 px bold, italic, and bold italic body fonts",
    )
    args = parser.parse_args()

    if args.body_styles and args.text_sizes is not None:
        parser.error("--body-styles cannot be combined with --text-sizes")

    verify_sources()

    if args.body_styles:
        generate_body_style_fonts()
        return 0

    cjk_reference = glyphs(PROJECT / SOURCE_CJK)
    old_cjk = cjk_reference - ASCII - set(PRIVATE_USE)
    han_codepoints = simplified_chinese() | CHINESE_PUNCTUATION
    fontawesome_codepoints = cjk_reference.intersection(PRIVATE_USE)
    if not old_cjk or not fontawesome_codepoints:
        raise SystemExit("Source Han CJK reference no longer has the expected repertoire")

    expected_repertoire = (legacy_repertoire() - old_cjk) | han_codepoints
    print(
        f"Source selection: 3,500 Simplified Chinese characters, "
        f"{len(CHINESE_PUNCTUATION)} Chinese punctuation glyphs, "
        f"{len(fontawesome_codepoints)} existing FontAwesome glyphs; "
        f"composite repertoire {len(expected_repertoire)} glyphs"
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
        if actual_glyphs != expected_repertoire:
            missing = expected_repertoire - actual_glyphs
            extra = actual_glyphs - expected_repertoire
            raise SystemExit(
                f"{name} differs from the English/Simplified Chinese selection: "
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
