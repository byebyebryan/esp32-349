import json

import pytest

from status349 import proto


def test_encode_prefix_and_newline():
    assert proto.encode({"t": "ping", "ts": 1}) == b'@349 {"t":"ping","ts":1}\n'


def test_classify_data_line():
    assert proto.classify('@349 {"t":"pong","ts":1}') == (True, {"t": "pong", "ts": 1})


def test_classify_log_line():
    assert proto.classify("I (123) boot: hello") == (False, None)


def test_classify_malformed_json():
    assert proto.classify("@349 nope") == (True, None)


def test_classify_non_object():
    assert proto.classify("@349 [1,2]") == (True, None)


def test_bar_builder():
    assert proto.bar([{"id": "clock"}], 3) == {"t": "bar", "rev": 3, "zones": [{"id": "clock"}]}


def test_clock_builder():
    assert proto.clock(1.9, -3600.7) == {"t": "clock", "epoch": 1, "offset": -3600}


def test_media_builder():
    message = proto.media("playing", "title", "artist", "album", 1.23456, 180.0)
    assert message["t"] == "media"
    assert message["pos"] == 1.235
    assert message["len"] == 180.0


def test_notify_and_close_builders():
    message = proto.notify(7, "app", "sum", "body", 1, 5000, 42)
    assert message == {
        "t": "notify",
        "id": 7,
        "app": "app",
        "summary": "sum",
        "body": "body",
        "urgency": 1,
        "expire": 5000,
        "ts": 42,
    }
    assert proto.close(7) == {"t": "close", "id": 7}
    assert proto.notify(7, "app", "sum", "body", 1, 5000, 42, total=3)["total"] == 3
    assert proto.notify(7, "app", "sum", "body", 1, 5000, 42, total=3, cached=False)["cached"] is False
    assert proto.close(7, total=2) == {"t": "close", "id": 7, "total": 2}


def test_card_sync_capability_requires_an_advertised_integer_capacity():
    assert proto.card_sync_capacity({"cap": ["link", "card-sync-v1"], "cache_cards": 32}) == 32
    assert proto.card_sync_capacity({"cap": "card-sync-v1", "cache_cards": 4}) == 4
    assert proto.card_sync_capacity({"cap": ["link"], "cache_cards": 32}) is None
    assert proto.card_sync_capacity({"cap": ["card-sync-v1"], "cache_cards": True}) is None


def test_dashboard_capability_requires_card_sync_v1_too():
    assert proto.dashboard_capable({"cap": ["card-sync-v1", "dashboard-v1"]})
    assert not proto.dashboard_capable({"cap": ["dashboard-v1"]})
    assert not proto.dashboard_capable({"cap": ["card-sync-v1"]})
    assert not proto.dashboard_capable({"cap": "dashboard-v1"})


def test_grouped_capability_requires_cache_dashboard_and_grouped_v1():
    assert proto.grouped_ui_capable(
        {"cap": ["card-sync-v1", "dashboard-v1", "grouped-ui-v1"], "cache_cards": 32}
    )
    assert not proto.grouped_ui_capable(
        {"cap": ["card-sync-v1", "grouped-ui-v1"], "cache_cards": 32}
    )
    assert not proto.grouped_ui_capable(
        {"cap": ["dashboard-v1", "grouped-ui-v1"], "cache_cards": 32}
    )
    assert not proto.grouped_ui_capable(
        {"cap": ["card-sync-v1", "dashboard-v1"], "cache_cards": 32}
    )


def test_notification_history_capability_requires_complete_grouped_dashboard_set():
    capable = {
        "cap": [
            "card-sync-v1", "dashboard-v1", "grouped-ui-v1", "notification-history-v1",
        ],
        "cache_cards": 32,
    }
    assert proto.notification_history_capable(capable)
    assert not proto.notification_history_capable({**capable, "cap": capable["cap"][:-1]})
    assert not proto.notification_history_capable(
        {**capable, "cap": ["card-sync-v1", "grouped-ui-v1", "notification-history-v1"]}
    )
    assert not proto.notification_history_capable(
        {**capable, "cap": ["dashboard-v1", "grouped-ui-v1", "notification-history-v1"]}
    )


def test_notification_action_capability_requires_grouped_cache_and_valid_boot_id():
    capable = {
        "cap": [
            "card-sync-v1", "dashboard-v1", "grouped-ui-v1", "notification-actions-v1",
        ],
        "cache_cards": 32,
        "boot_id": 7,
    }
    assert proto.notification_actions_capable(capable)
    for boot_id in (None, 0, True, -1, 0x1_0000_0000):
        assert not proto.notification_actions_capable({**capable, "boot_id": boot_id})
    assert not proto.notification_actions_capable({**capable, "cap": capable["cap"][:-1]})
    assert not proto.notification_actions_capable({**capable, "cap": ["notification-actions-v1"], "cache_cards": 32})


