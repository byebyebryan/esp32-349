"""Fail-closed checks for font source selection and converter output."""

from pathlib import Path
import sys

import pytest


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import generate_status_fonts as generator  # noqa: E402


def test_missing_pinned_font_asset_fails_before_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(generator, "PROJECT", tmp_path)
    monkeypatch.setattr(generator, "SOURCE_HASHES", {"missing.ttf": "0" * 64})

    with pytest.raises(SystemExit, match="required font source is missing"):
        generator.verify_sources()


def test_unassigned_required_codepoint_fails_closed():
    with pytest.raises(SystemExit, match="1 missing"):
        generator.validate_source_assignment_partition(
            profile={0x41, 0x42},
            preserved={0x41},
            assignments={"font": set()},
        )


def test_unexpected_source_assignment_fails_closed():
    with pytest.raises(SystemExit, match="1 unexpected"):
        generator.validate_source_assignment_partition(
            profile={0x41},
            preserved={0x41},
            assignments={"font": {0x43}},
        )


def test_source_selection_preserves_extras_outside_the_required_profile():
    # This unit contract needs no ESP-IDF-managed components. The firmware
    # lane's font audit separately checks the real pinned LVGL source union.
    additions = generator.validate_source_assignment_partition(
        profile={0x41, 0x42},
        preserved={0x41, 0x43},
        assignments={"font": {0x42}},
    )

    assert additions == {0x42}


def test_converter_missing_required_glyph_fails_closed():
    with pytest.raises(SystemExit, match="1 missing"):
        generator.validate_generated_profile("status_text_16", {0x41}, {0x41, 0x42})


def test_converter_extra_glyph_fails_closed():
    with pytest.raises(SystemExit, match="1 extra"):
        generator.validate_generated_profile("status_text_16", {0x41, 0x43}, {0x41, 0x42})


def test_large_font_bitmap_offset_fails_with_compact_descriptor_config():
    with pytest.raises(SystemExit, match="20-bit limit"):
        generator.validate_bitmap_offset(
            "status_text_22", generator.MAX_SMALL_BITMAP_OFFSET + 1
        )
