import asyncio
import json

import pytest
from dbus_next import Message, MessageType

from status349 import proto
from status349.actions import ActionEntry
from status349.config import default_config
from status349.daemon import Daemon
from status349.sources import notifications as source_module
from status349.sources.notifications import NotificationSource


class _Writer:
    def __init__(self):
        self.frames: list[dict] = []

    def write(self, payload: bytes) -> None:
        is_data, message = proto.classify(payload.decode("utf-8").rstrip("\n"))
        assert is_data and message is not None
        self.frames.append(message)

    async def drain(self) -> None:
        return None


def _history_daemon(retention_s: int = 5) -> tuple[Daemon, _Writer]:
    cfg = default_config()
    cfg.notifications.retention_s = retention_s
    daemon = Daemon(cfg, asyncio.Event())
    daemon._grouped_enabled = True
    daemon._grouped_mode = True
    daemon._history_enabled = True
    daemon._history_mode = True
    daemon._card_sync_capacity = 32
    daemon._dashboard_capable = True
    daemon.grouped_session = 123
    writer = _Writer()
    daemon._writer = writer
    daemon._device_boot_id = 1
    daemon._sample = lambda: {}
    return daemon, writer


def _history_hello(boot_id: int = 2) -> dict:
    return {
        "t": "hello",
        "proto": 1,
        "cap": [
            "link", "bar", "card-sync-v1", "dashboard-v1", "grouped-ui-v1",
            "notification-history-v1",
        ],
        "cache_cards": 32,
        "boot_id": boot_id,
    }


