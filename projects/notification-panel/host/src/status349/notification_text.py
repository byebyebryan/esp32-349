"""Source-aware notification body conversion and bounded text styling.

This module deliberately does not import :mod:`status349.proto`: the protocol
module uses ``project_body`` too, so keeping this file independent avoids an
import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import os
import unicodedata

from markdown_it import MarkdownIt


DEFAULT_BODY_BYTES = 511
MAX_BODY_RUNS = 16
MAX_BODY_INPUT_CODEPOINTS = 8192
ELLIPSIS = "…"

_MARKDOWN = MarkdownIt("commonmark", {"html": False, "typographer": False})
_STYLE_TAGS = {"b": 1, "strong": 1, "i": 2, "em": 2}
_HTML_TAGS = {
    "a", "b", "blockquote", "br", "div", "em", "i", "img", "li", "ol",
    "p", "span", "strong", "u", "ul",
}

_TERMINAL_SENDERS = {
    "alacritty", "org.alacritty.alacritty",
    "contour",
    "foot", "footclient", "org.codeberg.dnkl.foot",
    "ghostty", "com.mitchellh.ghostty",
    "gnome-terminal", "org.gnome.terminal", "org.gnome.console", "kgx",
    "hyper",
    "kitty",
    "konsole", "org.kde.konsole",
    "mate-terminal", "terminator", "tilix", "org.gnome.tilix",
    "rio",
    "terminal", "x-terminal-emulator", "xterm",
    "wezterm", "wezterm-gui", "org.wezfurlong.wezterm",
    "warp", "warp-terminal",
    "xfce4-terminal",
}


@dataclass(frozen=True)
class _MappedChar:
    value: str
    source_start: int
    source_end: int


class _StyledText:
    """Build text plus byte ranges without styling source delimiters."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._char_length = 0
        self._char_runs: list[tuple[int, int, int]] = []
        self.valid_styles = True

    @property
    def text(self) -> str:
        return "".join(self._parts)

    @property
    def has_text(self) -> bool:
        return self._char_length > 0

    @property
    def ends_with_newline(self) -> bool:
        return bool(self._parts and self._parts[-1].endswith("\n"))

    def append(self, value: str, style: int = 0) -> None:
        if not value:
            return
        start = self._char_length
        end = start + len(value)
        self._parts.append(value)
        self._char_length = end
        if style:
            if self._char_runs and self._char_runs[-1][1] == start and self._char_runs[-1][2] == style:
                previous_start, _previous_end, previous_style = self._char_runs[-1]
                self._char_runs[-1] = (previous_start, end, previous_style)
            else:
                self._char_runs.append((start, end, style))

    def line_break(self, count: int = 1, style: int = 0) -> None:
        """Append up to ``count`` line breaks, preserving deliberate blank lines."""
        trailing = len(self._parts[-1]) - len(self._parts[-1].rstrip("\n")) if self._parts else 0
        if trailing < count:
            self.append("\n" * (count - trailing), style)

    def byte_runs(self) -> list[dict]:
        if not self._char_runs:
            return []
        text = self.text
        byte_offsets = [0]
        try:
            for char in text:
                byte_offsets.append(byte_offsets[-1] + len(char.encode("utf-8")))
        except UnicodeEncodeError:
            return []
        return [
            {"start": byte_offsets[start], "end": byte_offsets[end], "style": style}
            for start, end, style in self._char_runs
        ]


def _is_hangul_jamo(char: str) -> bool:
    value = ord(char)
    return 0x1100 <= value <= 0x11FF or 0xA960 <= value <= 0xA97F or 0xD7B0 <= value <= 0xD7FF


def _display_character(char: str) -> str:
    if unicodedata.name(char, "").startswith("LATIN"):
        decomposed = unicodedata.normalize("NFKD", char)
        if decomposed and " " <= decomposed[0] <= "~":
            return decomposed[0]
    return char