def test_cards_status_requires_nonnegative_counts_and_integer_ids():
    assert proto.card_status({"count": 2, "overflow": 1, "ids": [8, 7], "capacity": 32}) == {
        "count": 2,
        "overflow": 1,
        "ids": [8, 7],
        "capacity": 32,
    }
    pending = {"count": 0, "overflow": 0, "ids": [], "capacity": 32, "view_pending": True}
    assert proto.card_status(pending) == pending
    assert proto.card_status({**pending, "view_pending": 1}) is None
    assert proto.card_status({**pending, "deck": {}}) is None
    assert proto.card_status({"count": True, "overflow": 1, "ids": [], "capacity": 32}) is None
    assert proto.card_status({"count": 1, "overflow": -1, "ids": [1], "capacity": 32}) is None
    assert proto.card_status({"count": 1, "overflow": 0, "ids": ["1"], "capacity": 32}) is None
    assert proto.card_status({"count": 2, "overflow": 0, "ids": [1], "capacity": 32}) is None
    assert proto.card_status({"count": 2, "overflow": 0, "ids": [1, 2], "capacity": 1}) is None
    assert proto.card_status({"count": 2, "overflow": 0, "ids": [1, 1], "capacity": 32}) is None


def test_cards_status_validates_action_metadata_and_pending_identity():
    base = {"count": 2, "overflow": 0, "ids": [8, 7], "capacity": 32}
    actions = {
        "enabled": True,
        "open": [
            {"id": 8, "rev": 3, "state": "ready"},
            {"id": 7, "rev": 4, "state": "unavailable"},
        ],
        # A dispatched request can still be pending after its card was closed.
        "pending": {"id": 99, "open_rev": 5, "request": 12},
    }
    assert proto.card_status({**base, "actions": actions}) == {**base, "actions": actions}
    assert proto.card_status({**base, "actions": {"enabled": False, "open": [], "pending": None}})[
        "actions"
    ] == {"enabled": False, "open": [], "pending": None}
    for invalid in (
        {**actions, "enabled": 1},
        {**actions, "open": [{"id": 8, "rev": True, "state": "ready"}]},
        {**actions, "open": [{"id": 8, "rev": 3, "state": []}]},
        {**actions, "open": [{"id": 9, "rev": 3, "state": "ready"}]},
        {**actions, "open": [actions["open"][0], actions["open"][0]]},
        {**actions, "pending": {"id": 99, "open_rev": 0, "request": 12}},
        {**actions, "pending": {"id": 99, "open_rev": 5, "request": True}},
        {"enabled": False, "open": actions["open"], "pending": None},
        {"enabled": False, "open": [], "pending": {"id": 99, "open_rev": 5, "request": 12}},
    ):
        assert proto.card_status({**base, "actions": invalid}) is None


def test_cards_status_preserves_and_validates_optional_deck_readback():
    status = {"count": 3, "overflow": 4, "ids": [9, 8, 7], "capacity": 32}
    deck = {
        "enabled": True,
        "reachable": 2,
        "position": 1,
        "focus_id": 9,
        "next_id": 8,
        "stale": False,
    }

    assert proto.card_status({**status, "deck": deck}) == {**status, "deck": deck}
    assert proto.card_status(status) == status
    assert proto.card_status({**status, "deck": {key: value for key, value in deck.items() if key != "focus_id"}}) is None
    assert proto.card_status({**status, "deck": {**deck, "enabled": 1}}) is None
    assert proto.card_status({**status, "deck": {**deck, "reachable": 33}}) is None
    assert proto.card_status({**status, "deck": {**deck, "reachable": 1, "next_id": 8}}) is None


