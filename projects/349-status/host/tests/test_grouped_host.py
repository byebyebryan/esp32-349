"""Grouped retained-host lifecycle and serialized PTY acceptance cases."""

import asyncio
import json
import time

from dbus_next import Message

from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.fake import FakeDevice


class _Writer:
    def __init__(self):
        self.frames: list[dict] = []

    def write(self, payload: bytes) -> None:
        line = payload.decode("utf-8").rstrip("\n")
        is_data, message = proto.classify(line)
        assert is_data and message is not None
        self.frames.append(message)

    async def drain(self) -> None:
        return None


async def _wait_for(predicate, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met before timeout")


def _grouped_daemon() -> tuple[Daemon, _Writer]:
    daemon = Daemon(default_config(), asyncio.Event())
    daemon._grouped_enabled = True
    daemon._grouped_mode = True
    daemon._card_sync_capacity = 32
    daemon._dashboard_capable = True
    daemon.grouped_session = 123
    writer = _Writer()
    daemon._writer = writer
    return daemon, writer


def test_grouped_negotiation_requires_all_capabilities_and_syncs_retained_collection():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        sent = []

        async def capture(message):
            sent.append(message)
            return True

        daemon._write_message = capture
        daemon._sample = lambda: {}
        for nid in range(35):
            message = proto.notify(nid + 1, "test", f"card {nid}", "body", 1, -1, nid)
            daemon.model.add_notification(message)
            daemon.model.retain_notification(message)

        hello = {
            "t": "hello",
            "proto": 1,
            "cap": ["link", "bar", "card-sync-v1", "dashboard-v1", "grouped-ui-v1"],
            "cache_cards": 8,
            "boot_id": 31,
        }
        await daemon._on_line("@349 " + json.dumps(hello))
        assert daemon._grouped_enabled
        assert sent[0]["t"] == "sync_begin"
        assert sent[0]["grouped"] == {"session": daemon.grouped_session}
        assert sent[0]["count"] == 8
        assert sent[0]["overflow"] == 24
        assert [card["id"] for item in sent if item["t"] == "sync_cards" for card in item["notifs"]] == list(
            range(28, 36)
        )

        sent.clear()
        incomplete = {**hello, "cap": ["link", "card-sync-v1", "grouped-ui-v1"], "boot_id": 32}
        await daemon._on_line("@349 " + json.dumps(incomplete))
        assert not daemon._grouped_enabled
        assert sent[0]["t"] == "sync_begin"
        assert "grouped" not in sent[0]
        assert "dashboard" not in sent[0]

    asyncio.run(scenario())


def test_normal_coalescing_manual_takeover_and_critical_presentation(monkeypatch):
    async def scenario():
        now = [10.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _grouped_daemon()

        await daemon._device_notify(proto.notify(1, "app", "normal", "body", 1, 1200, 1))
        assert daemon._pending_present_id == 1
        assert daemon._pending_present_due == 10.3
        assert not any(frame["t"] == "present" for frame in writer.frames)

        now[0] = 10.299
        async with daemon._state_lock:
            if daemon._pending_present_due <= now[0]:
                await daemon._publish_pending_presentation_locked(now[0])
        assert not any(frame["t"] == "present" for frame in writer.frames)

        now[0] = 10.3
        async with daemon._state_lock:
            await daemon._publish_pending_presentation_locked(now[0])
        first_present = next(frame for frame in writer.frames if frame["t"] == "present")
        assert first_present == {
            "t": "present",
            "session": 123,
            "generation": 1,
            "id": 1,
            "remaining_ms": 1200,
            "urgency": 1,
        }

        await daemon._handle_input(
            {"t": "input", "action": "browse", "session": 123, "generation": 1, "group": "notifications"}
        )
        assert daemon._manual_notifications
        assert daemon._presentation is None
        assert writer.frames[-1]["t"] == "present" and writer.frames[-1]["remaining_ms"] == 0

        await daemon._device_notify(proto.notify(2, "app", "normal while reading", "body", 1, -1, 2))
        assert daemon._pending_present_id is None
        assert len([frame for frame in writer.frames if frame["t"] == "present"]) == 2

        await daemon._device_notify(proto.notify(3, "app", "critical", "body", 2, -1, 3))
        critical = writer.frames[-1]
        assert critical == {
            "t": "present",
            "session": 123,
            "generation": 2,
            "id": 3,
            "remaining_ms": -1,
            "urgency": 2,
        }
        assert not daemon._manual_notifications

        before = len(writer.frames)
        await daemon._handle_input(
            {"t": "input", "action": "browse", "session": 123, "generation": 1, "group": "home"}
        )
        assert len(writer.frames) == before
        assert daemon._presentation["generation"] == 2

    asyncio.run(scenario())


def test_device_boot_generation_resets_without_rewinding_host_generation():
    async def scenario():
        daemon, writer = _grouped_daemon()
        daemon._device_boot_id = 41

        await daemon._device_notify(proto.notify(1, "app", "first", "body", 2, 0, 1))
        assert daemon._grouped_generation == 1
        assert daemon._device_expected_generation == 1

        await daemon._send_sync()
        assert daemon._device_expected_generation == 1
        await daemon._handle_input(
            {"t": "input", "action": "browse", "session": 123, "generation": 1, "group": "notifications"}
        )
        assert daemon._manual_notifications

        writer.frames.clear()
        hello = {
            "t": "hello",
            "proto": 1,
            "cap": ["link", "bar", "card-sync-v1", "dashboard-v1", "grouped-ui-v1"],
            "cache_cards": 32,
            "boot_id": 42,
        }
        await daemon._on_line("@349 " + json.dumps(hello))
        assert daemon._device_expected_generation == 0
        assert daemon._grouped_generation == 1
        assert daemon._presentation is None
        assert not daemon._manual_notifications
        assert not any(frame["t"] == "present" for frame in writer.frames)

        await daemon._handle_input(
            {"t": "input", "action": "browse", "session": 123, "generation": 0, "group": "notifications"}
        )
        assert daemon._manual_notifications

        await daemon._device_notify(proto.notify(2, "app", "fresh critical", "body", 2, 0, 2))
        assert daemon._grouped_generation == 2
        assert daemon._device_expected_generation == 2
        assert not daemon._manual_notifications

        await daemon._handle_input(
            {"t": "input", "action": "browse", "session": 123, "generation": 0, "group": "home"}
        )
        assert not daemon._manual_notifications
        assert daemon._grouped_group == "notifications"

        await daemon._send_sync()
        assert daemon._device_expected_generation == 2
        await daemon._handle_input(
            {"t": "input", "action": "browse", "session": 123, "generation": 2, "group": "notifications"}
        )
        assert daemon._manual_notifications

    asyncio.run(scenario())


def test_grouped_arrival_expired_before_delivery_is_retained_without_active_resurrection():
    async def scenario(disconnected):
        daemon, writer = _grouped_daemon()
        await daemon.notifications._handle_notify(
            Message(
                destination="org.freedesktop.Notifications",
                path="/org/freedesktop/Notifications",
                interface="org.freedesktop.Notifications",
                member="Notify",
                signature="susssasa{sv}i",
                sender=":1.96",
                serial=1,
                body=["app", 0, "", "short timeout", "body", [], {}, 1],
            )
        )
        local_id = next(iter(daemon.notifications._local_to_daemon))
        daemon.notifications._expiry_deadlines[local_id] = time.monotonic() - 1

        await daemon.notifications._expire_due()
        if disconnected:
            daemon._grouped_enabled = False
            daemon._writer = None
        await daemon._device_notify(daemon.notifications._outbox[local_id])

        assert local_id not in daemon.model.notifs
        assert local_id in daemon.model.retained_notifs
        assert daemon._pending_present_id is None
        assert not any(frame["t"] == "present" for frame in writer.frames)

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_grouped_eviction_bounds_active_projection_during_link_loss():
    async def scenario():
        daemon, _writer = _grouped_daemon()
        daemon._grouped_enabled = False
        daemon._writer = None
        for nid in range(1, 41):
            await daemon._device_notify(proto.notify(nid, "offline", str(nid), "", 1, 0, 0))
        assert list(daemon.model.retained_notifs) == list(range(9, 41))
        assert list(daemon.model.notifs) == list(range(9, 41))
        assert daemon._pending_present_id is None
    asyncio.run(scenario())


def test_grouped_dismiss_accepts_stale_generation_and_replacement_can_reintroduce():
    async def scenario():
        daemon, writer = _grouped_daemon()
        message = proto.notify(7, "app", "original", "body", 1, -1, 7)
        daemon.model.add_notification(message)
        daemon.model.retain_notification(message)
        daemon._grouped_generation = 4
        daemon.notifications._mirrored_local_ids.add(7)
        daemon.notifications._local_to_daemon[7] = 900
        daemon.notifications._daemon_to_local[900] = 7

        await daemon._handle_input(
            {"t": "input", "action": "dismiss", "session": 123, "generation": 0, "id": 7}
        )
        assert 7 not in daemon.model.retained_notifs
        assert daemon.notifications.is_locally_removed(7)
        assert daemon.notifications._daemon_to_local[900] == 7
        assert writer.frames[-1] == {"t": "close", "id": 7, "total": 0, "session": 123}
        sync = daemon.model.card_snapshot(32, retained=True)
        assert sync["notifs"] == []

        from dbus_next import Message, MessageType

        replacement_call = Message(
            destination="org.freedesktop.Notifications",
            path="/org/freedesktop/Notifications",
            interface="org.freedesktop.Notifications",
            member="Notify",
            signature="susssasa{sv}i",
            sender=":1.9",
            serial=9,
            body=["app", 900, "", "replacement", "body", [], {}, -1],
        )
        await daemon.notifications._handle_notify(replacement_call)
        assert not daemon.notifications.is_locally_removed(7)
        await daemon._device_notify(daemon.notifications._outbox[7])
        assert list(daemon.model.retained_notifs) == [7]
        assert daemon.model.retained_notifs[7]["summary"] == "replacement"
        assert writer.frames[-1]["t"] == "notify"
        assert writer.frames[-1]["session"] == 123

    asyncio.run(scenario())


def test_ipc_status_exposes_grouped_presentation_metadata_without_card_text():
    daemon, _writer = _grouped_daemon()
    daemon.model.retain_notification(proto.notify(9, "private app", "private title", "private body", 1, -1, 1))
    daemon._presentation = {
        "id": 9,
        "generation": 4,
        "urgency": 1,
        "deadline": None,
    }

    status = daemon._status()

    assert status["grouped"] is True
    assert status["retained_notifs"] == 1
    assert status["presentation"] == {"session": 123, "generation": 4, "id": 9, "remaining_ms": -1}
    assert "private title" not in repr(status)
    assert "private body" not in repr(status)


def test_grouped_pty_link_serializes_session_presentation_dismiss_and_recovery_sync():
    hello = {
        "t": "hello",
        "proto": 1,
        "cap": ["link", "bar", "card-sync-v1", "dashboard-v1", "grouped-ui-v1"],
        "cache_cards": 32,
        "boot_id": 3401,
    }
    fake = FakeDevice(hello).start()
    try:
        cfg = default_config()
        cfg.link.port = fake.path
        cfg.notifications.mode = "off"
        cfg.daemon.tick_s = 0.05
        cfg.daemon.sync_interval_s = 30.0

        async def scenario():
            stop = asyncio.Event()
            daemon = Daemon(cfg, stop)
            daemon._sample = lambda: {}
            task = asyncio.create_task(daemon.run())
            try:
                await _wait_for(
                    lambda: any(
                        message.get("t") == "sync_begin" and "grouped" in message
                        for message in fake.received
                    )
                )
                assert daemon._status()["grouped"] is True
                first_begin = next(message for message in fake.received if message.get("t") == "sync_begin")
                session = first_begin["grouped"]["session"]
                assert session == daemon.grouped_session

                result = await daemon._ipc_handler(
                    {"cmd": "notify", "summary": "serialized grouped", "body": "body", "expire": 1800}
                )
                local_id = result["id"]
                await _wait_for(
                    lambda: any(
                        message.get("t") == "present" and message.get("id") == local_id
                        for message in fake.received
                    )
                )
                notify = next(
                    message
                    for message in fake.received
                    if message.get("t") == "notify" and message.get("id") == local_id
                )
                present = next(
                    message
                    for message in fake.received
                    if message.get("t") == "present" and message.get("id") == local_id
                )
                assert notify["session"] == session
                assert present["session"] == session
                assert 1 <= present["generation"] <= 0x7FFFFFFF
                assert 0 < present["remaining_ms"] <= 1800
                status = daemon._status()
                assert status["retained_notifs"] == 1
                assert status["presentation"] == {
                    "session": session,
                    "generation": present["generation"],
                    "id": local_id,
                    "remaining_ms": status["presentation"]["remaining_ms"],
                }

                fake.send(
                    {
                        "t": "input",
                        "action": "dismiss",
                        "session": session,
                        "generation": 0,
                        "id": local_id,
                    }
                )
                await _wait_for(
                    lambda: any(
                        message.get("t") == "close"
                        and message.get("id") == local_id
                        and message.get("session") == session
                        for message in fake.received
                    )
                )

                prior_tx = max(
                    (message["tx"] for message in fake.received if message.get("t") == "sync_begin"),
                    default=0,
                )
                await daemon._send_sync()
                await _wait_for(
                    lambda: any(
                        message.get("t") == "sync_commit" and message.get("tx", 0) > prior_tx
                        for message in fake.received
                    )
                )
                latest = [
                    message
                    for message in fake.received
                    if message.get("t") == "sync_begin" and message.get("tx", 0) > prior_tx
                ][-1]
                assert latest["grouped"] == {"session": session}
                assert latest["count"] == 0
                assert latest["overflow"] == 0
                assert daemon._status()["retained_notifs"] == 0
            finally:
                stop.set()
                await asyncio.wait_for(task, 5)

        asyncio.run(scenario())
    finally:
        fake.stop()


def test_grouped_pty_reboot_resets_expected_input_generation_without_replaying_attention():
    hello = {
        "t": "hello",
        "proto": 1,
        "cap": ["link", "bar", "card-sync-v1", "dashboard-v1", "grouped-ui-v1"],
        "cache_cards": 32,
        "boot_id": 4401,
    }
    fake = FakeDevice(hello).start()
    try:
        cfg = default_config()
        cfg.link.port = fake.path
        cfg.notifications.mode = "off"
        cfg.daemon.tick_s = 0.05
        cfg.daemon.sync_interval_s = 30.0

        async def scenario():
            daemon = Daemon(cfg, asyncio.Event())
            daemon._sample = lambda: {}
            task = asyncio.create_task(daemon.run())
            try:
                await _wait_for(lambda: any(message.get("t") == "sync_commit" for message in fake.received))
                await daemon._device_notify(proto.notify(51, "test", "first", "body", 2, 0, 51))
                await _wait_for(
                    lambda: any(message.get("t") == "present" and message.get("generation") == 1 for message in fake.received)
                )
                assert daemon._device_expected_generation == 1

                # An ordinary same-boot sync preserves the device input epoch.
                await daemon._send_sync()
                fake.send(
                    {
                        "t": "input",
                        "action": "browse",
                        "session": daemon.grouped_session,
                        "generation": 1,
                        "group": "notifications",
                    }
                )
                await _wait_for(lambda: daemon._manual_notifications)
                await _wait_for(
                    lambda: any(
                        message.get("t") == "present"
                        and message.get("generation") == 1
                        and message.get("remaining_ms") == 0
                        for message in fake.received
                    )
                )

                present_count = sum(message.get("t") == "present" for message in fake.received)
                last_tx = max(message.get("tx", 0) for message in fake.received if message.get("t") == "sync_commit")
                fake.send({**hello, "boot_id": 4402})
                await _wait_for(lambda: daemon._device_boot_id == 4402 and daemon._device_expected_generation == 0)
                await _wait_for(
                    lambda: any(
                        message.get("t") == "sync_commit" and message.get("tx", 0) > last_tx
                        for message in fake.received
                    )
                )
                assert daemon._presentation is None
                assert not daemon._manual_notifications
                assert sum(message.get("t") == "present" for message in fake.received) == present_count

                fake.send(
                    {
                        "t": "input",
                        "action": "browse",
                        "session": daemon.grouped_session,
                        "generation": 0,
                        "group": "notifications",
                    }
                )
                await _wait_for(lambda: daemon._manual_notifications)

                await daemon._device_notify(proto.notify(52, "test", "fresh critical", "body", 2, 0, 52))
                await _wait_for(
                    lambda: any(message.get("t") == "present" and message.get("generation") == 2 for message in fake.received)
                )
                assert daemon._device_expected_generation == 2

                fake.send(
                    {
                        "t": "input",
                        "action": "browse",
                        "session": daemon.grouped_session,
                        "generation": 0,
                        "group": "home",
                    }
                )
                await asyncio.sleep(0.1)
                assert not daemon._manual_notifications
                assert daemon._grouped_group == "notifications"
            finally:
                daemon.stop.set()
                await asyncio.wait_for(task, 5)

        asyncio.run(scenario())
    finally:
        fake.stop()