def _normalized_chars(text: str) -> list[_MappedChar]:
    """Normalize characters while retaining source spans for style projection."""
    units: list[_MappedChar] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                units.append(_MappedChar("\n", index, index + 2))
                index += 2
            else:
                units.append(_MappedChar("\n", index, index + 1))
                index += 1
            continue
        if 0xD800 <= ord(char) <= 0xDFFF:
            char = "\uFFFD"
        elif char != "\n" and (char.isspace() or unicodedata.category(char) == "Cc"):
            char = " "
        units.append(_MappedChar(char, index, index + 1))
        index += 1

    normalized: list[_MappedChar] = []
    start = 0
    while start < len(units):
        end = start + 1
        while end < len(units):
            current = units[end].value
            previous = units[end - 1].value
            if unicodedata.combining(current) or (_is_hangul_jamo(current) and _is_hangul_jamo(previous)):
                end += 1
            else:
                break
        source_start = units[start].source_start
        source_end = units[end - 1].source_end
        cluster = unicodedata.normalize("NFC", "".join(unit.value for unit in units[start:end]))
        for char in cluster:
            if char == "\n":
                normalized.append(_MappedChar(char, source_start, source_end))
            else:
                displayed = _display_character(char)
                if displayed.isspace() or unicodedata.category(displayed) == "Cc":
                    displayed = " "
                normalized.append(_MappedChar(displayed, source_start, source_end))
        start = end

    lines: list[list[_MappedChar]] = [[]]
    breaks: list[_MappedChar | None] = [None]
    for mapped in normalized:
        if mapped.value == "\n":
            lines.append([])
            breaks.append(mapped)
            continue
        line = lines[-1]
        if mapped.value == " " and line and line[-1].value == " ":
            previous = line[-1]
            line[-1] = _MappedChar(
                " ", min(previous.source_start, mapped.source_start), max(previous.source_end, mapped.source_end)
            )
        else:
            line.append(mapped)

    for line_index, line in enumerate(lines):
        start = 0
        end = len(line)
        while start < end and line[start].value == " ":
            start += 1
        while end > start and line[end - 1].value == " ":
            end -= 1
        lines[line_index] = line[start:end]

    first = next((i for i, line in enumerate(lines) if line), None)
    if first is None:
        return []
    last = next(i for i in range(len(lines) - 1, -1, -1) if lines[i])
    selected: list[int] = []
    previous_blank = False
    for line_index in range(first, last + 1):
        blank = not lines[line_index]
        if blank and previous_blank:
            continue
        selected.append(line_index)
        previous_blank = blank

    result: list[_MappedChar] = []
    for selected_index, line_index in enumerate(selected):
        if selected_index:
            separator = breaks[line_index]
            # A selected blank line creates the second LF in a paragraph gap.
            # If empty lines were collapsed, the nearest retained separator is
            # sufficient; removed separators carry no display characters.
            if separator is not None:
                result.append(_MappedChar("\n", separator.source_start, separator.source_end))
        result.extend(lines[line_index])
    return result


def _bounded_source(text: str) -> tuple[str, bool]:
    """Keep body processing bounded, reserving space for a truncation marker."""
    if len(text) <= MAX_BODY_INPUT_CODEPOINTS:
        return text, False

    # Leave one code point for the regular ellipsis appended by the caller.
    end = MAX_BODY_INPUT_CODEPOINTS - len(ELLIPSIS)
    # Python strings can contain explicit UTF-16 surrogate pairs. Do not leave
    # half of such a pair at the end of the bounded prefix.
    if end and end < len(text):
        previous = ord(text[end - 1])
        following = ord(text[end])
        if 0xD800 <= previous <= 0xDBFF and 0xDC00 <= following <= 0xDFFF:
            end -= 1
    return text[:end], True


def normalize_body(text: str) -> str:
    """Normalize body Unicode and whitespace while keeping useful LF breaks."""
    if not isinstance(text, str):
        return ""
    return "".join(mapped.value for mapped in _normalized_chars(text))


def _validated_runs(text: str, runs: list[dict] | None) -> tuple[list[tuple[int, int, int]], bool]:
    if runs is None:
        return [], True
    if not isinstance(runs, list):
        return [], False
    if not runs:
        return [], True
    if len(runs) > MAX_BODY_RUNS:
        return [], False
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        return [], False

    byte_to_char = {0: 0}
    byte_offset = 0
    for char_index, char in enumerate(text, start=1):
        byte_offset += len(char.encode("utf-8"))
        byte_to_char[byte_offset] = char_index

    normalized_runs: list[tuple[int, int, int]] = []
    previous_end = 0
    for run in runs:
        if not isinstance(run, dict) or set(run) != {"start", "end", "style"}:
            return [], False
        start = run.get("start")
        end = run.get("end")
        style = run.get("style")
        if (
            isinstance(start, bool) or not isinstance(start, int)
            or isinstance(end, bool) or not isinstance(end, int)
            or isinstance(style, bool) or not isinstance(style, int)
            or style not in (1, 2, 3)
            or start < previous_end or start < 0 or end <= start or end > len(encoded)
            or start not in byte_to_char or end not in byte_to_char
        ):
            return [], False
        normalized_runs.append((byte_to_char[start], byte_to_char[end], style))
        previous_end = end
    return normalized_runs, True


