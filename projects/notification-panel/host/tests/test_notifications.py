import asyncio
import os
import time

import pytest
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.fake import FakeDevice
from status349.sources.notifications import (
    ASSOCIATION_LIMIT,
    DISMISS_QUEUE_LIMIT,
    MONITOR_QUEUE_LIMIT,
    NOTIFY_OUTBOX_LIMIT,
    PENDING_REPLY_LIMIT,
    NOTIFICATIONS_NAME,
    NOTIFICATIONS_PATH,
    NotificationSource,
    effective_popup_timeout_ms,
    is_ignored,
    parse_closed_body,
    parse_notify_body,
)

HAVE_SESSION_BUS = bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


def test_parse_notify_body():
    body = ["app", 0, "", "sum", "body", [], {"urgency": Variant("y", 2)}, 5000]
    assert parse_notify_body(body) == {
        "app": "app",
        "replaces": 0,
        "summary": "sum",
        "body": "body",
        "urgency": 2,
        "expire": 5000,
    }


def test_parse_notify_body_short():
    assert parse_notify_body(["app"]) is None


def test_parse_notify_body_defaults_urgency():
    body = ["app", 0, "", "sum", "body", [], {}, -1]
    assert parse_notify_body(body)["urgency"] == 1


def test_parse_closed_body():
    assert parse_closed_body([7, 3]) == (7, 3)
    assert parse_closed_body([]) is None


def test_is_ignored():
    assert is_ignored("KeePassXC", ["keepassxc"])
    assert not is_ignored("Firefox", ["keepassxc"])


def test_popup_timeout_uses_app_request_or_server_default():
    cfg = default_config().notifications
    assert effective_popup_timeout_ms(1200, 1, cfg) == 1200
    assert effective_popup_timeout_ms(-1, 1, cfg) == 10000
    assert effective_popup_timeout_ms(-1, 2, cfg) == 0
    assert effective_popup_timeout_ms(0, 1, cfg) == 0


def _notify_call(sender: str, serial: int, replaces_id: int, summary: str, expire: int = 5000) -> Message:
    return Message(
        destination=NOTIFICATIONS_NAME,
        path=NOTIFICATIONS_PATH,
        interface=NOTIFICATIONS_NAME,
        member="Notify",
        signature="susssasa{sv}i",
        sender=sender,
        serial=serial,
        body=["test-app", replaces_id, "", summary, "body", [], {}, expire],
    )


def _notify_reply(destination: str, serial: int, daemon_id: int) -> Message:
    return Message(
        message_type=MessageType.METHOD_RETURN,
        sender=":1.40",
        destination=destination,
        reply_serial=serial,
        signature="u",
        body=[daemon_id],
    )


def _ready_identity(source: NotificationSource, owner: str) -> None:
    source._server_owner = owner
    source._server_pid = 1
    source._identity_state = "ready"
    source._identity_last_error = None


def _ready_action_info(source: NotificationSource, local_id: int, version: int) -> None:
    generation = source._owner_change_generation
    owner = source._server_owner
    source._notification_owner_generations[local_id] = generation
    source._open_info[local_id] = {
        "version": version, "owner": owner, "owner_generation": generation,
        "reply_owner": owner, "reply_generation": generation, "identity_pending": False,
    }


def _closed_signal(daemon_id: int) -> Message:
    return _closed_signal_reason(daemon_id, 2)


def _closed_signal_reason(daemon_id: int, reason: int) -> Message:
    return Message(
        message_type=MessageType.SIGNAL,
        sender=NOTIFICATIONS_NAME,
        path=NOTIFICATIONS_PATH,
        interface=NOTIFICATIONS_NAME,
        member="NotificationClosed",
        signature="uu",
        body=[daemon_id, reason],
    )


def test_notify_reply_correlation_uses_client_sender_and_returned_id():
    async def scenario():
        closed = []

        async def noop(_message):
            pass

        async def on_close(local_id):
            closed.append(local_id)

        source = NotificationSource(default_config().notifications, noop, on_close)
        await source._handle_notify(_notify_call(":1.40", 7, 0, "first"))
        await source._handle_notify(_notify_call(":1.41", 7, 0, "second"))
        await source._handle(_notify_reply(":1.41", 7, 222))
        await source._handle(_notify_reply(":1.40", 7, 111))

        assert source._daemon_to_local == {111: 1, 222: 2}
        await source._handle(_closed_signal(222))
        assert closed == [2]
        assert 111 in source._daemon_to_local
        assert 2 not in source._expiry_deadlines

    asyncio.run(scenario())


