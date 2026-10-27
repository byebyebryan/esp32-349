"""Integrity and inheritance tests for the pinned notification font profile."""

import importlib.util
from pathlib import Path

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
        "Box Drawing": 128,
        "Block Elements": 32,
        "Braille Patterns": 256,
    }
    assert len(repertoire.notification_profile()) == 4365


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

    assert len(assigned) == 355
    assert len(set(assigned)) == 355
    assert sum(map(len, assignments.values())) == 355


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
