from dbus_next import Variant

from status349 import notification_text
from status349.notification_text import convert_body, normalize_body, project_body
from status349.sources import notifications
from status349.sources.notifications import parse_notify_body, parse_open_metadata


def _run_text(body: str, run: dict) -> str:
    return body.encode("utf-8")[run["start"] : run["end"]].decode("utf-8")


def test_desktop_markup_keeps_visible_links_alt_text_and_emphasis():
    body, runs = convert_body(
        "chrome",
        "Build status",
        "<p>Build <b>passed</b> &amp; <a href='https://example.test'>open logs</a></p>"
        "<p>Next <img src='image.png' alt='report &amp; notes'>.</p>",
        {},
    )

    assert body == "Build passed & open logs\n\nNext report & notes."
    assert [_run_text(body, run) for run in runs] == ["passed"]
    assert runs[0]["style"] == 1


def test_desktop_markup_decodes_entities_once_and_keeps_unknown_or_escaped_tags_literal():
    body, runs = convert_body(
        "browser",
        "Message",
        "<T>literal</T> &lt;b&gt;not parsed&lt;/b&gt; &amp;lt;T&amp;gt;",
        {},
    )

    assert body == "<T>literal</T> <b>not parsed</b> &lt;T&gt;"
    assert runs == []


def test_desktop_breaks_links_and_image_alt_keep_visible_text_and_lf():
    body, runs = convert_body(
        "chrome",
        "Message",
        "<p>Open <a href='https://example.test'>logs</a><br>next "
        "<img src='image.png' alt='diagram'></p>",
        {},
    )
    assert body == "Open logs\nnext diagram"
    assert runs == []


def test_plain_terminal_text_keeps_lf_and_leaves_markdown_markers_literal():
    body, runs = convert_body("kitty", "Shell", "First\r\nSecond\n\n\n**literal**", {})
    assert body == "First\nSecond\n\n**literal**"
    assert runs == []


def test_desktop_break_sequences_keep_one_blank_line_and_style_offsets():
    for tag in ("<br>", "<br/>"):
        for count in (2, 4):
            body, runs = convert_body(
                "chrome", "Message", "<b>First</b>" + tag * count + "<i>東京</i>", {},
            )
            assert body == "First\n\n東京"
            assert runs == [
                {"start": 0, "end": 5, "style": 1},
                {"start": 7, "end": 13, "style": 2},
            ]


def test_codex_code_links_images_and_paragraphs_keep_visible_content():
    body, runs = convert_body(
        "kitty",
        "Codex",
        "Run `--dry-run` and read [the logs](https://example.test); see ![diagram](image.png).\n\n"
        "Next paragraph.",
        {},
    )
    assert body == "Run --dry-run and read the logs; see diagram.\n\nNext paragraph."
    assert runs == []


def test_codex_markdown_emphasis_code_links_images_and_paragraphs():
    body, runs = convert_body(
        "kitty",
        "Codex",
        "**Build passed.**\nReview *changes* and `--dry-run`; see [logs](https://example.test) "
        "and ![diagram](diagram.png)\n\nNext paragraph.",
        {"desktop-entry": Variant("s", "kitty.desktop")},
    )

    assert body == (
        "Build passed.\nReview changes and --dry-run; see logs and diagram\n\nNext paragraph."
    )
    assert [(_run_text(body, run), run["style"]) for run in runs] == [
        ("Build passed.", 1),
        ("changes", 2),
    ]


def test_normalization_is_idempotent_with_combining_accents_cjk_and_paragraphs():
    raw = "  e\u0301é  東京\t\r\n\r\n\r\n next  "
    normalized = normalize_body(raw)

    assert normalized == "ee 東京\n\nnext"
    assert normalize_body(normalized) == normalized


def test_malformed_html_keeps_plain_text_and_drops_all_emphasis():
    unclosed, unclosed_runs = convert_body("browser", "Message", "<b>unfinished &amp; ready", {})
    mismatched, mismatched_runs = convert_body(
        "browser", "Message", "<b>bold<i>mixed</b>tail</i>", {}
    )

    assert unclosed == "unfinished & ready"
    assert unclosed_runs == []
    assert mismatched == "boldmixedtail"
    assert mismatched_runs == []


