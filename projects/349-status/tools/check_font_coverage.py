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


def glyphs(path: Path) -> set[int]:
    return {int(code, 16) for code in FONT_GLYPH.findall(path.read_text())}


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
