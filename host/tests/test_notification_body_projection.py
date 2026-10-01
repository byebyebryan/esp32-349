import asyncio

import pytest
from dbus_next import Message, MessageType
from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.sources.notifications import NOTIFICATIONS_NAME, NotificationSource

HISTORY_CAPABILITIES = [
    proto.CARD_SYNC_CAPABILITY,
    proto.DASHBOARD_CAPABILITY,
    proto.GROUPED_UI_CAPABILITY,
    proto.NOTIFICATION_HISTORY_CAPABILITY,
]


class _Writer:
    def __init__(self):
        self.frames: list[dict] = []

    def write(self, payload: bytes) -> None:
        is_data, message = proto.classify(payload.decode("utf-8").rstrip("\n"))
        assert is_data and message is not None
        self.frames.append(message)

    async def drain(self) -> None:
        return None


def _style_hello(capabilities: list[str]) -> dict:
    return {"t": "hello", "proto": 1, "cap": capabilities, "cache_cards": 32}


def _history_snapshot(cards: list[dict]) -> dict:
    return {
        "rev": 1,
        "bar": proto.bar([], 1),
        "clock": None,
        "media": None,
        "dashboard": {},
        "notifs": cards,
        "limit": 32,
        "overflow": 0,
    }


def _sync_cards(messages: list[dict]) -> list[dict]:
    return [
        card
        for message in messages
        if message["t"] == "sync_cards"
        for card in message["notifs"]
    ]


def test_body_style_capability_requires_the_full_history_capability_set():
    assert not proto.notification_body_style_capable(
        _style_hello([proto.NOTIFICATION_BODY_STYLE_CAPABILITY])
    )
    assert not proto.notification_body_style_capable(
        _style_hello(
            [*HISTORY_CAPABILITIES[:-1], proto.NOTIFICATION_BODY_STYLE_CAPABILITY]
        )
    )
    assert proto.notification_body_style_capable(
        _style_hello([*HISTORY_CAPABILITIES, proto.NOTIFICATION_BODY_STYLE_CAPABILITY])
    )


def test_notification_projection_normalizes_text_and_remaps_utf8_ranges():
    raw_body = "A\r\n  e\u0301\t東京 "
    # The range covers the decomposed e plus combining acute in the input.
    body_runs = [{"start": 5, "end": 8, "style": 1}]

    message = proto.notify(1, "app", "summary", raw_body, 1, 0, 1, body_runs=body_runs)

    assert message["body"] == "A\ne 東京"
    assert message["body_runs"] == [{"start": 2, "end": 3, "style": 1}]


def test_notification_body_styles_clip_on_utf8_boundaries_and_keep_ellipsis_regular():
    body = "a" * 507 + "東京"
    message = proto.notify(
        1,
        "app",
        "summary",
        body,
        1,
        0,
        1,
        body_runs=[{"start": 507, "end": len(body.encode("utf-8")), "style": 2}],
    )

    assert len(message["body"].encode("utf-8")) <= proto.NOTIFICATION_BODY_HISTORY_BYTES
    assert message["body"].endswith("…")
    assert message["body"] == "a" * 507 + "…"
    assert "body_runs" not in message


@pytest.mark.parametrize(
    "runs",
    [
        [{"start": 2, "end": 4, "style": 1}],  # starts inside a multibyte code point
        [{"start": 1, "end": 4, "style": 1}, {"start": 3, "end": 5, "style": 2}],
        [{"start": 1, "end": 1, "style": 1}],
        [{"start": 1, "end": 3, "style": 4}],
        [{"start": 1, "end": 3, "style": True}],
        [{"start": 1, "end": 99, "style": 1}],
    ],
)
def test_malformed_body_styles_degrade_as_a_whole_to_plain_text(runs):
    message = proto.notify(1, "app", "summary", "Aé東京Z", 1, 0, 1, body_runs=runs)

    assert message["body"] == "Ae東京Z"
    assert "body_runs" not in message