def _project_runs(
    mapped_text: list[_MappedChar], source_runs: list[tuple[int, int, int]]
) -> list[dict]:
    if not source_runs:
        return []
    char_styles: list[int] = []
    run_index = 0
    for mapped in mapped_text:
        while run_index < len(source_runs) and source_runs[run_index][1] <= mapped.source_start:
            run_index += 1
        style = 0
        index = run_index
        while index < len(source_runs) and source_runs[index][0] < mapped.source_end:
            start, end, run_style = source_runs[index]
            if start < mapped.source_end and end > mapped.source_start:
                style |= run_style
            index += 1
        char_styles.append(style)

    result: list[dict] = []
    byte_offset = 0
    current_start: int | None = None
    current_end = 0
    current_style = 0
    for mapped, style in zip(mapped_text, char_styles, strict=True):
        char_bytes = len(mapped.value.encode("utf-8"))
        if style:
            if current_start is not None and current_end == byte_offset and current_style == style:
                current_end += char_bytes
            else:
                if current_start is not None:
                    result.append({"start": current_start, "end": current_end, "style": current_style})
                current_start = byte_offset
                current_end = byte_offset + char_bytes
                current_style = style
        elif current_start is not None:
            result.append({"start": current_start, "end": current_end, "style": current_style})
            current_start = None
            current_style = 0
        byte_offset += char_bytes
    if current_start is not None:
        result.append({"start": current_start, "end": current_end, "style": current_style})
    return result


def _clip_body(text: str, runs: list[dict], max_bytes: int) -> tuple[str, list[dict]]:
    if max_bytes <= 0:
        return "", []
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        clipped_bytes = len(encoded)
        clipped_text = text
    elif max_bytes < len(ELLIPSIS.encode("utf-8")):
        clipped_text = encoded[:max_bytes].decode("utf-8", "ignore")
        clipped_bytes = len(clipped_text.encode("utf-8"))
    else:
        prefix_limit = max_bytes - len(ELLIPSIS.encode("utf-8"))
        prefix: list[str] = []
        prefix_bytes = 0
        for char in text:
            char_bytes = len(char.encode("utf-8"))
            if prefix_bytes + char_bytes > prefix_limit:
                break
            prefix.append(char)
            prefix_bytes += char_bytes
        clipped_text = "".join(prefix) + ELLIPSIS
        clipped_bytes = prefix_bytes

    clipped_runs: list[dict] = []
    for run in runs:
        start = run["start"]
        end = min(run["end"], clipped_bytes)
        if start < end:
            clipped_runs.append({"start": start, "end": end, "style": run["style"]})
    return clipped_text, clipped_runs


def project_body(
    text: str,
    runs: list[dict] | None,
    max_bytes: int = DEFAULT_BODY_BYTES,
) -> tuple[str, list[dict]]:
    """Normalize, validate, map and UTF-8-clip one body and its style ranges.

    Input ranges use UTF-8 byte offsets into ``text``. Invalid range data drops
    all styles while retaining the normalized text.
    """
    if not isinstance(text, str):
        return "", []
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")
    text, truncated = _bounded_source(text)
    source_runs, valid = _validated_runs(text, runs)
    mapped_text = _normalized_chars(text)
    normalized = "".join(mapped.value for mapped in mapped_text)
    mapped_runs = _project_runs(mapped_text, source_runs) if valid else []
    if len(mapped_runs) > MAX_BODY_RUNS:
        mapped_runs = []
    if truncated:
        # Append after range projection so a source range crossing the bounded
        # prefix can never style this synthetic display character.
        normalized += ELLIPSIS
    return _clip_body(normalized, mapped_runs, max_bytes)


def _hint_text(hints: object, key: str) -> str | None:
    if not isinstance(hints, dict):
        return None
    value = hints.get(key)
    value = getattr(value, "value", value)
    return value if isinstance(value, str) else None


def _sender_name(app: str, hints: object) -> str:
    desktop_entry = _hint_text(hints, "desktop-entry")
    source = desktop_entry if desktop_entry else app
    source = os.path.basename(source).strip().lower()
    if source.endswith(".desktop"):
        source = source[:-8]
    return source