def test_cards_status_preserves_and_validates_optional_grouped_readback():
    status = {"count": 2, "overflow": 0, "ids": [17, 16], "capacity": 32}
    grouped = {
        "enabled": True,
        "session": 123,
        "group": "notifications",
        "manual": True,
        "generation": 0,
        "present_id": None,
        "remaining_ms": 0,
    }
    assert proto.card_status({**status, "grouped": grouped}) == {**status, "grouped": grouped}
    history_grouped = {**grouped, "history": True}
    assert proto.card_status({**status, "grouped": history_grouped}) == {
        **status, "grouped": history_grouped,
    }

    inactive = {
        "enabled": False,
        "session": 0,
        "group": "home",
        "manual": False,
        "generation": 0,
        "present_id": None,
        "remaining_ms": 0,
    }
    assert proto.card_status({**status, "grouped": inactive}) == {**status, "grouped": inactive}

    active = {**grouped, "manual": False, "generation": 4, "present_id": 17, "remaining_ms": -1}
    assert proto.card_status({**status, "grouped": active}) == {**status, "grouped": active}
    for invalid in (
        {**grouped, "session": True},
        {**grouped, "session": 0},
        {**grouped, "group": "media"},
        {**grouped, "generation": -1},
        {**grouped, "remaining_ms": -2},
        {**grouped, "present_id": 99},
        {**grouped, "manual": True, "present_id": 17},
        {**inactive, "generation": 1},
        {**grouped, "history": 1},
        {**inactive, "history": True},
    ):
        assert proto.card_status({**status, "grouped": invalid}) is None


def test_card_sync_chunks_measure_escaped_utf8_bytes():
    cards = [
        proto.notify(
            nid,
            "\0" * 31,
            "\0" * 63,
            "\0" * 100 + "東京" * 30,
            1,
            5000,
            42,
            body_max_bytes=proto.NOTIFICATION_BODY_LEGACY_BYTES,
        )
        for nid in range(32)
    ]
    snapshot = {
        "rev": 9,
        "bar": {"t": "bar", "rev": 9, "zones": []},
        "clock": None,
        "media": None,
        "notifs": cards,
        "limit": 32,
        "overflow": 4,
    }

    messages = proto.card_sync_messages(snapshot, tx=7)
    chunks = [message for message in messages if message["t"] == "sync_cards"]

    assert messages[0] == {
        "t": "sync_begin",
        "tx": 7,
        "rev": 9,
        "bar": snapshot["bar"],
        "clock": None,
        "media": None,
        "limit": 32,
        "count": 32,
        "overflow": 4,
    }
    assert messages[-1] == {"t": "sync_commit", "tx": 7}
    assert [card for chunk in chunks for card in chunk["notifs"]] == cards
    assert [chunk["start"] for chunk in chunks] == [
        sum(len(previous["notifs"]) for previous in chunks[:index]) for index in range(len(chunks))
    ]
    assert all(len(proto.encode(message)) <= proto.LINE_MAX for message in messages)
    assert all(len(proto.encode(chunk)) <= proto.CARD_CHUNK_MAX for chunk in chunks)


def test_dashboard_is_added_only_to_opted_in_sync_begin():
    snapshot = {
        "rev": 9,
        "bar": {"t": "bar", "rev": 9, "zones": []},
        "clock": None,
        "media": None,
        "dashboard": {
            "cpu": 0.41,
            "mem": 0.52,
            "network": True,
            "rx_bytes_per_s": 1234.5,
            "tx_bytes_per_s": None,
            "battery": {"level": 0.78, "charging": False},
            "volume": {"level": 0.32, "mute": False},
            "bluetooth": 2,
        },
        "notifs": [],
        "limit": 32,
        "overflow": 0,
    }

    old_messages = proto.card_sync_messages(snapshot, tx=1)
    new_messages = proto.card_sync_messages(snapshot, tx=2, include_dashboard=True)

    assert "dashboard" not in old_messages[0]
    assert new_messages[0]["dashboard"] == proto.dashboard_payload(snapshot["dashboard"])
    assert all(len(proto.encode(message)) <= proto.LINE_MAX for message in new_messages)


def test_dashboard_rates_are_optional_bounded_and_reject_bool_values():
    expected = {
        "cpu": None,
        "cpu_freq_mhz": None,
        "mem": None,
        "mem_used_bytes": None,
        "network": None,
        "rx_bytes_per_s": 1_000_000_000_000.0,
        "tx_bytes_per_s": 0.0,
        "battery": None,
        "volume": None,
        "bluetooth": None,
    }
    assert proto.dashboard_payload({
        "rx_bytes_per_s": 1_000_000_000_000,
        "tx_bytes_per_s": 0,
    }) == expected
    for invalid in (True, -1, 1_000_000_000_001, 10**1000, float("inf"), float("nan"), "12"):
        assert proto.dashboard_payload({"rx_bytes_per_s": invalid})["rx_bytes_per_s"] is None


