"""Startup bounds and legacy close ownership across retained-history eviction."""

import asyncio
import json

from dbus_next import Message, MessageType

from status349.config import default_config
from status349.daemon import Daemon
from status349.sources import notifications
from status349.sources.notifications import (
    ASSOCIATION_LIMIT,
    NOTIFICATIONS_NAME,
    NOTIFICATIONS_PATH,
    parse_open_metadata,
)


def _daemon():
    daemon = Daemon(default_config(), asyncio.Event())
    daemon._sample = lambda: {}

    async def discard(_message):
        return True

    daemon._write_message = discard
    return daemon


async def _notify(daemon, serial, *, replaces=0, expire=0, deliver=True):
    source = daemon.notifications
    await source._handle_notify(Message(
        destination=NOTIFICATIONS_NAME,
        path=NOTIFICATIONS_PATH,
        interface=NOTIFICATIONS_NAME,
        member="Notify",
        signature="susssasa{sv}i",
        sender=":1.400",
        serial=serial,
        body=["test-app", replaces, "", f"card {serial}", "body", [], {}, expire],
    ))
    desktop_id = replaces or serial
    await source._handle(Message(
        message_type=MessageType.METHOD_RETURN,
        sender=NOTIFICATIONS_NAME,
        destination=":1.400",
        reply_serial=serial,
        signature="u",
        body=[desktop_id],
    ))
    local_id = source._daemon_to_local[desktop_id]
    if deliver:
        await daemon._device_notify(source._outbox.pop(local_id))
    return local_id


async def _legacy_hello(daemon):
    await daemon._on_line("@349 " + json.dumps({
        "t": "hello", "proto": 1, "fw": "legacy", "cap": ["link", "bar"],
    }))


def test_startup_burst_bounds_source_metadata_without_a_connected_display():
    async def scenario():
        daemon = _daemon()
        source = daemon.notifications
        for serial in range(1, 1001):
            await _notify(daemon, serial, deliver=False)

        assert not daemon._notification_peer_known
        assert len(source._local_to_daemon) == ASSOCIATION_LIMIT
        assert len(source._daemon_to_local) == ASSOCIATION_LIMIT
        assert len(source._mirrored_local_ids) == ASSOCIATION_LIMIT
        assert len(source._outbox) == ASSOCIATION_LIMIT
        assert len(source._action_daemon_id) == ASSOCIATION_LIMIT
        assert len(source._open_info) == ASSOCIATION_LIMIT
        assert not source._by_serial
        assert not source._open_reply_versions
        assert set(source._daemon_to_local) == set(range(969, 1001))

    asyncio.run(scenario())


def test_startup_delivery_bounds_active_and_retained_cards():
    async def scenario():
        daemon = _daemon()
        ids = [await _notify(daemon, serial) for serial in range(1, 101)]
        assert list(daemon.model.notifs) == ids[-ASSOCIATION_LIMIT:]
        assert list(daemon.model.retained_notifs) == ids[-ASSOCIATION_LIMIT:]
        assert set(daemon.notifications._local_to_daemon) == set(ids[-ASSOCIATION_LIMIT:])

    asyncio.run(scenario())


def test_negotiated_legacy_card_keeps_close_tracking_after_retained_eviction():
    async def scenario():
        daemon = _daemon()
        await _legacy_hello(daemon)
        ids = [await _notify(daemon, serial) for serial in range(1, 101)]
        assert len(daemon.model.notifs) == 100
        assert len(daemon.model.retained_notifs) == ASSOCIATION_LIMIT
        assert len(daemon.notifications._local_to_daemon) == 100
        assert ids[0] not in daemon.model.retained_notifs

        await daemon.notifications._handle(Message(
            message_type=MessageType.SIGNAL,
            sender=NOTIFICATIONS_NAME,
            path=NOTIFICATIONS_PATH,
            interface=NOTIFICATIONS_NAME,
            member="NotificationClosed",
            signature="uu",
            body=[1, 2],
        ))
        assert ids[0] not in daemon.model.notifs
        assert ids[0] not in daemon.notifications._local_to_daemon
        assert len(daemon.model.notifs) == 99
        assert daemon.model.snapshot()["notifs_overflow"] == 96

    asyncio.run(scenario())


def test_negotiated_legacy_eviction_preserves_popup_and_injected_expiry():
    async def scenario():
        daemon = _daemon()
        await _legacy_hello(daemon)
        first = await _notify(daemon, 1, expire=1000)
        daemon.notifications._expiry_deadlines[first] = 0
        for serial in range(2, 34):
            await _notify(daemon, serial)
        assert first not in daemon.model.retained_notifs
        await daemon.notifications._expire_due()
        assert first not in daemon.model.notifs

        injected = await daemon._ipc_handler({"cmd": "notify", "summary": "injected"})
        injected_id = injected["id"]
        for serial in range(34, 67):
            await _notify(daemon, serial)
        assert injected_id not in daemon.model.retained_notifs
        assert injected_id in daemon.model.notifs
        assert injected_id in daemon._injected_expiry

    asyncio.run(scenario())