def test_more_than_sixteen_disjoint_runs_degrades_to_plain_text():
    body = "x-x-" * 17
    runs = [
        {"start": index * 4, "end": index * 4 + 1, "style": 1} for index in range(17)
    ]

    message = proto.notify(1, "app", "summary", body, 1, 0, 1, body_runs=runs)

    assert message["body"] == body
    assert "body_runs" not in message


def test_display_conversion_keeps_original_open_action_metadata():
    async def scenario():
        raw_body = "<b>Open &amp; 東京</b><br>literal <T>"
        call_body = [
            "Desktop App",
            0,
            "",
            "Raw title",
            raw_body,
            ["default", "Open"],
            {},
            10000,
        ]
        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            lambda _local_id: asyncio.sleep(0),
        )
        source._server_owner, source._server_pid = ":1.40", 40
        await source._handle(
            Message(
                destination=NOTIFICATIONS_NAME,
                path="/org/freedesktop/Notifications",
                interface=NOTIFICATIONS_NAME,
                member="Notify",
                signature="susssasa{sv}i",
                body=call_body,
                sender=":1.50",
                serial=1,
            )
        )
        local_id = next(iter(source._outbox))
        display = source._outbox[local_id]
        expected = {
            "app": "Desktop App",
            "summary": "Raw title",
            "body": raw_body,
            "default_label": "Open",
        }
        assert display["body"] == "Open & 東京\nliteral <T>"
        assert display["body_runs"] == [{"start": 0, "end": 13, "style": 1}]
        assert source._open_info[local_id]["expected"] == expected

        await source._handle(
            Message(
                message_type=MessageType.METHOD_RETURN,
                destination=":1.50",
                sender=":1.40",
                reply_serial=1,
                signature="u",
                body=[9],
            )
        )
        candidate = source.action_candidate(local_id)
        assert candidate is not None and candidate["expected"] == expected
        assert candidate["expected"]["body"] == raw_body

    asyncio.run(scenario())


def test_full_sync_includes_styles_only_when_requested_and_requires_history():
    card = {
        "id": 1,
        "app": "app",
        "summary": "summary",
        "body": "plain 東京",
        "body_runs": [{"start": 6, "end": 12, "style": 3}],
        "urgency": 1,
        "history": {"rev": 1, "age_ms": 0, "remaining_ms": 10000},
    }

    styled = proto.card_sync_messages(
        _history_snapshot([card]),
        1,
        include_dashboard=True,
        grouped_session=12,
        include_history=True,
        include_body_styles=True,
    )
    legacy = proto.card_sync_messages(
        _history_snapshot([card]),
        2,
        include_dashboard=True,
        grouped_session=12,
        include_history=True,
    )

    assert _sync_cards(styled)[0]["body_runs"] == card["body_runs"]
    assert "body_runs" not in _sync_cards(legacy)[0]
    assert (
        _sync_cards(styled)[0]["body"] == _sync_cards(legacy)[0]["body"] == card["body"]
    )
    with pytest.raises(ValueError, match="require history"):
        proto.card_sync_messages(
            _history_snapshot([card]),
            3,
            include_dashboard=True,
            grouped_session=12,
            include_body_styles=True,
        )


def test_sync_chunks_measure_hostile_json_with_styles_and_keep_the_hard_line_bound():
    body = ('"\\' * 255) + "x"
    # JSON escapes every quote and backslash. Repeating the bounded, worst-case
    # body across the cache exercises the 2048-byte chunk target.
    runs = [
        {"start": index * 30, "end": index * 30 + 20, "style": 1} for index in range(16)
    ]
    cards = [
        {
            "id": card_id,
            "app": "app",
            "summary": "hostile",
            "body": body,
            "body_runs": runs,
            "urgency": 1,
            "history": {"rev": 1, "age_ms": 0, "remaining_ms": 10000},
        }
        for card_id in range(1, 33)
    ]

    messages = proto.card_sync_messages(
        _history_snapshot(cards),
        9,
        include_dashboard=True,
        grouped_session=12,
        include_history=True,
        include_body_styles=True,
    )
    encoded = [proto.encode(message) for message in messages]
    assert all(len(frame) <= proto.LINE_MAX for frame in encoded)
    projected = _sync_cards(messages)
    assert len(projected) == 32 and all(card["body_runs"] == runs for card in projected)
    card_frames = [frame for frame in encoded if b'"t":"sync_cards"' in frame]
    assert len(card_frames) > 1
    assert all(len(frame) <= proto.CARD_CHUNK_MAX for frame in card_frames)