def test_unknown_replaces_id_is_replaced_by_notify_reply_id():
    async def scenario():
        closed = []

        async def noop(_message):
            pass

        async def on_close(local_id):
            closed.append(local_id)

        source = NotificationSource(default_config().notifications, noop, on_close)
        await source._handle_notify(_notify_call(":1.50", 9, 777, "replacement"))
        assert source._daemon_to_local == {777: 1}
        await source._handle(_notify_reply(":1.50", 9, 888))
        assert source._daemon_to_local == {888: 1}
        assert source._local_to_daemon == {1: 888}
        await source._handle(_closed_signal(777))
        assert closed == []
        await source._handle(_closed_signal(888))
        assert closed == [1]

    asyncio.run(scenario())


def test_late_notify_reply_after_close_cannot_restore_mapping():
    async def scenario():
        closed = []

        async def noop(_message):
            pass

        async def on_close(local_id):
            closed.append(local_id)

        source = NotificationSource(default_config().notifications, noop, on_close)
        await source._handle_notify(_notify_call(":1.55", 10, 777, "closes before reply"))
        await source._handle(_closed_signal(777))
        await source._handle(_notify_reply(":1.55", 10, 888))

        assert closed == [1]
        assert source._daemon_to_local == {}
        assert source._local_to_daemon == {}
        assert source._by_serial == {}
        assert source._outbox == {}

        # A later request can still establish a fresh mapping for the same
        # daemon ID after the closed request's late reply has been discarded.
        await source._handle_notify(_notify_call(":1.55", 11, 888, "new card"))
        await source._handle(_notify_reply(":1.55", 11, 888))
        assert source._daemon_to_local == {888: 2}

    asyncio.run(scenario())


def test_grouped_reason_one_completes_attention_but_explicit_close_removes_record():
    async def scenario():
        closed = []
        expired = []

        async def on_close(local_id):
            closed.append(local_id)

        async def on_expire(local_id):
            expired.append(local_id)

        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            on_close,
            on_expire=on_expire,
            grouped_mode=lambda: True,
        )
        await source._handle_notify(_notify_call(":1.90", 1, 0, "first"))
        await source._handle(_notify_reply(":1.90", 1, 777))
        await source._handle(_closed_signal_reason(777, 1))

        assert expired == [1]
        assert closed == []
        assert source._daemon_to_local == {777: 1}
        assert source._local_to_daemon == {1: 777}
        assert source._mirrored_local_ids == {1}

        await source._handle(_closed_signal_reason(777, 3))
        assert closed == [1]
        assert source._daemon_to_local == {}
        assert source._local_to_daemon == {}

    asyncio.run(scenario())


def test_grouped_reason_one_disables_propagation_and_reused_id_belongs_to_new_card():
    async def scenario():
        class Control:
            def __init__(self):
                self.closed = []

            async def call(self, message):
                self.closed.append(message.body[0])

        async def noop(_local_id):
            pass

        cfg = default_config().notifications
        cfg.device_dismiss = "propagate"
        source = NotificationSource(
            cfg,
            lambda _message: asyncio.sleep(0),
            noop,
            grouped_mode=lambda: True,
        )
        control = Control()
        source._control = control
        _ready_identity(source, ":1.40")

        await source._handle_notify(_notify_call(":1.93", 1, 0, "closed card"))
        await source._handle(_notify_reply(":1.93", 1, 777))
        await source._handle(_closed_signal_reason(777, 1))
        await source.dismiss(1)
        assert control.closed == []

        # A fresh Notify reply reusing a desktop ID transfers its action
        # association. The archived display ID remains inert.
        await source._handle_notify(_notify_call(":1.93", 2, 0, "new card"))
        await source._handle(_notify_reply(":1.93", 2, 777))
        await source.dismiss(1)
        await source.dismiss(2)
        await source._dismiss_queue.join()
        assert control.closed == [777]
        assert source._daemon_to_local == {777: 2}
        assert source._action_daemon_id == {2: 777}
        await source.stop()

    asyncio.run(scenario())