def test_history_ttl_is_independent_of_persistent_critical_popup(monkeypatch):
    async def scenario():
        now = [100.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _history_daemon(retention_s=5)
        await daemon._device_notify(proto.notify(1, "app", "persistent", "body", 2, 0, 1))
        daemon.notifications._local_to_daemon[1] = 901
        daemon.notifications._daemon_to_local[901] = 1
        daemon.notifications._action_daemon_id[1] = 901
        daemon.action_manager.entries[1] = ActionEntry()

        first = next(frame for frame in writer.frames if frame["t"] == "notify")
        assert first["history"] == {"rev": 1, "age_ms": 0, "remaining_ms": 5000}
        assert daemon._presentation is not None and daemon._presentation["deadline"] is None

        now[0] = 102.0
        await daemon._device_expire(1)
        assert 1 not in daemon.model.notifs
        assert 1 in daemon.model.retained_notifs

        now[0] = 104.999
        async with daemon._state_lock:
            assert await daemon._expire_history_locked(now[0]) == []
        now[0] = 105.0
        async with daemon._state_lock:
            assert await daemon._expire_history_locked(now[0]) == [1]
        assert 1 not in daemon.model.retained_notifs
        assert 1 not in daemon.model.retained_received_mono
        assert 1 not in daemon.notifications._local_to_daemon
        assert 901 not in daemon.notifications._daemon_to_local
        await daemon.action_manager._refresh_candidates()
        assert 1 not in daemon.action_manager.entries
        assert any(frame["t"] == "close" and frame["id"] == 1 for frame in writer.frames)

    asyncio.run(scenario())


def test_sync_and_replug_serialize_age_without_renewal(monkeypatch):
    async def scenario():
        now = [200.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _history_daemon(retention_s=10)
        await daemon._device_notify(proto.notify(7, "app", "kept", "body", 1, 0, 1))
        first_revision = daemon.model.retained_history_rev[7]
        first_receipt = daemon.model.retained_received_mono[7]

        now[0] = 203.25
        await daemon._send_sync()
        sync_card = next(
            card for frame in writer.frames if frame["t"] == "sync_cards" for card in frame["notifs"]
        )
        assert sync_card["history"] == {
            "rev": first_revision, "age_ms": 3250, "remaining_ms": 6750,
        }

        # A new board boot renegotiates history and receives its remaining age.
        writer.frames.clear()
        daemon._history_enabled = False
        daemon._grouped_enabled = False
        await daemon._on_line("@349 " + json.dumps(_history_hello(boot_id=2)))
        replug_card = next(
            card for frame in writer.frames if frame["t"] == "sync_cards" for card in frame["notifs"]
        )
        assert replug_card["history"] == sync_card["history"]
        assert daemon.model.retained_received_mono[7] == first_receipt
        assert daemon.model.retained_history_rev[7] == first_revision

    asyncio.run(scenario())


def test_reload_shortens_history_without_renewing_existing_cards(tmp_path, monkeypatch):
    async def scenario():
        now = [100.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _history_daemon(retention_s=1800)
        daemon.cfg.notifications.mode = "off"
        cfg_path = tmp_path / "349d.toml"
        cfg_path.write_text('[notifications]\nmode = "off"\nretention_s = 600\n')
        daemon.cfg_path = str(cfg_path)

        await daemon._device_notify(proto.notify(1, "app", "older", "body", 1, 0, 1))
        now[0] = 700.0
        await daemon._device_notify(proto.notify(2, "app", "recent", "body", 1, 0, 2))
        recent_revision = daemon.model.retained_history_rev[2]
        now[0] = 800.0
        writer.frames.clear()

        assert await daemon.reload()
        assert daemon.model.retention_s == 600
        assert list(daemon.model.retained_notifs) == [2]
        assert any(frame["t"] == "close" and frame["id"] == 1 for frame in writer.frames)
        assert daemon._needs_sync and daemon._tick_wakeup.is_set()
        await daemon._send_sync()
        card = next(
            card for frame in writer.frames if frame["t"] == "sync_cards" for card in frame["notifs"]
        )
        assert card["id"] == 2
        assert card["history"] == {
            "rev": recent_revision, "age_ms": 100000, "remaining_ms": 500000,
        }
        assert daemon.model.retained_received_mono[2] == 700.0

        now[0] = 1300.0
        async with daemon._state_lock:
            assert await daemon._expire_history_locked(now[0]) == [2]
        assert not daemon.model.retained_notifs

    asyncio.run(scenario())


def test_identical_accepted_notification_replacement_renews_but_sync_does_not(monkeypatch):
    async def scenario():
        now = [300.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _history_daemon(retention_s=10)
        original = proto.notify(9, "app", "same", "same body", 1, 0, 1)
        await daemon._device_notify(original)
        revision = daemon.model.retained_history_rev[9]

        now[0] = 304.0
        await daemon._send_sync()
        assert daemon.model.retained_history_rev[9] == revision
        assert daemon.model.retained_received_mono[9] == 300.0

        await daemon._device_notify(dict(original))
        assert daemon.model.retained_history_rev[9] > revision
        assert daemon.model.retained_received_mono[9] == 304.0
        latest = [frame for frame in writer.frames if frame["t"] == "notify"][-1]
        assert latest["history"] == {
            "rev": daemon.model.retained_history_rev[9], "age_ms": 0, "remaining_ms": 10000,
        }

    asyncio.run(scenario())


def test_replacement_excluded_from_batch_expiry_keeps_its_source_and_history_state(monkeypatch):
    async def scenario():
        now = [350.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, _writer = _history_daemon(retention_s=5)
        await daemon._device_notify(proto.notify(1, "app", "replace", "body", 1, 0, 1))
        await daemon._device_notify(proto.notify(2, "app", "expire", "body", 1, 0, 2))
        daemon.notifications._local_to_daemon[1] = 501
        daemon.notifications._daemon_to_local[501] = 1
        daemon.notifications._local_to_daemon[2] = 502
        daemon.notifications._daemon_to_local[502] = 2
        now[0] = 355.0

        await daemon._device_notify(proto.notify(1, "app", "replacement", "new body", 1, 0, 3))

        assert list(daemon.model.retained_notifs) == [1]
        assert daemon.model.retained_received_mono[1] == 355.0
        assert daemon.notifications._local_to_daemon[1] == 501
        assert daemon.notifications._daemon_to_local[501] == 1
        assert 2 not in daemon.notifications._local_to_daemon
        assert 502 not in daemon.notifications._daemon_to_local

    asyncio.run(scenario())


def test_manual_browsing_blocks_focus_until_history_takeover(monkeypatch):
    async def scenario():
        now = [400.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _history_daemon()
        await daemon._device_notify(proto.notify(1, "app", "first", "body", 1, 0, 1))
        await daemon._handle_input({
            "t": "input", "action": "browse", "session": 123, "generation": 0,
            "group": "notifications",
        })
        assert daemon._manual_notifications

        presentations = sum(frame["t"] == "present" for frame in writer.frames)
        await daemon._device_notify(proto.notify(2, "app", "while browsing", "body", 2, 0, 2))
        assert sum(frame["t"] == "present" for frame in writer.frames) == presentations

        await daemon._handle_input({
            "t": "input", "action": "browse", "session": 123,
            "generation": daemon._device_expected_generation,
            "group": "notifications", "manual": False,
        })
        assert not daemon._manual_notifications
        await daemon._device_notify(proto.notify(3, "app", "after takeover", "body", 2, 0, 3))
        assert any(frame["t"] == "present" and frame["id"] == 3 for frame in writer.frames)

        # New takeover input is rejected by old grouped peers and by malformed values.
        daemon._history_enabled = False
        daemon._manual_notifications = True
        before = daemon._manual_notifications
        await daemon._handle_input({
            "t": "input", "action": "browse", "session": 123,
            "generation": daemon._device_expected_generation,
            "group": "notifications", "manual": False,
        })
        assert daemon._manual_notifications is before
        daemon._history_enabled = True
        for invalid in (0, 1, None, "false"):
            await daemon._handle_input({
                "t": "input", "action": "browse", "session": 123,
                "generation": daemon._device_expected_generation,
                "group": "notifications", "manual": invalid,
            })
            assert daemon._manual_notifications is True

    asyncio.run(scenario())


@pytest.mark.parametrize("removal", ["device_close", "dismiss", "expiry"])
def test_empty_history_releases_manual_focus_suppression(removal, monkeypatch):
    async def scenario():
        now = [600.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _history_daemon(retention_s=5)
        await daemon._device_notify(proto.notify(1, "app", "last", "body", 1, 0, 1))
        await daemon._handle_input({
            "t": "input", "action": "browse", "session": 123,
            "generation": daemon._device_expected_generation,
            "group": "notifications",
        })
        assert daemon._manual_notifications

        if removal == "device_close":
            await daemon._device_close(1)
        elif removal == "dismiss":
            await daemon._handle_input({
                "t": "input", "action": "dismiss", "session": 123,
                "generation": daemon._device_expected_generation, "id": 1,
            })
        else:
            now[0] = 605.0
            async with daemon._state_lock:
                assert await daemon._expire_history_locked(now[0]) == [1]

        assert not daemon.model.retained_notifs
        assert daemon._grouped_group == "notifications"
        assert not daemon._manual_notifications

        for local_id in (2, 3):
            await daemon._device_notify(
                proto.notify(local_id, "app", f"arrival {local_id}", "body", 2, 0, local_id)
            )

        presented_ids = [frame["id"] for frame in writer.frames if frame["t"] == "present"]
        assert presented_ids[-2:] == [2, 3]

    asyncio.run(scenario())


def test_capacity_eviction_releases_source_and_action_metadata():
    async def scenario():
        daemon, _writer = _history_daemon()
        source = daemon.notifications
        source._mirrored_local_ids.add(1)
        source._local_to_daemon[1] = 501
        source._daemon_to_local[501] = 1
        source._action_daemon_id[1] = 501
        source._open_info[1] = {"version": 1, "expected": None, "confirmed": False, "owner": None}
        daemon.action_manager.entries[1] = ActionEntry()

        for local_id in range(1, 34):
            await daemon._device_notify(
                proto.notify(local_id, "app", str(local_id), "body", 0, 0, local_id)
            )

        assert list(daemon.model.retained_notifs) == list(range(2, 34))
        assert 1 not in source._mirrored_local_ids
        assert 1 not in source._local_to_daemon
        assert 501 not in source._daemon_to_local
        assert 1 not in source._action_daemon_id
        assert source.actions_changed.is_set()
        await daemon.action_manager._refresh_candidates()
        assert 1 not in daemon.action_manager.entries

    asyncio.run(scenario())


def test_action_input_expires_due_history_before_acceptance(monkeypatch):
    async def scenario():
        now = [500.0]
        monkeypatch.setattr("status349.daemon.time.monotonic", lambda: now[0])
        daemon, writer = _history_daemon(retention_s=2)
        await daemon._device_notify(proto.notify(4, "app", "old", "body", 1, 0, 1))
        daemon._actions_negotiated = True
        daemon._device_boot_id = daemon._action_ledger_boot_id = 8
        now[0] = 502.0

        await daemon._handle_input({
            "t": "input", "action": "activate", "session": 123, "boot_id": 8,
            "id": 4, "open_rev": 1, "request": 1,
        })

        assert 4 not in daemon.model.retained_notifs
        assert daemon._action_results[1][1] == "stale"
        assert any(frame["t"] == "action_result" and frame["status"] == "stale" for frame in writer.frames)

    asyncio.run(scenario())


def test_source_retains_511_utf8_bytes_and_stamps_identical_replacements(monkeypatch):
    async def scenario():
        now = [10.0]
        monkeypatch.setattr("status349.sources.notifications.time.monotonic", lambda: now[0])
        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            lambda _local_id: asyncio.sleep(0),
        )
        body = "東京" * 300
        call = Message(
            destination="org.freedesktop.Notifications",
            path="/org/freedesktop/Notifications",
            interface="org.freedesktop.Notifications",
            member="Notify",
            signature="susssasa{sv}i",
            sender=":1.55",
            serial=1,
            body=["app", 0, "", "summary", body, [], {}, 0],
        )
        parsed = source_module.parse_notify_body(call.body)
        assert len(parsed["body"].encode("utf-8")) <= 511
        assert parsed["body"].endswith("…")

        await source._handle_notify(call)
        local_id = next(iter(source._outbox))
        first = source._outbox[local_id]
        first_receipt = first["_received_mono"]
        await source._handle(Message(
            message_type=MessageType.METHOD_RETURN,
            sender="org.freedesktop.Notifications",
            destination=":1.55",
            reply_serial=1,
            signature="u",
            body=[900],
        ))

        now[0] = 12.0
        repeated = Message(
            destination="org.freedesktop.Notifications",
            path="/org/freedesktop/Notifications",
            interface="org.freedesktop.Notifications",
            member="Notify",
            signature="susssasa{sv}i",
            sender=":1.55",
            serial=2,
            body=["app", 900, "", "summary", body, [], {}, 0],
        )
        await source._handle_notify(repeated)
        replacement = source._outbox[local_id]
        assert replacement["body"] == first["body"]
        assert replacement["_received_mono"] == 12.0
        assert replacement["_received_mono"] > first_receipt

    asyncio.run(scenario())