def test_bounded_associations_keep_a_recent_replacement():
    async def scenario():
        daemon = _daemon()
        ids = [await _notify(daemon, serial) for serial in range(1, 33)]
        assert await _notify(daemon, 33, replaces=1) == ids[0]
        await _notify(daemon, 34)
        assert ids[0] in daemon.notifications._local_to_daemon
        assert ids[0] in daemon.model.retained_notifs
        assert ids[1] not in daemon.notifications._local_to_daemon
        assert ids[1] not in daemon.model.retained_notifs
        assert len(daemon.model.notifs) == ASSOCIATION_LIMIT

    asyncio.run(scenario())


def test_oversized_raw_action_identity_is_rejected_before_serialization(monkeypatch):
    def unexpected_serialize(*args, **kwargs):
        raise AssertionError("oversized action identity reached JSON serialization")

    monkeypatch.setattr(notifications.json, "dumps", unexpected_serialize)
    assert parse_open_metadata([
        "app", 0, "", "summary", "x" * 1_000_000, ["default", "Open"], {}, 0,
    ]) is None


def test_ignored_body_is_skipped_before_conversion(monkeypatch):
    def unexpected_conversion(*args, **kwargs):
        raise AssertionError("ignored body reached display conversion")

    monkeypatch.setattr(notifications, "convert_body", unexpected_conversion)

    async def scenario():
        daemon = _daemon()
        await daemon.notifications._handle_notify(Message(
            destination=NOTIFICATIONS_NAME,
            path=NOTIFICATIONS_PATH,
            interface=NOTIFICATIONS_NAME,
            member="Notify",
            signature="susssasa{sv}i",
            body=["KeePassXC", 0, "", "summary", "x" * 1_000_000, [], {}, 0],
        ))
        assert not daemon.notifications._outbox
        assert not daemon.notifications._local_to_daemon

    asyncio.run(scenario())


def test_oversized_app_with_ignore_filters_is_suppressed_without_logging_raw_name(monkeypatch):
    app = "KeePassXC" + " " * 1_000_000
    assert notifications.is_ignored(app, ["KeePassXC"])
    debug_messages = []

    def record_debug(*args, **kwargs):
        debug_messages.append(args)

    def unexpected_conversion(*args, **kwargs):
        raise AssertionError("oversized filtered app reached display conversion")

    monkeypatch.setattr(notifications.log, "debug", record_debug)
    monkeypatch.setattr(notifications, "convert_body", unexpected_conversion)

    async def scenario():
        daemon = _daemon()
        await daemon.notifications._handle_notify(Message(
            destination=notifications.NOTIFICATIONS_NAME,
            path=notifications.NOTIFICATIONS_PATH,
            interface=notifications.NOTIFICATIONS_NAME,
            member="Notify",
            signature="susssasa{sv}i",
            sender=":1.401",
            serial=1,
            body=[app, 0, "", "summary", "body", [], {}, 0],
        ))
        assert not daemon.notifications._outbox
        assert not daemon.notifications._local_to_daemon

    asyncio.run(scenario())

    assert debug_messages == [("ignoring notification with oversized app identifier",)]


def test_huge_notification_labels_use_bounded_source_and_wire_projections(monkeypatch):
    huge = "x" * 8_000_000
    conversion_inputs = []
    raw_action_inputs = []
    convert = notifications.convert_body
    parse_open = notifications.parse_open_metadata

    def record_conversion(app, summary, body, hints):
        conversion_inputs.append((app, summary))
        return convert(app, summary, body, hints)

    def record_raw_open_metadata(body):
        raw_action_inputs.append((body[0], body[3]))
        return parse_open(body)

    monkeypatch.setattr(notifications, "convert_body", record_conversion)
    monkeypatch.setattr(notifications, "parse_open_metadata", record_raw_open_metadata)

    async def scenario():
        daemon = _daemon()
        daemon.cfg.notifications.ignore_apps = []
        await daemon.notifications._handle_notify(Message(
            destination=notifications.NOTIFICATIONS_NAME,
            path=notifications.NOTIFICATIONS_PATH,
            interface=notifications.NOTIFICATIONS_NAME,
            member="Notify",
            signature="susssasa{sv}i",
            sender=":1.402",
            serial=2,
            body=[huge, 0, "", huge, "body", ["default", "Open"], {}, 0],
        ))

        assert conversion_inputs == [("oversized-notification-source", "")]
        assert len(daemon.notifications._outbox) == 1
        local_id, projected = next(iter(daemon.notifications._outbox.items()))
        assert projected["app"] == "x" * 31
        assert projected["summary"] == "x" * 63
        assert daemon.notifications._open_info[local_id]["expected"] is None

    asyncio.run(scenario())

    assert len(raw_action_inputs) == 1
    assert raw_action_inputs[0][0] is huge
    assert raw_action_inputs[0][1] is huge