def _is_terminal(app: str, hints: object) -> tuple[bool, bool]:
    sender = _sender_name(app, hints)
    return sender in _TERMINAL_SENDERS, sender == "kitty"


def _remove_kitty_guards(text: str) -> str:
    chars: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        chars.append(char)
        if char in "<&" and index + 1 < len(text) and text[index + 1] == "\u200c":
            index += 2
        else:
            index += 1
    return "".join(chars)


def _render_inline(tokens: list, builder: _StyledText, base_style: int = 0) -> None:
    stack: list[tuple[str, int]] = []
    style = base_style
    for token in tokens:
        token_type = token.type
        if token_type in ("strong_open", "em_open"):
            name, bit = ("strong", 1) if token_type == "strong_open" else ("em", 2)
            stack.append((name, bit))
            style |= bit
        elif token_type in ("strong_close", "em_close"):
            name = "strong" if token_type == "strong_close" else "em"
            if not stack or stack[-1][0] != name:
                builder.valid_styles = False
                continue
            stack.pop()
            style = base_style
            for _name, bit in stack:
                style |= bit
        elif token_type == "image":
            children = token.children or []
            if children:
                _render_inline(children, builder, style)
            else:
                alt = token.attrGet("alt")
                if alt:
                    builder.append(str(alt), style)
        elif token_type in ("softbreak", "hardbreak"):
            builder.append("\n", style)
        elif token_type in ("link_open", "link_close"):
            continue
        elif token_type in ("text", "text_special", "code_inline", "html_inline"):
            # Inline code loses its delimiters but receives no code-specific style.
            # Raw HTML is literal in a terminal message; never feed it to the HTML parser.
            builder.append(token.content, style)
        elif token.children:
            _render_inline(token.children, builder, style)
        elif token.content:
            builder.append(token.content, style)
    if stack:
        builder.valid_styles = False


def _markdown_text(text: str) -> tuple[str, list[dict]]:
    builder = _StyledText()
    try:
        tokens = _MARKDOWN.parse(text)
    except (ValueError, RuntimeError):
        return text, []

    list_stack: list[tuple[str, int]] = []
    previous_block = False
    item_prefix_pending = False
    for token in tokens:
        token_type = token.type
        if token_type in ("bullet_list_open", "ordered_list_open"):
            start = 1
            if token_type == "ordered_list_open":
                raw_start = token.attrGet("start")
                try:
                    start = max(1, int(raw_start)) if raw_start is not None else 1
                except (TypeError, ValueError):
                    start = 1
            list_stack.append(("ordered" if token_type == "ordered_list_open" else "bullet", start - 1))
        elif token_type in ("bullet_list_close", "ordered_list_close"):
            if list_stack:
                list_stack.pop()
        elif token_type == "list_item_open":
            if builder.has_text and not builder.ends_with_newline:
                builder.line_break()
            if list_stack and list_stack[-1][0] == "ordered":
                kind, number = list_stack[-1]
                number += 1
                list_stack[-1] = (kind, number)
                builder.append(f"{number}. ")
            else:
                builder.append("• ")
            item_prefix_pending = True
            previous_block = False
        elif token_type == "inline":
            if builder.has_text and not builder.ends_with_newline and not item_prefix_pending:
                builder.line_break(2 if previous_block else 1)
            _render_inline(token.children or [], builder)
            previous_block = True
            item_prefix_pending = False
        elif token_type in ("fence", "code_block"):
            if builder.has_text and not builder.ends_with_newline:
                builder.line_break(2 if previous_block else 1)
            builder.append(token.content)
            previous_block = True
            item_prefix_pending = False
        elif token_type in ("html_block",):
            # Keep raw terminal HTML intact, including tag-like code samples.
            if builder.has_text and not builder.ends_with_newline:
                builder.line_break()
            builder.append(token.content)
            previous_block = True
            item_prefix_pending = False
        elif token_type == "hr":
            if builder.has_text and not builder.ends_with_newline:
                builder.line_break(2 if previous_block else 1)
            builder.append("---")
            previous_block = True
            item_prefix_pending = False

    if not builder.valid_styles:
        return builder.text, []
    return builder.text, builder.byte_runs()