def test_propagated_dismiss_does_not_hold_serial_reader_or_delay_local_close():
    async def scenario():
        cfg = default_config()
        cfg.notifications.device_dismiss = "propagate"
        daemon = Daemon(cfg, asyncio.Event())
        daemon._grouped_enabled = True
        daemon._grouped_mode = True
        daemon._card_sync_capacity = 32
        local_id, daemon_id = 7, 777
        daemon.model.retain_notification(proto.notify(local_id, "app", "summary", "body", 1, -1, 1))

        source = daemon.notifications
        entered = asyncio.Event()
        release = asyncio.Event()

        class Control:
            async def call(self, _message):
                entered.set()
                await release.wait()

        source._control = Control()
        _ready_identity(source, ":1.95")
        source._daemon_to_local = {daemon_id: local_id}
        source._local_to_daemon = {local_id: daemon_id}
        source._action_daemon_id = {local_id: daemon_id}
        _ready_action_info(source, local_id, 3)
        source._mirrored_local_ids.add(local_id)
        sent = []

        async def capture(message):
            sent.append(message)
            return True

        daemon.send = capture

        class Reader:
            async def __aiter__(self):
                yield proto.encode({
                    "t": "input", "action": "dismiss", "session": daemon.grouped_session,
                    "generation": daemon._device_expected_generation, "id": local_id,
                }) + b"reader-progress-marker\n"

        try:
            await asyncio.wait_for(daemon._read_loop(Reader()), timeout=1)
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert not release.is_set()
            assert "reader-progress-marker" in daemon._devlog
            assert local_id not in daemon.model.retained_notifs
            assert any(message.get("t") == "close" and message.get("id") == local_id for message in sent)
        finally:
            release.set()
            await source.stop()

    asyncio.run(scenario())


def test_propagated_dismiss_timeout_cleans_pinned_dbus_reply_handler(monkeypatch):
    async def scenario():
        cfg = default_config().notifications
        cfg.device_dismiss = "propagate"
        source = NotificationSource(cfg, lambda _message: asyncio.sleep(0), lambda _local_id: asyncio.sleep(0))

        # Exercise the pinned dbus-next MessageBus.call implementation without
        # connecting to a live session bus. _call only needs these fields and a
        # send method that records the outgoing message.
        from dbus_next.aio import MessageBus

        control = object.__new__(MessageBus)
        control._loop = asyncio.get_running_loop()
        control._serial = 0
        control._method_return_handlers = {}
        control._name_owners = {}
        sent = []
        control.send = lambda message: sent.append(message)

        source._control = control
        _ready_identity(source, ":1.96")
        source._daemon_to_local = {778: 8}
        source._local_to_daemon = {8: 778}
        source._action_daemon_id = {8: 778}
        _ready_action_info(source, 8, 4)
        source._mirrored_local_ids.add(8)
        monkeypatch.setattr("status349.sources.notifications.DISMISS_TIMEOUT_S", 0.02)

        await source.dismiss(8)
        await asyncio.wait_for(source._dismiss_queue.join(), timeout=1)
        assert len(sent) == 1
        assert sent[0].destination == ":1.96"
        assert control._method_return_handlers == {}
        await asyncio.sleep(0.04)
        assert len(sent) == 1  # A timeout is handled once and never retried.
        await source.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalidation", ["replacement", "owner", "mode"])