def test_cards_status_accepts_old_responses_and_validates_optional_dashboard_readback():
    base = {"count": 0, "overflow": 0, "ids": [], "capacity": 32}
    dashboard = {
        "cpu": 0.25,
        "mem": 0.75,
        "network": True,
        "rx_bytes_per_s": 1000.5,
        "tx_bytes_per_s": None,
    }

    assert proto.card_status(base) == base
    assert proto.card_status({**base, "dashboard": dashboard}) == {**base, "dashboard": dashboard}
    for field, invalid in (
        ("cpu", True),
        ("cpu", 10**1000),
        ("mem", 1.01),
        ("network", 1),
        ("rx_bytes_per_s", True),
        ("rx_bytes_per_s", -1),
        ("rx_bytes_per_s", 1_000_000_000_001),
        ("tx_bytes_per_s", float("inf")),
        ("tx_bytes_per_s", 10**1000),
        ("tx_bytes_per_s", "unknown"),
    ):
        assert proto.card_status({**base, "dashboard": {**dashboard, field: invalid}}) is None
    assert proto.card_status({**base, "dashboard": {"cpu": 0.5}}) is None


def test_grouped_sync_begin_carries_session_only_when_requested():
    snapshot = {
        "rev": 9,
        "bar": {"t": "bar", "rev": 9, "zones": []},
        "clock": None,
        "media": None,
        "dashboard": proto.dashboard_payload(None),
        "notifs": [],
        "limit": 32,
        "overflow": 0,
    }
    legacy = proto.card_sync_messages(snapshot, tx=1, include_dashboard=True)
    grouped = proto.card_sync_messages(
        snapshot, tx=2, include_dashboard=True, grouped_session=123
    )
    assert "grouped" not in legacy[0]
    assert grouped[0]["grouped"] == {"session": 123}
    assert all(len(proto.encode(message)) <= proto.LINE_MAX for message in grouped)


def test_history_sync_negotiates_exact_metadata_and_old_projection_strips_it():
    card = {
        **proto.notify(17, "app", "sum", "東京" * 200, 1, 1000, 1),
        "history": {"rev": 4, "age_ms": 125, "remaining_ms": 1799875},
    }
    snapshot = {
        "rev": 9,
        "bar": {"t": "bar", "rev": 9, "zones": []},
        "clock": None,
        "media": None,
        "dashboard": proto.dashboard_payload(None),
        "notifs": [card],
        "limit": 32,
        "overflow": 0,
    }
    history_messages = proto.card_sync_messages(
        snapshot, tx=1, include_dashboard=True, grouped_session=123, include_history=True
    )
    history_begin = history_messages[0]
    assert history_begin["grouped"] == {"session": 123, "history": True}
    history_cards = [item for frame in history_messages if frame["t"] == "sync_cards" for item in frame["notifs"]]
    assert history_cards == [card]
    assert len(history_cards[0]["body"].encode("utf-8")) <= proto.NOTIFICATION_BODY_HISTORY_BYTES
    assert all(len(proto.encode(frame)) <= proto.LINE_MAX for frame in history_messages)

    old_messages = proto.card_sync_messages(
        snapshot, tx=2, include_dashboard=True, grouped_session=123
    )
    old_card = next(item for frame in old_messages if frame["t"] == "sync_cards" for item in frame["notifs"])
    assert "history" not in old_card
    assert len(old_card["body"].encode("utf-8")) == proto.NOTIFICATION_BODY_LEGACY_BYTES
    assert old_card["body"].endswith("…")
    assert all(len(proto.encode(frame)) <= proto.LINE_MAX for frame in old_messages)
    for invalid in (
        {"rev": 0, "age_ms": 0, "remaining_ms": 1},
        {"rev": True, "age_ms": 0, "remaining_ms": 1},
        {"rev": 1, "age_ms": -1, "remaining_ms": 1},
        {"rev": 1, "age_ms": 0, "remaining_ms": 0},
        {"rev": 1, "age_ms": 0, "remaining_ms": 1, "extra": 2},
    ):
        malformed = {**snapshot, "notifs": [{**card, "history": invalid}]}
        with pytest.raises(ValueError, match="history sync card"):
            proto.card_sync_messages(
                malformed, tx=3, include_dashboard=True, grouped_session=123, include_history=True
            )
    with pytest.raises(ValueError, match="grouped sync"):
        proto.card_sync_messages(snapshot, tx=4, include_history=True)
    with pytest.raises(ValueError, match="dashboard sync"):
        proto.card_sync_messages(snapshot, tx=5, grouped_session=123, include_history=True)