def test_incremental_and_full_sync_projection_match_and_replacement_clears_styles(
    monkeypatch,
):
    async def scenario():
        now = [100.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        cfg = default_config()
        daemon = Daemon(cfg, asyncio.Event())
        daemon.clock.read = lambda: (1790411700, -7 * 3600)
        daemon._sample = lambda: {"cpu": 0.1, "mem": 0.2, "network": True}
        writer = _Writer()
        daemon._writer = writer
        hello = _style_hello(
            [*HISTORY_CAPABILITIES, proto.NOTIFICATION_BODY_STYLE_CAPABILITY]
        )
        await daemon._on_line(proto.encode(hello).decode("utf-8").rstrip())
        assert daemon._history_enabled and daemon._body_style_enabled

        original = proto.notify(
            11,
            "app",
            "bold card",
            "Hello 東京",
            1,
            0,
            1,
            body_runs=[{"start": 6, "end": 12, "style": 1}],
        )
        await daemon._device_notify(original)
        incremental = next(
            frame for frame in reversed(writer.frames) if frame["t"] == "notify"
        )
        assert incremental["body"] == "Hello 東京"
        assert incremental["body_runs"] == original["body_runs"]

        await daemon._send_sync()
        cards = [
            card
            for frame in writer.frames
            if frame["t"] == "sync_cards"
            for card in frame["notifs"]
        ]
        assert cards[-1]["id"] == 11
        assert cards[-1]["body"] == incremental["body"]
        assert cards[-1]["body_runs"] == incremental["body_runs"]

        plain_replacement = proto.notify(
            11, "app", "replacement", "plain 東京", 1, 0, 2
        )
        await daemon._device_notify(plain_replacement)
        replacement = next(
            frame for frame in reversed(writer.frames) if frame["t"] == "notify"
        )
        assert replacement["body"] == "plain 東京"
        assert "body_runs" not in replacement
        await daemon._send_sync()
        cards = [
            card
            for frame in writer.frames
            if frame["t"] == "sync_cards"
            for card in frame["notifs"]
        ]
        assert cards[-1]["id"] == 11
        assert cards[-1]["body"] == replacement["body"]
        assert "body_runs" not in cards[-1]

        # Older peers retain history/plain projection while stripping the style
        # capability and all optional runs from both incremental and sync data.
        legacy_hello = _style_hello(HISTORY_CAPABILITIES)
        await daemon._on_line(proto.encode(legacy_hello).decode("utf-8").rstrip())
        assert daemon._history_enabled and not daemon._body_style_enabled
        styled_again = proto.notify(
            12,
            "app",
            "legacy",
            "Legacy plain",
            1,
            0,
            3,
            body_runs=[{"start": 0, "end": 6, "style": 2}],
        )
        await daemon._device_notify(styled_again)
        legacy_delta = next(
            frame
            for frame in reversed(writer.frames)
            if frame["t"] == "notify" and frame["id"] == 12
        )
        assert "body_runs" not in legacy_delta
        await daemon._send_sync()
        sync = [
            card
            for frame in writer.frames
            if frame["t"] == "sync_cards"
            for card in frame["notifs"]
        ]
        legacy_card = next(card for card in sync if card["id"] == 12)
        assert "body_runs" not in legacy_card
        assert legacy_card["body"] == legacy_delta["body"]

    asyncio.run(scenario())