def test_dismiss_dispatch_revalidates_immediately_before_call(invalidation):
    async def scenario():
        cfg = default_config().notifications
        cfg.device_dismiss = "propagate"
        source = NotificationSource(cfg, lambda _message: asyncio.sleep(0), lambda _local_id: asyncio.sleep(0))
        sent = []

        class Control:
            async def call(self, message):
                sent.append(message)

        control = Control()
        source._control = control
        _ready_identity(source, ":1.97")
        source._daemon_to_local = {779: 9}
        source._local_to_daemon = {9: 779}
        source._action_daemon_id = {9: 779}
        _ready_action_info(source, 9, 5)
        source._mirrored_local_ids.add(9)
        dispatch_started = asyncio.Event()
        release_dispatch = asyncio.Event()
        original_dispatch = source._dispatch_dismiss

        def invalidate():
            if invalidation == "replacement":
                source._open_info[9] = {"version": 6}
            elif invalidation == "owner":
                source._server_owner = ":1.98"
            else:
                source.cfg.device_dismiss = "local"

        async def delay_dispatch(pending, message):
            dispatch_started.set()
            await release_dispatch.wait()
            return await original_dispatch(pending, message)

        source._dispatch_dismiss = delay_dispatch
        await source.dismiss(9)
        await asyncio.wait_for(dispatch_started.wait(), timeout=1)
        # The queue worker already validated at dequeue; hold dispatch before
        # its final identity check to exercise replacement during that gap.
        invalidate()
        release_dispatch.set()
        await asyncio.wait_for(source._dismiss_queue.join(), timeout=1)
        assert sent == []
        await source.stop()

    asyncio.run(scenario())


def test_dismiss_shutdown_cancels_inflight_call_and_drains_bounded_queue(monkeypatch):
    async def scenario():
        cfg = default_config().notifications
        cfg.device_dismiss = "propagate"
        source = NotificationSource(cfg, lambda _message: asyncio.sleep(0), lambda _local_id: asyncio.sleep(0))

        from dbus_next.aio import MessageBus

        control = object.__new__(MessageBus)
        control._loop = asyncio.get_running_loop()
        control._serial = 0
        control._method_return_handlers = {}
        control._name_owners = {}
        sent = []
        control.send = lambda message: sent.append(message)
        source._control = control
        _ready_identity(source, ":1.99")
        source._daemon_to_local = {780: 11}
        source._local_to_daemon = {11: 780}
        source._action_daemon_id = {11: 780}
        _ready_action_info(source, 11, 7)
        source._mirrored_local_ids.add(11)
        monkeypatch.setattr("status349.sources.notifications.DISMISS_TIMEOUT_S", 5.0)

        for _ in range(DISMISS_QUEUE_LIMIT + 4):
            await source.dismiss(11)
        await asyncio.wait_for(
            _wait_for_notification(lambda: bool(control._method_return_handlers)), timeout=1
        )
        assert source._dismiss_queue.qsize() == DISMISS_QUEUE_LIMIT - 1
        assert len(sent) == 1

        await source.stop()
        assert source._dismiss_queue.empty()
        await asyncio.wait_for(source._dismiss_queue.join(), timeout=1)
        assert control._method_return_handlers == {}
        assert len(sent) == 1

    asyncio.run(scenario())


def test_dismiss_worker_preserves_cancellation_when_reply_completes():
    async def scenario():
        cfg = default_config().notifications
        cfg.device_dismiss = "propagate"
        source = NotificationSource(cfg, lambda _message: asyncio.sleep(0), lambda _local_id: asyncio.sleep(0))
        entered = asyncio.Event()
        reply = asyncio.Event()

        class Control:
            async def call(self, _message):
                entered.set()
                await reply.wait()
                # Cancellation arrives while the reply has completed, before
                # a nested wait_for could resume its parent queue worker.
                asyncio.get_running_loop().call_soon(source._dismiss_task.cancel)

        source._control = Control()
        _ready_identity(source, ":1.100")
        source._daemon_to_local = {781: 12}
        source._local_to_daemon = {12: 781}
        source._action_daemon_id = {12: 781}
        _ready_action_info(source, 12, 8)
        source._mirrored_local_ids.add(12)
        try:
            await source.dismiss(12)
            await asyncio.wait_for(entered.wait(), timeout=1)
            reply.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(source._dismiss_task), timeout=1)
            await asyncio.wait_for(source._dismiss_queue.join(), timeout=1)
        finally:
            await source.stop()

    asyncio.run(scenario())


