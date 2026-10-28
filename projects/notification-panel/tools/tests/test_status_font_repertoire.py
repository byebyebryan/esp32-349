"""Integrity and inheritance tests for the pinned notification font profile."""

import importlib.util
import hashlib
import io
from pathlib import Path
import zipfile

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "status_font_repertoire.py"
spec = importlib.util.spec_from_file_location("status_font_repertoire", MODULE_PATH)
repertoire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repertoire)


def test_profile_groups_have_the_locked_cardinalities():
    groups = repertoire.profile_groups()

    assert {name: len(values) for name, values in groups.items()} == {
        "GF Latin Core": 319,
        "GF Latin Plus": 110,
        "CLDR 49 en punctuation": 32,
        "CLDR 49 en numbers": 18,
        "CLDR 49 zh-Hans punctuation": 55,
        "CLDR 49 zh-Hans numbers": 28,
        "TGH 2013 level 1": 3500,
        "TGH 2013 level 2": 3000,
        "Box Drawing": 128,
        "Block Elements": 32,
        "Braille Patterns": 256,
    }
    assert len(repertoire.notification_profile()) == 7365


def test_level_2_membership_contains_the_requested_common_characters():
    level_1 = repertoire.profile_groups()["TGH 2013 level 1"]
    level_2 = repertoire.profile_groups()["TGH 2013 level 2"]

    assert len(level_1 | level_2) == 6500
    assert not (level_1 & level_2)
    assert {0x55EF, 0x5E27, 0x9608} <= level_2


def test_changed_pinned_repertoire_list_fails_closed(monkeypatch, tmp_path):
    (tmp_path / "GF_Latin_Core.nam").write_text("0x0020 # SPACE\n")
    monkeypatch.setattr(repertoire, "REPERTOIRES", tmp_path)

    with pytest.raises(ValueError, match="pinned GF_Latin_Core.nam changed"):
        repertoire.profile_groups()


def test_changed_source_assignment_manifest_fails_closed(monkeypatch, tmp_path):
    (tmp_path / "status-text-source-assignments.json").write_text("{}\n")
    monkeypatch.setattr(repertoire, "SOURCE_ASSIGNMENTS", tmp_path / "status-text-source-assignments.json")

    with pytest.raises(ValueError, match="pinned status text source assignments changed"):
        repertoire.status_text_source_assignments()


def test_source_assignments_are_disjoint_and_cover_the_declared_additions():
    assignments = repertoire.status_text_source_assignments()
    assigned = [codepoint for values in assignments.values() for codepoint in values]

    assert len(assigned) == 3355
    assert len(set(assigned)) == 3355
    assert sum(map(len, assignments.values())) == 3355


def test_unihan_extraction_sorts_table_indexes_and_preserves_level_1(tmp_path, monkeypatch):
    baseline = repertoire._read_chinese_table(
        repertoire.CHINESE_TABLE,
        repertoire.CHINESE_TABLE_SHA256,
        repertoire.CHINESE_LEVEL_1_COUNT,
        "TGH 2013 level 1 repertoire",
    )
    points = list(baseline)
    used = set(points)
    candidate = 0x9FFF
    while len(points) < repertoire.CHINESE_PROFILE_COUNT:
        if candidate not in used:
            points.append(candidate)
            used.add(candidate)
        candidate -= 1

    text = "\n".join(
        ["# Unihan test source", "#\tkTGH"]
        + [
            f"U+{codepoint:04X}\tkTGH\t2013:{index}"
            for index, codepoint in reversed(list(enumerate(points, 1)))
        ]
        + ["U+55EF\tkTGHZ2013\tunused dictionary reading"]
    ) + "\n"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(repertoire.UNIHAN_TGH_MEMBER, text)
    archive_path = tmp_path / "Unihan.zip"
    archive_path.write_bytes(archive_bytes.getvalue())
    output_bytes = repertoire._format_tgh_table(points)
    monkeypatch.setattr(
        repertoire, "UNIHAN_ARCHIVE_SHA256", hashlib.sha256(archive_path.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(
        repertoire, "CHINESE_TABLE_LEVEL_1_2_SHA256", hashlib.sha256(output_bytes).hexdigest()
    )

    result = repertoire.extract_unihan_tgh_levels_1_2(archive_path, tmp_path / "out")

    assert result == points
    assert (tmp_path / "out" / repertoire.CHINESE_TABLE_LEVEL_1_2.name).read_bytes() == output_bytes


def test_unihan_extraction_rejects_an_unpinned_archive_before_output(tmp_path):
    archive = tmp_path / "not-unihan.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr(repertoire.UNIHAN_TGH_MEMBER, "")

    output = tmp_path / "out"
    with pytest.raises(ValueError, match="pinned Unicode 17.0.0 Unihan archive changed"):
        repertoire.extract_unihan_tgh_levels_1_2(archive, output)
    assert not output.exists()


def test_zh_hans_exemplars_inherit_from_zh_then_root(tmp_path):
    (tmp_path / "main").mkdir()
    (tmp_path / "main/zh_Hans.xml").write_text(
        "<ldml><identity><language type='zh'/></identity></ldml>"
    )
    (tmp_path / "main/zh.xml").write_text(
        "<ldml><characters><exemplarCharacters type='punctuation'>[\u4E2D \\-]</exemplarCharacters>"
        "</characters></ldml>"
    )
    (tmp_path / "main/root.xml").write_text(
        "<ldml><characters><exemplarCharacters type='numbers'>[0 1]</exemplarCharacters>"
        "</characters></ldml>"
    )

    assert repertoire._cldr_exemplar(tmp_path, {}, "zh-Hans", "punctuation") == {
        0x4E2D,
        0x2D,
    }
    assert repertoire._cldr_exemplar(tmp_path, {}, "zh-Hans", "numbers") == {0x30, 0x31}


def test_cldr_upward_marker_falls_back_and_empty_set_does_not(tmp_path):
    assert repertoire._parse_cldr_exemplar("[a \\u4E2D]", "test") == {ord("a"), 0x4E2D}
    assert repertoire._parse_cldr_exemplar("[]", "empty") == set()
    with pytest.raises(ValueError, match="unescaped hyphen"):
        repertoire._parse_cldr_exemplar("[a-z]", "range")

    (tmp_path / "main").mkdir()
    (tmp_path / "main/en.xml").write_text(
        "<ldml><characters><exemplarCharacters type='punctuation'>↑↑↑</exemplarCharacters>"
        "</characters></ldml>"
    )
    (tmp_path / "main/root.xml").write_text(
        "<ldml><characters><exemplarCharacters type='punctuation'>[r]</exemplarCharacters>"
        "</characters></ldml>"
    )
    assert repertoire._cldr_exemplar(tmp_path, {}, "en", "punctuation") == {ord("r")}


def test_extraction_rejects_an_unpinned_cldr_source_before_output(tmp_path):
    common = tmp_path / "common"
    (common / "main").mkdir(parents=True)
    (common / "supplemental").mkdir()
    for relative in repertoire.CLDR_SOURCE_HASHES:
        path = common / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<ldml/>\n")
    output = tmp_path / "output"

    with pytest.raises(ValueError, match="pinned CLDR main/en.xml changed"):
        repertoire.extract_cldr_repertoires(common, output)
    assert not output.exists()