def test_action_sync_is_opt_in_and_requires_valid_card_open_metadata():
    snapshot = {
        "rev": 9,
        "bar": {"t": "bar", "rev": 9, "zones": []},
        "clock": None,
        "media": None,
        "notifs": [
            {**proto.notify(7, "app", "sum", "body", 1, 1000, 1),
             "open": {"rev": 3, "state": "unavailable"}},
        ],
        "limit": 32,
        "overflow": 0,
    }
    messages = proto.card_sync_messages(
        snapshot, tx=1, grouped_session=4, include_actions=True,
    )
    assert messages[0]["actions"] == {"enabled": True}
    assert messages[1]["notifs"][0]["open"] == {"rev": 3, "state": "unavailable"}
    legacy = proto.card_sync_messages(snapshot, tx=2, grouped_session=4)
    assert "actions" not in legacy[0]

    for invalid in (
        {"rev": 0, "state": "ready"},
        {"rev": True, "state": "ready"},
        {"rev": 3, "state": "disabled"},
        {"rev": 3, "state": []},
    ):
        malformed = {**snapshot, "notifs": [{**snapshot["notifs"][0], "open": invalid}]}
        with pytest.raises(ValueError, match="valid open object"):
            proto.card_sync_messages(malformed, tx=3, grouped_session=4, include_actions=True)
    with pytest.raises(ValueError, match="require grouped sync"):
        proto.card_sync_messages(snapshot, tx=4, include_actions=True)


def test_card_action_and_action_result_builders_reject_malformed_types():
    assert proto.card_action(4, 7, 3, "ready") == {
        "t": "card_action", "session": 4, "id": 7,
        "open": {"rev": 3, "state": "ready"},
    }
    assert proto.action_result(4, 12, 7, 3, 5, "unknown") == {
        "t": "action_result", "session": 4, "boot_id": 12, "id": 7,
        "open_rev": 3, "request": 5, "status": "unknown",
    }
    with pytest.raises(ValueError):
        proto.card_action(True, 7, 3, "ready")
    with pytest.raises(ValueError):
        proto.card_action(4, 7, 3, [])
    with pytest.raises(ValueError):
        proto.action_result(4, True, 7, 3, 5, "unknown")
    with pytest.raises(ValueError):
        proto.action_result(4, 12, 7, 3, 5, [])


@pytest.mark.parametrize(
    ("limit", "notifs", "overflow"),
    [
        (0, [], 3),
        (8, [{"t": "notify", "id": nid} for nid in range(8)], 4),
    ],
)
def test_sync_begin_encodes_effective_cache_limit(limit, notifs, overflow):
    snapshot = {
        "rev": 4,
        "bar": {"t": "bar", "rev": 4, "zones": []},
        "clock": None,
        "media": None,
        "notifs": notifs,
        "limit": limit,
        "overflow": overflow,
    }

    messages = proto.card_sync_messages(snapshot, tx=11)
    begin_line = proto.encode(messages[0])
    begin = json.loads(begin_line[len(proto.PREFIX):])

    assert begin["limit"] == limit
    assert begin["count"] == len(notifs)
    assert begin["overflow"] == overflow
    assert all(len(proto.encode(message)) <= proto.LINE_MAX for message in messages)
    assert all(
        len(proto.encode(message)) <= proto.CARD_CHUNK_MAX
        for message in messages
        if message["t"] == "sync_cards"
    )


def test_notify_uses_supported_glyphs_and_fits_device_buffers():
    message = proto.notify(1, "a" * 30 + "é", "s" * 62 + "é", "b" * 158 + "é", 1, 5000, 42)
    assert message["app"] == "a" * 30 + "e"
    assert message["summary"] == "s" * 62 + "e"
    assert message["body"] == "b" * 158 + "e"
    assert len(proto.encode(message)) < proto.LINE_MAX


def test_notification_body_limit_uses_utf8_bytes_and_marks_truncation():
    text = "東京" * 200
    message = proto.notify(1, "app", "sum", text, 1, -1, 1)
    body = message["body"]
    assert len(body.encode("utf-8")) <= proto.NOTIFICATION_BODY_HISTORY_BYTES
    assert body.endswith("…")
    legacy = proto.notify(
        1, "app", "sum", text, 1, -1, 1,
        body_max_bytes=proto.NOTIFICATION_BODY_LEGACY_BYTES,
    )
    assert len(legacy["body"].encode("utf-8")) <= proto.NOTIFICATION_BODY_LEGACY_BYTES
    assert legacy["body"].endswith("…")


def test_non_latin_text_reaches_device_font_fallback():
    assert proto.display_text("Café 東京 🔋 が") == "Cafe 東京 🔋 が"
    assert proto.display_text("Cafe\u0301") == "Cafe"
    assert proto.display_text("first\nsecond") == "first second"


def test_encode_rejects_a_line_over_device_limit():
    with pytest.raises(ValueError, match="device limit"):
        proto.encode({"t": "text", "v": "x" * proto.LINE_MAX})