async def _wait_for_notification(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


def test_grouped_source_expiry_only_removes_legacy_active_projection():
    async def scenario():
        active_expired = []
        removed = []

        async def on_active_expire(local_id):
            active_expired.append(local_id)

        async def on_close(local_id):
            removed.append(local_id)

        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            on_close,
            on_active_expire=on_active_expire,
            grouped_mode=lambda: True,
        )
        await source._handle_notify(_notify_call(":1.94", 1, 0, "retained", expire=100))
        await source._handle(_notify_reply(":1.94", 1, 778))
        source._expiry_deadlines[1] = time.monotonic() - 1

        await source._expire_due()

        assert active_expired == [1]
        assert removed == []
        assert source._local_to_daemon == {1: 778}
        assert source._daemon_to_local == {778: 1}
        assert source._mirrored_local_ids == {1}
        assert source.attention_expired(1)

    asyncio.run(scenario())


def test_daemon_shares_local_notification_ids_between_dbus_and_ipc():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        await daemon.notifications._handle_notify(_notify_call(":1.95", 1, 0, "desktop"))
        desktop_id = next(iter(daemon.notifications._local_to_daemon))

        reply = await daemon._ipc_handler({"cmd": "notify", "summary": "injected"})

        assert desktop_id == 100000
        assert reply["id"] == 100001
        assert reply["id"] in daemon.model.retained_notifs

    asyncio.run(scenario())


def test_grouped_source_bounds_associations_outbox_pending_replies_and_monitor_queue():
    async def scenario():
        async def noop(_local_id):
            pass

        cfg = default_config().notifications
        source = NotificationSource(cfg, lambda _message: asyncio.sleep(0), noop, grouped_mode=lambda: True)
        for serial in range(1, 41):
            await source._handle_notify(_notify_call(":1.91", serial, 0, f"card {serial}"))
        assert len(source._local_to_daemon) == ASSOCIATION_LIMIT
        assert len(source._mirrored_local_ids) == ASSOCIATION_LIMIT
        assert len(source._outbox) <= NOTIFY_OUTBOX_LIMIT
        assert len(source._by_serial) <= PENDING_REPLY_LIMIT
        assert 1 not in source._local_to_daemon

        pending = NotificationSource(cfg, lambda _message: asyncio.sleep(0), noop, grouped_mode=lambda: True)
        await pending._handle_notify(_notify_call(":1.92", 1, 0, "base"))
        await pending._handle(_notify_reply(":1.92", 1, 902))
        for serial in range(2, 72):
            await pending._handle_notify(_notify_call(":1.92", serial, 902, f"replacement {serial}"))
        assert len(pending._by_serial) == PENDING_REPLY_LIMIT
        assert len(pending._outbox) == 1
        assert not any(key[1] == 2 for key in pending._by_serial)
        await pending._handle(_notify_reply(":1.92", 2, 999))
        assert pending._daemon_to_local == {902: 1}

        class Monitor:
            disconnected = False

            def disconnect(self):
                self.disconnected = True

        monitor = Monitor()
        pending._monitor = monitor
        signal_message = Message(
            message_type=MessageType.SIGNAL,
            sender=NOTIFICATIONS_NAME,
            path=NOTIFICATIONS_PATH,
            interface=NOTIFICATIONS_NAME,
            member="NotificationClosed",
            signature="uu",
            body=[902, 1],
        )
        for _ in range(MONITOR_QUEUE_LIMIT):
            pending._messages.put_nowait(signal_message)
        pending._enqueue(signal_message)
        assert pending._messages.empty()
        assert monitor.disconnected

    asyncio.run(scenario())