def test_display_conversion_happens_before_the_511_byte_clip():
    raw = "<b></b>" * 100 + "<i>visible</i>"
    assert len(raw.encode("utf-8")) > 511

    body, runs = convert_body("browser", "Message", raw, {})

    assert body == "visible"
    assert runs == [{"start": 0, "end": 7, "style": 2}]


def test_clip_ellipsis_is_regular_after_a_clipped_emphasis_run():
    body, runs = project_body("a" * 20, [{"start": 0, "end": 20, "style": 1}], max_bytes=10)

    assert body == "a" * 7 + "…"
    assert len(body.encode("utf-8")) == 10
    assert runs == [{"start": 0, "end": 7, "style": 1}]
    assert body.encode("utf-8")[runs[0]["end"] :] == "…".encode("utf-8")


def test_kitty_guards_are_removed_narrowly_and_result_stays_literal():
    body, runs = convert_body(
        "kitty",
        "Terminal output",
        "a<\u200cT> b&\u200cname \u200ckept",
        {"desktop-entry": Variant("s", "kitty.desktop")},
    )
    assert body == "a<T> b&name \u200ckept"
    assert runs == []


def test_codex_inline_code_keeps_guarded_markup_literal_without_code_style():
    body, runs = convert_body(
        "kitty",
        "Codex",
        "Run `<\u200cT> &\u200c>` now.",
        {"desktop-entry": Variant("s", "kitty.desktop")},
    )
    assert body == "Run <T> &> now."
    assert runs == []


def test_markdown_requires_a_known_terminal_and_exact_codex_title():
    body, runs = convert_body("custom-app", "Codex", "**literal**", {})
    assert body == "**literal**"
    assert runs == []

    body, runs = convert_body("kitty", "codex", "**literal**", {})
    assert body == "**literal**"
    assert runs == []

    body, runs = convert_body("kitty", "Build", "**literal**", {})
    assert body == "**literal**"
    assert runs == []


def test_oversized_sender_suffix_cannot_manufacture_a_terminal_identity():
    padding = " " * (notifications.MAX_NOTIFICATION_SOURCE_IDENTITY_CODEPOINTS + 1)
    cases = (
        ("unknown" + padding + "kitty", {}),
        ("kitty", {"desktop-entry": Variant("s", "unknown" + padding + "kitty.desktop")}),
    )

    for app, hints in cases:
        parsed = parse_notify_body([
            app, 0, "", "Codex", "**literal**", [], hints, 0,
        ])
        assert parsed["body"] == "**literal**"
        assert parsed.get("body_runs", []) == []


def test_notify_display_conversion_does_not_change_raw_open_metadata():
    raw_body = "<b>Checked &amp; ready</b>"
    notify_call = [
        "Chrome", 0, "", "Raw & title", raw_body, ["default", "Open"], {}, 5000,
    ]

    parsed = parse_notify_body(notify_call)
    action_metadata = parse_open_metadata(notify_call)

    assert parsed["body"] == "Checked & ready"
    assert parsed["body_runs"] == [{"start": 0, "end": 15, "style": 1}]
    assert action_metadata == {
        "app": "Chrome",
        "summary": "Raw & title",
        "body": raw_body,
        "default_label": "Open",
    }


def test_oversized_plain_body_is_bounded_before_normalization(monkeypatch):
    observed_sizes = []
    normalize = notification_text._normalized_chars

    def record_and_normalize(text):
        observed_sizes.append(len(text))
        return normalize(text)

    monkeypatch.setattr(notification_text, "_normalized_chars", record_and_normalize)
    body, runs = convert_body(
        "kitty", "Shell", "é" * (notification_text.MAX_BODY_INPUT_CODEPOINTS * 100), {}
    )

    assert observed_sizes and max(observed_sizes) <= notification_text.MAX_BODY_INPUT_CODEPOINTS
    assert body.endswith("…")
    assert len(body.encode("utf-8")) == notification_text.DEFAULT_BODY_BYTES
    assert runs == []


