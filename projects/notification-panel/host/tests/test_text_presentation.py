"""Projection behavior at the boundary between Unicode and bitmap text."""

import pytest

from status349 import proto
from status349.notification_text import normalize_body, project_body


@pytest.mark.parametrize("selector", ["\uFE0E", "\uFE0F"])
def test_supported_symbols_use_text_bases_in_labels_and_styled_bodies(selector):
    source = f"A⚠{selector}中Z"
    assert proto.display_text(source) == "A⚠中Z"
    text, runs = project_body(source, [{"start": 1, "end": 10, "style": 3}])
    assert text == "A⚠中Z"
    assert runs == [{"start": 1, "end": 7, "style": 3}]
    # A second wire projection is stable; removed bytes never shift style
    # onto the following unstyled character.
    assert project_body(text, runs) == (text, runs)
    card = proto.notify(1, "app", source, source, 1, 0, 1)
    assert card["summary"] == card["body"] == "A⚠中Z"


@pytest.mark.parametrize("source", [
    "\uFE0E\uFE0F",       # isolated selectors
    "A\uFE0F",            # no registered variation sequence
    "中\uFE0F",           # ideographic presentation is a separate policy
    "中\uFE00",           # other variation selectors remain intact
    "🚀\uFE0F",           # unsupported emoji base
    "👩\u200D💻",          # unsupported ZWJ sequence
    "⚠\uFE0F\uFE0E",     # malformed selector run
    "x\u0301",            # noncomposing combining mark
])
def test_other_unicode_sequences_are_not_blanket_stripped(source):
    assert proto.display_text(source) == source
    assert normalize_body(source) == source
    assert normalize_body(normalize_body(source)) == source


def test_selector_only_style_does_not_style_neighboring_base_or_text():
    assert project_body("⚠️Z", [{"start": 3, "end": 6, "style": 1}]) == ("⚠Z", [])


def test_text_fallback_precedes_utf8_clipping_and_preserves_accent_policy():
    assert project_body("⚠️中文", None, max_bytes=6) == ("⚠…", [])
    assert proto.display_text("Café / Cafe\u0301 / ⚠️") == "Cafe / Cafe / ⚠"
    assert normalize_body("Café\nCafe\u0301\n⚠️") == "Cafe\nCafe\n⚠"