def test_server_default_popup_expires_without_desktop_close():
    async def scenario():
        closed = []
        close_seen = asyncio.Event()

        async def on_close(local_id):
            closed.append(local_id)
            close_seen.set()

        cfg = default_config().notifications
        cfg.popup_timeout_ms = 30
        source = NotificationSource(cfg, lambda _message: asyncio.sleep(0), on_close)
        await source._handle_notify(_notify_call(":1.70", 1, 0, "temporary", expire=-1))
        await source._handle(_notify_reply(":1.70", 1, 901))
        task = asyncio.create_task(source._process_loop())
        try:
            await asyncio.wait_for(close_seen.wait(), 1)
            assert closed == [1]
            assert source._mirrored_local_ids == set()
            assert source._daemon_to_local == {}
            assert source._local_to_daemon == {}
            assert source._by_serial == {}
            assert source._outbox == {}
            assert source._expiry_deadlines == {}
            await source._handle(_closed_signal(901))
            assert closed == [1]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_replacement_resets_popup_expiry():
    async def scenario():
        closed = []

        async def on_close(local_id):
            closed.append(local_id)

        cfg = default_config().notifications
        source = NotificationSource(cfg, lambda _message: asyncio.sleep(0), on_close)
        await source._handle_notify(_notify_call(":1.71", 1, 0, "first", expire=-1))
        await source._handle(_notify_reply(":1.71", 1, 902))
        source._expiry_deadlines[1] = time.monotonic() - 1
        await source._handle_notify(_notify_call(":1.71", 2, 902, "replacement", expire=-1))
        await source._handle(_notify_reply(":1.71", 2, 902))
        await source._expire_due()
        assert closed == []
        assert source._daemon_to_local == {902: 1}
        source._expiry_deadlines[1] = time.monotonic() - 1
        await source._expire_due()
        assert closed == [1]
        assert source._daemon_to_local == {}

    asyncio.run(scenario())


def test_monitor_loss_closes_cards_with_untrustworthy_desktop_ids():
    async def scenario():
        closed = []
        close_seen = asyncio.Event()

        async def noop(_message):
            pass

        async def on_close(local_id):
            closed.append(local_id)
            close_seen.set()

        async def fail_setup():
            raise RuntimeError("session bus unavailable")

        source = NotificationSource(default_config().notifications, noop, on_close)
        await source._handle_notify(_notify_call(":1.55", 10, 0, "stale card"))
        await source._handle(_notify_reply(":1.55", 10, 888))
        source._setup = fail_setup
        task = asyncio.create_task(source._monitor_loop())
        try:
            await asyncio.wait_for(close_seen.wait(), 1)
            assert closed == [1]
            assert source._mirrored_local_ids == set()
            assert source._daemon_to_local == {}
            assert source._outbox == {}
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_partial_monitor_setup_closes_created_connection(monkeypatch):
    async def scenario():
        created = []

        class FakeBus:
            def __init__(self, **_kwargs):
                self.closed = False
                created.append(self)

            async def connect(self):
                if len(created) == 2:
                    raise RuntimeError("monitor connect failed")
                return self

            def add_message_handler(self, _handler):
                pass

            async def call(self, _message):
                return Message(message_type=MessageType.METHOD_RETURN, reply_serial=1)

            def remove_message_handler(self, _handler):
                pass

            def disconnect(self):
                self.closed = True

        monkeypatch.setattr("status349.sources.notifications.MessageBus", FakeBus)

        async def noop(_message):
            pass

        async def on_close(_local_id):
            pass

        source = NotificationSource(default_config().notifications, noop, on_close)
        with pytest.raises(RuntimeError, match="monitor connect failed"):
            await source._setup()
        await source._teardown()
        assert created[0].closed

    asyncio.run(scenario())


def test_close_cancels_a_rate_limited_queued_notify_and_later_card_is_delivered():
    async def scenario():
        delivered = []
        closed = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        later_delivered = asyncio.Event()

        async def on_notify(message):
            delivered.append(message["id"])
            if message["id"] == 1:
                first_started.set()
                await release_first.wait()
            elif message["id"] == 3:
                later_delivered.set()

        async def on_close(local_id):
            closed.append(local_id)

        source = NotificationSource(default_config().notifications, on_notify, on_close)
        send_task = asyncio.create_task(source._send_loop())
        try:
            await source._handle_notify(_notify_call(":1.60", 1, 0, "first"))
            await asyncio.wait_for(first_started.wait(), 1)

            await source._handle_notify(_notify_call(":1.60", 2, 0, "fast close"))
            await source._handle(_notify_reply(":1.60", 2, 900))
            await source._handle(_closed_signal(900))
            assert closed == [2]

            release_first.set()
            await source._handle_notify(_notify_call(":1.61", 3, 0, "later valid card"))
            await asyncio.wait_for(later_delivered.wait(), 1)
            assert delivered == [1, 3]
        finally:
            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def _notify(summary: str, expire: int = 5000) -> int:
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        reply = await bus.call(
            Message(
                destination=NOTIFICATIONS_NAME,
                path=NOTIFICATIONS_PATH,
                interface=NOTIFICATIONS_NAME,
                member="Notify",
                signature="susssasa{sv}i",
                body=["349-test", 0, "", summary, "body", [], {}, expire],
            )
        )
        return int(reply.body[0])
    finally:
        bus.disconnect()