class _DesktopMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.builder = _StyledText()
        self._style_stack: list[tuple[str, int]] = []
        self._source = ""
        self._line_starts = [0]

    def set_source(self, source: str) -> None:
        self._source = source
        self._line_starts = [0]
        self._line_starts.extend(index + 1 for index, char in enumerate(source) if char == "\n")

    def _current_end_tag(self, tag: str) -> str:
        line, column = self.getpos()
        line_index = min(max(0, line - 1), len(self._line_starts) - 1)
        start = self._line_starts[line_index] + column
        if self._source.startswith("</", start):
            end = self._source.find(">", start)
            if end >= 0:
                return self._source[start:end + 1]
            return self._source[start:]
        return f"</{tag}>"

    @property
    def style(self) -> int:
        value = 0
        for _tag, bit in self._style_stack:
            value |= bit
        return value

    def _style_open(self, tag: str) -> None:
        self._style_stack.append((tag, _STYLE_TAGS[tag]))

    def _style_close(self, tag: str) -> None:
        if not self._style_stack or self._style_stack[-1][0] != tag:
            self.builder.valid_styles = False
            return
        self._style_stack.pop()

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], raw: str) -> None:
        if tag not in _HTML_TAGS:
            self.builder.append(raw, self.style)
            return
        if tag in _STYLE_TAGS:
            self._style_open(tag)
        elif tag in ("p", "div", "blockquote"):
            if self.builder.has_text and not self.builder.ends_with_newline:
                self.builder.line_break()
        elif tag == "li":
            if self.builder.has_text and not self.builder.ends_with_newline:
                self.builder.line_break()
            self.builder.append("• ")
        elif tag == "br":
            # Each explicit break matters; normalization bounds blank lines.
            self.builder.append("\n")
        elif tag == "img":
            values = dict(attrs)
            alt = values.get("alt")
            if alt:
                self.builder.append(alt, self.style)

    def _end(self, tag: str, raw: str) -> None:
        if tag not in _HTML_TAGS:
            self.builder.append(raw, self.style)
            return
        if tag in _STYLE_TAGS:
            self._style_close(tag)
        elif tag in ("p", "div", "blockquote"):
            self.builder.line_break(2)
        elif tag == "li":
            self.builder.line_break()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _HTML_TAGS:
            self.builder.append(self.get_starttag_text(), self.style)
            return
        if tag == "img":
            values = dict(attrs)
            alt = values.get("alt")
            if alt:
                self.builder.append(alt, self.style)
        elif tag == "br":
            self.builder.append("\n")
        elif tag in _STYLE_TAGS:
            self.builder.valid_styles = False

    def handle_endtag(self, tag: str) -> None:
        self._end(tag, self._current_end_tag(tag))

    def handle_data(self, data: str) -> None:
        self.builder.append(data, self.style)

    def handle_entityref(self, name: str) -> None:
        self.builder.append(unescape(f"&{name};"), self.style)

    def handle_charref(self, name: str) -> None:
        self.builder.append(unescape(f"&#{name};"), self.style)

    def handle_comment(self, data: str) -> None:
        self.builder.append(f"<!--{data}-->", self.style)

    def handle_decl(self, decl: str) -> None:
        self.builder.append(f"<!{decl}>", self.style)

    def unknown_decl(self, data: str) -> None:
        self.builder.append(f"<![{data}]>", self.style)

    def handle_pi(self, data: str) -> None:
        self.builder.append(f"<?{data}>", self.style)


def _desktop_markup_text(text: str) -> tuple[str, list[dict]]:
    parser = _DesktopMarkupParser()
    parser.set_source(text)
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # A parser failure should retain the original, readable notification.
        return text, []
    if parser._style_stack:
        parser.builder.valid_styles = False
    if not parser.builder.valid_styles:
        return parser.builder.text, []
    return parser.builder.text, parser.builder.byte_runs()


def convert_body(app: str, summary: str, body: str, hints: object) -> tuple[str, list[dict]]:
    """Convert one desktop body according to its positively identified source."""
    if not isinstance(body, str):
        body = str(body)
    body, truncated = _bounded_source(body)
    terminal, kitty = _is_terminal(str(app), hints)
    if kitty:
        body = _remove_kitty_guards(body)

    if terminal:
        if summary == "Codex":
            text, runs = _markdown_text(body)
        else:
            text, runs = body, []
    else:
        text, runs = _desktop_markup_text(body)
    if truncated:
        # The marker is added after parsing so even hidden leading markup and
        # whitespace cannot make a clipped source look like a complete body.
        text += ELLIPSIS
    return project_body(text, runs)