def test_oversized_codex_markdown_is_bounded_and_keeps_unicode_style(monkeypatch):
    parsed_sizes = []
    parse = notification_text._MARKDOWN.parse

    def record_and_parse(text, *args, **kwargs):
        parsed_sizes.append(len(text))
        return parse(text, *args, **kwargs)

    monkeypatch.setattr(notification_text._MARKDOWN, "parse", record_and_parse)
    styled = "**é東京**"
    source = styled + " " * (notification_text.MAX_BODY_INPUT_CODEPOINTS - len(styled)) + "hidden"

    body, runs = convert_body("kitty", "Codex", source, {})

    assert parsed_sizes and max(parsed_sizes) <= notification_text.MAX_BODY_INPUT_CODEPOINTS
    # The existing display policy reduces Latin accents after normalization.
    assert body == "e東京…"
    assert [(_run_text(body, run), run["style"]) for run in runs] == [("e東京", 1)]


def test_oversized_html_with_hidden_leading_markup_is_bounded_and_marked(monkeypatch):
    parser_inputs = []
    feed = notification_text.HTMLParser.feed

    def record_and_feed(parser, source):
        parser_inputs.append(source)
        return feed(parser, source)

    monkeypatch.setattr(notification_text.HTMLParser, "feed", record_and_feed)
    source = "<b></b>" * 1170 + "Visible"

    body, runs = convert_body("browser", "Message", source, {})

    assert parser_inputs == [source[: notification_text.MAX_BODY_INPUT_CODEPOINTS - 1]]
    assert body == "V…"
    assert runs == []


def test_project_body_bounds_validation_and_normalization_and_marks_truncation(monkeypatch):
    observed_validation_sizes = []
    observed_normalization_sizes = []
    validate = notification_text._validated_runs
    normalize = notification_text._normalized_chars

    def record_and_validate(text, runs):
        observed_validation_sizes.append(len(text))
        return validate(text, runs)

    def record_and_normalize(text):
        observed_normalization_sizes.append(len(text))
        return normalize(text)

    monkeypatch.setattr(notification_text, "_validated_runs", record_and_validate)
    monkeypatch.setattr(notification_text, "_normalized_chars", record_and_normalize)

    body, runs = project_body(" \t" * (notification_text.MAX_BODY_INPUT_CODEPOINTS * 100), None)

    assert observed_validation_sizes == [notification_text.MAX_BODY_INPUT_CODEPOINTS - 1]
    assert observed_normalization_sizes == [notification_text.MAX_BODY_INPUT_CODEPOINTS - 1]
    assert body == "…"
    assert runs == []


def test_project_body_drops_a_run_crossing_the_processing_boundary():
    source = " " * (notification_text.MAX_BODY_INPUT_CODEPOINTS + 5)
    # This ends at the display marker's offset if it is appended before range
    # validation, but reaches beyond the original bounded source prefix.
    runs = [{"start": 0, "end": notification_text.MAX_BODY_INPUT_CODEPOINTS + 2, "style": 1}]

    body, projected_runs = project_body(source, runs)

    assert body == "…"
    assert projected_runs == []


def test_project_body_keeps_a_short_style_before_the_processing_boundary():
    source = "東京" + " " * (notification_text.MAX_BODY_INPUT_CODEPOINTS * 2)

    body, runs = project_body(source, [{"start": 0, "end": 6, "style": 1}])

    assert body == "東京…"
    assert runs == [{"start": 0, "end": 6, "style": 1}]
    assert body.encode("utf-8")[runs[0]["end"] :] == "…".encode("utf-8")


def test_bounded_project_body_does_not_split_a_surrogate_pair(monkeypatch):
    observed = []
    normalize = notification_text._normalized_chars

    def record_and_normalize(text):
        observed.append(text)
        return normalize(text)

    monkeypatch.setattr(notification_text, "_normalized_chars", record_and_normalize)
    source = "A" * (notification_text.MAX_BODY_INPUT_CODEPOINTS - 2) + "\ud83d\ude00tail"

    body, runs = project_body(source, None)

    assert observed == ["A" * (notification_text.MAX_BODY_INPUT_CODEPOINTS - 2)]
    assert body == "A" * (notification_text.DEFAULT_BODY_BYTES - len("…".encode("utf-8"))) + "…"
    assert body.encode("utf-8").decode("utf-8") == body
    assert runs == []