async def _close(nid: int) -> None:
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        await bus.call(
            Message(
                destination=NOTIFICATIONS_NAME,
                path=NOTIFICATIONS_PATH,
                interface=NOTIFICATIONS_NAME,
                member="CloseNotification",
                signature="u",
                body=[nid],
            )
        )
    finally:
        bus.disconnect()


@pytest.mark.skipif(not HAVE_SESSION_BUS, reason="no session bus")
def test_notification_mirror_and_desktop_close():
    fake = FakeDevice().start()
    try:
        cfg = default_config()
        cfg.link.port = fake.path
        cfg.daemon.tick_s = 0.05
        cfg.daemon.sync_interval_s = 0.5
        summary = f"349-mirror-{os.getpid()}-{int(time.time() * 1000)}"

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(Daemon(cfg, stop).run())
            daemon_id = None
            try:
                await _wait_for(lambda: any(m["t"] == "sync" for m in fake.received))
                daemon_id = await _notify(summary)
                await _wait_for(lambda: any(m["t"] == "notify" and m["summary"] == summary for m in fake.received))

                await _close(daemon_id)
                daemon_id = None
                await _wait_for(lambda: any(m["t"] == "close" for m in fake.received))
            finally:
                if daemon_id is not None:
                    await _close(daemon_id)
                stop.set()
                await asyncio.wait_for(task, 5)

        asyncio.run(scenario())
    finally:
        fake.stop()


@pytest.mark.skipif(not HAVE_SESSION_BUS, reason="no session bus")
def test_mirrored_popup_expires_without_desktop_close():
    fake = FakeDevice().start()
    try:
        cfg = default_config()
        cfg.link.port = fake.path
        cfg.daemon.tick_s = 0.05
        cfg.daemon.sync_interval_s = 0.5
        summary = f"349-expire-{os.getpid()}-{int(time.time() * 1000)}"

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(Daemon(cfg, stop).run())
            daemon_id = None
            try:
                await _wait_for(lambda: any(m["t"] == "sync" for m in fake.received))
                daemon_id = await _notify(summary, expire=300)
                await _wait_for(lambda: any(m["t"] == "notify" and m["summary"] == summary for m in fake.received))
                local_id = next(m["id"] for m in fake.received if m["t"] == "notify" and m["summary"] == summary)
                await _wait_for(lambda: any(m["t"] == "close" and m["id"] == local_id for m in fake.received))
            finally:
                if daemon_id is not None:
                    await _close(daemon_id)
                stop.set()
                await asyncio.wait_for(task, 5)

        asyncio.run(scenario())
    finally:
        fake.stop()


@pytest.mark.skipif(not HAVE_SESSION_BUS, reason="no session bus")
def test_device_dismiss_propagates():
    fake = FakeDevice().start()
    try:
        cfg = default_config()
        cfg.link.port = fake.path
        cfg.daemon.tick_s = 0.05
        cfg.daemon.sync_interval_s = 0.5
        cfg.notifications.device_dismiss = "propagate"
        summary = f"349-propagate-{os.getpid()}-{int(time.time() * 1000)}"

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(Daemon(cfg, stop).run())
            daemon_id = None
            try:
                await _wait_for(lambda: any(m["t"] == "sync" for m in fake.received))
                daemon_id = await _notify(summary)
                await _wait_for(lambda: any(m["t"] == "notify" and m["summary"] == summary for m in fake.received))
                local_id = next(m["id"] for m in fake.received if m["t"] == "notify" and m["summary"] == summary)

                fake.send({"t": "input", "action": "dismiss", "id": local_id})
                await _wait_for(lambda: any(m["t"] == "close" and m["id"] == local_id for m in fake.received))
                daemon_id = None  # the daemon closed it on the desktop
            finally:
                if daemon_id is not None:
                    await _close(daemon_id)
                stop.set()
                await asyncio.wait_for(task, 5)

        asyncio.run(scenario())
    finally:
        fake.stop()
