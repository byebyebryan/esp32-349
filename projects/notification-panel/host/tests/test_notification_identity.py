"""Owner identity recovery and private dbus-broker lifecycle regressions."""

import asyncio
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest
from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from status349.config import NotificationsConfig, default_config
from status349.sources.notifications import (
    DBUS_OWNER_MATCH,
    MONITOR_RULES,
    NOTIFICATIONS_NAME,
    NOTIFICATIONS_PATH,
    NotificationSource,
)


def _notify(serial: int, sender: str = ":1.50", text: str = "body") -> Message:
    return Message(
        destination=NOTIFICATIONS_NAME,
        path=NOTIFICATIONS_PATH,
        interface=NOTIFICATIONS_NAME,
        member="Notify",
        signature="susssasa{sv}i",
        sender=sender,
        serial=serial,
        body=["Test", 0, "", f"Title {serial}", text, ["default", "Open"], {}, -1],
    )


def _notify_reply(serial: int, desktop_id: int, *, sender: str, destination: str = ":1.50") -> Message:
    return Message(
        message_type=MessageType.METHOD_RETURN,
        destination=destination,
        sender=sender,
        reply_serial=serial,
        signature="u",
        body=[desktop_id],
    )


def _owner_change(old_owner: str, new_owner: str) -> Message:
    return Message(
        message_type=MessageType.SIGNAL,
        sender="org.freedesktop.DBus",
        path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus",
        member="NameOwnerChanged",
        signature="sss",
        body=[NOTIFICATIONS_NAME, old_owner, new_owner],
    )


def _method_return(signature: str = "", body: list | None = None) -> Message:
    return Message(
        message_type=MessageType.METHOD_RETURN,
        sender="org.freedesktop.DBus",
        destination=":1.80",
        reply_serial=1,
        signature=signature,
        body=[] if body is None else body,
    )


def _dbus_error(name: str) -> Message:
    return Message(
        message_type=MessageType.ERROR,
        sender="org.freedesktop.DBus",
        destination=":1.80",
        reply_serial=1,
        error_name=name,
    )


def _source(owner: str = ":1.40", pid: int = 40, *, archived=None) -> NotificationSource:
    source = NotificationSource(
        NotificationsConfig(),
        lambda _message: asyncio.sleep(0),
        lambda _local_id: asyncio.sleep(0),
        on_monitor_reset=archived,
        grouped_mode=lambda: True,
    )
    source._server_owner = owner
    source._server_pid = pid
    source._identity_state = "ready"
    source._owner_change_applied_generation = source._owner_change_generation
    return source


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition did not become true")


def test_control_handler_consumes_only_notification_owner_signals():
    async def scenario():
        source = _source()
        control = object()
        source._control = control
        source._control_generation = 4
        handler = lambda message: source._handle_control_message(control, 4, message)

        # Normal D-Bus method replies must reach MessageBus.call, including
        # AddMatch and GetNameOwner replies.
        assert handler(_method_return("s", [":1.40"])) is False
        unrelated = _owner_change(":1.1", ":1.2")
        unrelated.body[0] = "org.freedesktop.Notifications.Other"
        assert handler(unrelated) is False
        assert "NameOwnerChanged" not in " ".join(MONITOR_RULES)
        assert DBUS_OWNER_MATCH.endswith("arg0='org.freedesktop.Notifications'")

        signal = _owner_change(":1.40", ":1.41")
        assert handler(signal) is True
        assert source._server_owner is None and source._server_pid is None
        queued = source._messages.get_nowait()
        assert queued.owner_change_generation == 1
        assert queued.message is signal

    asyncio.run(scenario())


def test_delayed_identity_query_cannot_restore_owner_after_change():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        class Control:
            async def call(self, message):
                assert message.member == "GetNameOwner"
                entered.set()
                await release.wait()
                return _method_return("s", [":1.40"])

        source = _source()
        control = Control()
        source._control = control
        source._control_generation = 9
        task = asyncio.create_task(source._refresh_server_identity(control, 9, 0))
        await asyncio.wait_for(entered.wait(), 1)
        assert source._handle_control_message(control, 9, _owner_change(":1.40", ":1.41"))
        release.set()

        assert await asyncio.wait_for(task, 1) is False
        assert source.identity_status() == {
            "state": "resolving", "owner": None, "pid": None, "last_error": None,
        }
        assert source._owner_change_generation == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("reply_before_owner_events", [True, False])
@pytest.mark.parametrize("identity_before_queued_events", [True, False])
def test_reused_desktop_id_requires_final_owner_evidence(
    reply_before_owner_events: bool, identity_before_queued_events: bool
):
    async def scenario():
        archived = []

        async def archive(ids):
            archived.extend(ids)

        source = _source(":1.40", 40, archived=archive)
        await source._handle(_notify(1, text="old owner card"))
        await source._handle(_notify_reply(1, 349, sender=":1.40"))
        assert source.action_candidate(1)["owner"] == ":1.40"

        await source._handle(_notify(2, text="new owner card"))
        if reply_before_owner_events:
            await source._handle(_notify_reply(2, 349, sender=":1.41"))
            assert source._open_info[2]["reply_owner"] == ":1.41"

        control = object()
        source._control = control
        source._control_generation = 2
        assert source._handle_control_message(control, 2, _owner_change(":1.40", ""))
        assert source._handle_control_message(control, 2, _owner_change("", ":1.41"))

        query_control = _IdentityControl(":1.41", 141)
        source._control = query_control
        if identity_before_queued_events:
            assert await source._refresh_server_identity(
                query_control, source._control_generation, source._owner_change_generation
            )
            assert source.action_candidate(2) is None

        # Drain the same exact control messages one at a time so the test can
        # assert the intermediate-loss event does not archive final-owner work.
        while not source._messages.empty():
            queued = source._messages.get_nowait()
            await source._handle_owner_change(
                queued.message, queued.owner_change_generation
            )
            if source.action_candidate(2) is None:
                assert 2 in source._open_info, (
                    f"owner event {queued.owner_change_generation} archived a fresh final-owner card; "
                    f"owner={source._observed_owner!r} server={source.identity_status()} "
                    f"ids={source._mirrored_local_ids!r}"
                )

        if not reply_before_owner_events:
            # The request was already observed, but its method return can
            # arrive after both loss and gain have been queued and handled.
            await source._handle(_notify_reply(2, 349, sender=":1.41"))
        if not identity_before_queued_events:
            assert await source._refresh_server_identity(
                query_control, source._control_generation, source._owner_change_generation
            )

        assert source.action_candidate(2) is not None
        candidate = source.action_candidate(2)
        assert candidate == {
                "id": 2, "version": source._open_info[2]["version"],
                "desktop_id": 349, "owner": ":1.41", "pid": 141,
                "expected": {
                    "app": "Test", "summary": "Title 2", "body": "new owner card",
                    "default_label": "Open",
                },
        }
        assert source.action_candidate(1) is None
        assert archived == [1]
        assert source._daemon_to_local == {349: 2}

    asyncio.run(scenario())


class _IdentityControl:
    def __init__(self, owner: str, pid: int):
        self.owner = owner
        self.pid = pid
        self.name_calls = 0

    async def call(self, message):
        if message.member == "GetNameOwner":
            self.name_calls += 1
            return _method_return("s", [self.owner])
        assert message.member == "GetConnectionUnixProcessID"
        return _method_return("u", [self.pid])


def test_transient_identity_failure_recovers_without_stalling_monitor(monkeypatch):
    async def scenario():
        second_lookup = asyncio.Event()
        release_lookup = asyncio.Event()
        attempts = 0

        class Control:
            async def call(self, message):
                nonlocal attempts
                if message.member == "GetNameOwner":
                    attempts += 1
                    if attempts == 1:
                        return _dbus_error("org.freedesktop.DBus.Error.NameHasNoOwner")
                    second_lookup.set()
                    await release_lookup.wait()
                    return _method_return("s", [":1.44"])
                assert message.member == "GetConnectionUnixProcessID"
                return _method_return("u", [44])

        monkeypatch.setattr("status349.sources.notifications.IDENTITY_RETRY_INITIAL_S", 0.01)
        monkeypatch.setattr("status349.sources.notifications.IDENTITY_RETRY_MAX_S", 0.02)
        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            lambda _local_id: asyncio.sleep(0),
        )
        control = Control()
        source._control = control
        source._control_generation = 1
        identity_task = asyncio.create_task(source._identity_loop(control, 1))

        delivered = asyncio.Event()
        async def on_notify(_message):
            delivered.set()
        source._on_notify = on_notify
        process_task = asyncio.create_task(source._process_loop())
        send_task = asyncio.create_task(source._send_loop())
        try:
            await _wait_until(lambda: source.identity_status()["state"] == "retrying")
            assert source.identity_status()["last_error"] == "name_not_owned"
            await asyncio.wait_for(second_lookup.wait(), 1)

            # The D-Bus identity RPC is stalled while the independent monitor
            # queue still mirrors a complete Notify/reply pair.
            source._enqueue(_notify(1, sender=":1.50"))
            source._enqueue(_notify_reply(1, 349, sender=":1.44"))
            await asyncio.wait_for(delivered.wait(), 0.5)
            assert source.identity_status()["state"] == "resolving"

            release_lookup.set()
            await _wait_until(lambda: source.action_candidate(1) is not None)
            candidate = source.action_candidate(1)
            assert candidate["owner"] == ":1.44" and candidate["pid"] == 44
            assert source.identity_status() == {
                "state": "ready", "owner": ":1.44", "pid": 44, "last_error": None,
            }
        finally:
            release_lookup.set()
            for task in (identity_task, process_task, send_task):
                task.cancel()
            await asyncio.gather(identity_task, process_task, send_task, return_exceptions=True)

    asyncio.run(scenario())


def test_a_b_c_fast_restarts_only_promote_final_owner_candidate():
    async def scenario():
        archived = []

        async def archive(ids):
            archived.extend(ids)

        source = _source(":1.40", 40, archived=archive)
        await source._handle(_notify(1, text="owner-A"))
        await source._handle(_notify_reply(1, 349, sender=":1.40"))
        assert source.action_candidate(1) is not None

        control = object()
        source._control = control
        source._control_generation = 2
        # B's owner event is observed, then B replies before that event has
        # been applied to the monitor stream.
        assert source._handle_control_message(control, 2, _owner_change(":1.40", ""))
        assert source._handle_control_message(control, 2, _owner_change("", ":1.41"))
        await source._handle(_notify(2, text="owner-B"))
        await source._handle(_notify_reply(2, 349, sender=":1.41"))

        # B dies before the monitor consumes either event. C's arrival/reply
        # is the only evidence that may survive the combined event backlog.
        assert source._handle_control_message(control, 2, _owner_change(":1.41", ""))
        assert source._handle_control_message(control, 2, _owner_change("", ":1.42"))
        await source._handle(_notify(3, text="owner-C"))
        await source._handle(_notify_reply(3, 349, sender=":1.42"))

        final_control = _IdentityControl(":1.42", 142)
        source._control = final_control
        assert await source._refresh_server_identity(
            final_control, source._control_generation, source._owner_change_generation
        )

        while not source._messages.empty():
            queued = source._messages.get_nowait()
            await source._handle_owner_change(
                queued.message, queued.owner_change_generation
            )

        candidates = source.action_candidates()
        assert set(candidates) == {3}
        assert candidates[3]["owner"] == ":1.42"
        assert candidates[3]["pid"] == 142
        assert candidates[3]["desktop_id"] == 349
        assert archived == [1, 2]
        assert source._daemon_to_local == {349: 3}

    asyncio.run(scenario())


def test_late_owner_a_reply_cannot_evict_owner_b_reused_id():
    async def scenario():
        source = _source(":1.40", 40)
        # A's call remains pending across replacement. Its late reply must
        # not touch mappings after B establishes its own ID 349.
        await source._handle(_notify(1, text="old A call"))
        old_local = next(iter(source._mirrored_local_ids))

        control = object()
        source._control = control
        source._control_generation = 3
        assert source._handle_control_message(control, 3, _owner_change(":1.40", ""))
        assert source._handle_control_message(control, 3, _owner_change("", ":1.41"))
        query_control = _IdentityControl(":1.41", 141)
        source._control = query_control
        assert await source._refresh_server_identity(
            query_control, source._control_generation, source._owner_change_generation
        )
        while not source._messages.empty():
            queued = source._messages.get_nowait()
            await source._handle_owner_change(
                queued.message, queued.owner_change_generation
            )

        await source._handle(_notify(2, text="current B call"))
        await source._handle(_notify_reply(2, 349, sender=":1.41"))
        b_local = next(
            local_id for local_id, info in source._open_info.items()
            if info["expected"]["summary"] == "Title 2"
        )
        assert source.action_candidate(b_local)["owner"] == ":1.41"
        association_before = dict(source._daemon_to_local)

        await source._handle(_notify_reply(1, 349, sender=":1.40"))

        assert source._daemon_to_local == association_before == {349: b_local}
        assert source.action_candidate(b_local)["owner"] == ":1.41"
        assert source.action_candidate(old_local) is None

    asyncio.run(scenario())


def test_old_card_stays_history_when_same_unique_owner_reacquires_name():
    async def scenario():
        archived = []

        async def archive(ids):
            archived.extend(ids)

        source = _source(":1.40", 40, archived=archive)
        await source._handle(_notify(1, text="old A card"))
        await source._handle(_notify_reply(1, 349, sender=":1.40"))
        assert source.action_candidate(1) is not None

        control = object()
        source._control = control
        source._control_generation = 4
        assert source._handle_control_message(control, 4, _owner_change(":1.40", ""))
        assert source._handle_control_message(control, 4, _owner_change("", ":1.40"))
        query_control = _IdentityControl(":1.40", 40)
        source._control = query_control
        assert await source._refresh_server_identity(
            query_control, source._control_generation, source._owner_change_generation
        )
        while not source._messages.empty():
            queued = source._messages.get_nowait()
            await source._handle_owner_change(
                queued.message, queued.owner_change_generation
            )

        assert source.action_candidate(1) is None
        assert source._daemon_to_local == {}
        assert archived == [1]

    asyncio.run(scenario())


@pytest.mark.parametrize("disconnected", ["control", "monitor"])
def test_disconnect_supervision_and_stop_collect_waiters(disconnected):
    async def scenario():
        class WaitBus:
            def __init__(self):
                self.disconnect = asyncio.Event()
                self.entered = asyncio.Event()
                self.cancelled = asyncio.Event()

            async def wait_for_disconnect(self):
                self.entered.set()
                try:
                    await self.disconnect.wait()
                finally:
                    if not self.disconnect.is_set():
                        self.cancelled.set()

        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            lambda _local_id: asyncio.sleep(0),
        )
        source._control = WaitBus()
        source._monitor = WaitBus()
        wait_task = asyncio.create_task(source._wait_for_connection_disconnect())
        await asyncio.wait_for(source._control.entered.wait(), 1)
        await asyncio.wait_for(source._monitor.entered.wait(), 1)
        getattr(source, f"_{disconnected}").disconnect.set()
        assert await asyncio.wait_for(wait_task, 1) == disconnected
        other = source._monitor if disconnected == "control" else source._control
        assert other.cancelled.is_set()

    asyncio.run(scenario())


def test_control_disconnect_reconnects_monitor_pair(monkeypatch):
    async def scenario():
        class WaitBus:
            def __init__(self):
                self.disconnected = asyncio.Event()

            async def wait_for_disconnect(self):
                await self.disconnected.wait()

            def disconnect(self):
                self.disconnected.set()

        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            lambda _local_id: asyncio.sleep(0),
        )
        pairs = []
        identities = []

        async def identity(cancelled: asyncio.Event):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def fake_setup():
            pair = (WaitBus(), WaitBus())
            pairs.append(pair)
            source._control, source._monitor = pair
            cancelled = asyncio.Event()
            identities.append(cancelled)
            source._identity_task = source._watch_task(
                asyncio.create_task(identity(cancelled))
            )

        monkeypatch.setattr(source, "_setup", fake_setup)
        real_sleep = asyncio.sleep

        async def fast_reconnect_sleep(delay, *args, **kwargs):
            await real_sleep(0 if delay >= 1 else delay)

        monkeypatch.setattr(
            "status349.sources.notifications.asyncio.sleep", fast_reconnect_sleep
        )
        task = asyncio.create_task(source._monitor_loop())
        try:
            await _wait_until(lambda: len(pairs) == 1)
            # Failure of the control channel must tear down the pair and cause
            # both connections, the owner subscription, and identity to restart.
            pairs[0][0].disconnected.set()
            await _wait_until(lambda: len(pairs) >= 2)
            assert pairs[0][1].disconnected.is_set()
            assert identities[0].is_set()
            assert source._control is pairs[1][0]
            assert source._monitor is pairs[1][1]
            assert source._identity_task is not None
            assert not identities[1].is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await source._teardown()
            assert all(event.is_set() for event in identities)

    asyncio.run(scenario())


def test_stop_during_connection_wait_cancels_disconnect_and_identity_tasks(monkeypatch):
    async def scenario():
        class WaitBus:
            def __init__(self):
                self.entered = asyncio.Event()
                self.cancelled = asyncio.Event()
                self.disconnected = asyncio.Event()

            async def wait_for_disconnect(self):
                self.entered.set()
                try:
                    await self.disconnected.wait()
                finally:
                    if not self.disconnected.is_set():
                        self.cancelled.set()

            def disconnect(self):
                self.disconnected.set()

        source = NotificationSource(
            default_config().notifications,
            lambda _message: asyncio.sleep(0),
            lambda _local_id: asyncio.sleep(0),
        )
        buses = []
        identity_cancelled = asyncio.Event()

        async def fake_setup():
            control, monitor = WaitBus(), WaitBus()
            buses.extend((control, monitor))
            source._control, source._monitor = control, monitor
            source._identity_task = source._watch_task(asyncio.create_task(identity()))

        async def identity():
            try:
                await asyncio.Event().wait()
            finally:
                identity_cancelled.set()

        monkeypatch.setattr(source, "_setup", fake_setup)
        await source.start()
        await _wait_until(lambda: len(buses) == 2)
        await asyncio.wait_for(buses[0].entered.wait(), 1)
        await asyncio.wait_for(buses[1].entered.wait(), 1)
        await source.stop()

        assert identity_cancelled.is_set()
        assert all(bus.cancelled.is_set() for bus in buses)
        assert source._identity_task is None
        assert source._control is None and source._monitor is None
        assert not any(
            task.get_name() in {"notify-control-disconnect", "notify-monitor-disconnect", "Task-"}
            and not task.done()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        )

    asyncio.run(scenario())


_PRIVATE_SERVER = textwrap.dedent(
    """
    import asyncio
    from dbus_next import Message, MessageType
    from dbus_next.aio import MessageBus
    from dbus_next.constants import BusType

    async def main():
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        await bus.request_name("org.freedesktop.Notifications")
        def handle(message):
            if (message.message_type == MessageType.METHOD_CALL
                    and message.interface == "org.freedesktop.Notifications"
                    and message.member == "Notify"):
                bus.send(Message.new_method_return(message, "u", [349]))
                return True
            return False
        bus.add_message_handler(handle)
        print("READY", flush=True)
        await asyncio.Event().wait()

    asyncio.run(main())
    """
)


class _PrivateBroker:
    def __init__(self, root: Path):
        self.root = root
        self.address = f"unix:path={root / 'bus.sock'}"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(root / "bus.sock"))
        self.listener.listen(16)
        config = root / "bus.conf"
        config.write_text(textwrap.dedent(
            """
            <busconfig>
              <type>session</type>
              <policy context="default">
                <allow send_destination="*" eavesdrop="true"/>
                <allow eavesdrop="true"/>
                <allow own="*"/>
              </policy>
            </busconfig>
            """
        ))
        command = (
            'export LISTEN_PID=$$; exec 3<&"$2"; '
            'exec /usr/bin/dbus-broker-launch --scope user --config-file "$1"'
        )
        environment = os.environ.copy()
        environment.update({
            "LISTEN_FDS": "1",
            "DBUS_SESSION_BUS_ADDRESS": self.address,
            "XDG_RUNTIME_DIR": str(root),
        })
        self.process = subprocess.Popen(
            ["bash", "-c", command, "bash", str(config), str(self.listener.fileno())],
            env=environment,
            pass_fds=(self.listener.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.listener.close()

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        if self.process.stderr:
            self.process.stderr.close()


def _start_server(address: str) -> subprocess.Popen:
    environment = os.environ.copy()
    environment["DBUS_SESSION_BUS_ADDRESS"] = address
    process = subprocess.Popen(
        [sys.executable, "-c", _PRIVATE_SERVER],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"notification test server exited early: {output}")
        if process.stdout and select.select([process.stdout], [], [], 0.05)[0]:
            if process.stdout.readline().strip() == "READY":
                return process
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    raise AssertionError("notification test server did not acquire its name")


def _stop_server(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    if process.stdout:
        process.stdout.close()


async def _call_bus(message: Message) -> Message:
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        async with asyncio.timeout(2):
            return await bus.call(message)
    finally:
        bus.disconnect()


async def _get_owner() -> str:
    reply = await _call_bus(Message(
        destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus", member="GetNameOwner",
        signature="s", body=[NOTIFICATIONS_NAME],
    ))
    assert reply.message_type == MessageType.METHOD_RETURN
    return reply.body[0]


async def _send_notify(summary: str) -> int:
    reply = await _call_bus(Message(
        destination=NOTIFICATIONS_NAME, path=NOTIFICATIONS_PATH,
        interface=NOTIFICATIONS_NAME, member="Notify", signature="susssasa{sv}i",
        body=["349-private-test", 0, "", summary, "body", ["default", "Open"], {}, -1],
    ))
    assert reply.message_type == MessageType.METHOD_RETURN
    return reply.body[0]


def test_private_broker_owner_restart_and_id_reuse_regression(monkeypatch):
    """Show monitor-only misses owner events, then exercise the control AddMatch fix."""
    if shutil.which("bash") is None:
        pytest.skip("bash is unavailable for dbus-broker activation")

    async def scenario(root: Path):
        broker = _PrivateBroker(root)
        address = broker.address
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(root))
        baseline = None
        provider = None
        client_source = None
        try:
            # Baseline: BecomeMonitor's NameOwnerChanged rule alone sees no
            # owner events through dbus-broker, leaving a cached owner stale.
            baseline = await MessageBus(
                bus_type=BusType.SESSION, negotiate_unix_fd=True
            ).connect()
            old_rule = (
                "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
                "member='NameOwnerChanged',arg0='org.freedesktop.Notifications'"
            )
            reply = await baseline.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus.Monitoring", member="BecomeMonitor",
                signature="asu", body=[[old_rule], 0],
            ))
            assert reply.message_type == MessageType.METHOD_RETURN
            baseline_events = []
            cached_owner = [None]

            def observe_owner_change(message):
                if message.member == "NameOwnerChanged":
                    baseline_events.append(message)
                    if message.body[0] == NOTIFICATIONS_NAME:
                        cached_owner[0] = message.body[2]
                    return True
                return False

            baseline.add_message_handler(
                observe_owner_change
            )
            provider = _start_server(address)
            owner_a = await _get_owner()
            cached_owner[0] = owner_a
            baseline_events.clear()
            _stop_server(provider)
            provider = None
            provider = _start_server(address)
            owner_b = await _get_owner()
            assert owner_a != owner_b
            await asyncio.sleep(0.15)
            assert baseline_events == []
            assert cached_owner[0] == owner_a and cached_owner[0] != owner_b
            baseline.disconnect()
            baseline = None
            _stop_server(provider)
            provider = None

            # Corrected source registers its normal exact AddMatch before the
            # initial identity snapshot and is already monitoring before A owns
            # the well-known name.
            archived = []
            async def on_notify(_message):
                pass
            async def on_close(_local_id):
                pass
            async def on_archive(ids):
                archived.extend(ids)

            cfg = default_config().notifications
            client_source = NotificationSource(
                cfg, on_notify, on_close, on_monitor_reset=on_archive,
                grouped_mode=lambda: True,
            )
            await client_source.start()
            await _wait_until(lambda: client_source.identity_status()["last_error"] == "name_not_owned")
            provider = _start_server(address)
            owner_a = await _get_owner()
            await _wait_until(lambda: client_source.identity_status()["owner"] == owner_a)
            pid_a = client_source.identity_status()["pid"]
            assert pid_a == provider.pid
            assert await _send_notify("owner-A") == 349
            await _wait_until(lambda: any(
                candidate["expected"]["summary"] == "owner-A"
                for candidate in client_source.action_candidates().values()
            ))
            local_a = next(
                local_id for local_id, candidate in client_source.action_candidates().items()
                if candidate["expected"]["summary"] == "owner-A"
            )

            _stop_server(provider)
            provider = None
            await _wait_until(lambda: client_source.identity_status()["owner"] is None)
            assert client_source.action_candidate(local_a) is None
            provider = _start_server(address)
            owner_b = await _get_owner()
            assert owner_b != owner_a
            assert await _send_notify("owner-B-reused-349") == 349
            await _wait_until(lambda: any(
                candidate["expected"]["summary"] == "owner-B-reused-349"
                for candidate in client_source.action_candidates().values()
            ))
            local_b = next(
                local_id for local_id, candidate in client_source.action_candidates().items()
                if candidate["expected"]["summary"] == "owner-B-reused-349"
            )
            candidate_b = client_source.action_candidate(local_b)
            assert candidate_b["desktop_id"] == 349
            assert candidate_b["owner"] == owner_b
            assert candidate_b["pid"] == provider.pid
            assert local_b != local_a
            assert client_source.action_candidate(local_a) is None
            await _wait_until(lambda: local_a in archived)

            # Restart B immediately as C without waiting for the source to
            # settle at the empty-name state. The single control subscription
            # must continue routing both events through another reused ID 349.
            _stop_server(provider)
            provider = None
            provider = _start_server(address)
            owner_c = await _get_owner()
            assert owner_c not in {owner_a, owner_b}
            assert await _send_notify("owner-C-immediate") == 349
            await _wait_until(lambda: any(
                candidate["expected"]["summary"] == "owner-C-immediate"
                for candidate in client_source.action_candidates().values()
            ))
            local_c = next(
                local_id for local_id, candidate in client_source.action_candidates().items()
                if candidate["expected"]["summary"] == "owner-C-immediate"
            )
            candidate_c = client_source.action_candidate(local_c)
            assert candidate_c["desktop_id"] == 349
            assert candidate_c["owner"] == owner_c
            assert candidate_c["pid"] == provider.pid
            assert local_c not in {local_a, local_b}
            assert client_source.action_candidate(local_a) is None
            assert client_source.action_candidate(local_b) is None
            await _wait_until(lambda: local_b in archived)
        finally:
            if client_source is not None:
                await client_source.stop()
            if baseline is not None:
                baseline.disconnect()
            _stop_server(provider)
            broker.stop()

    require_broker = os.environ.get("STATUS349_REQUIRE_DBUS_BROKER") == "1"
    if not Path("/usr/bin/dbus-broker-launch").exists():
        if require_broker:
            pytest.fail("STATUS349_REQUIRE_DBUS_BROKER=1 but dbus-broker-launch is unavailable")
        pytest.skip("dbus-broker-launch is unavailable")

    with tempfile.TemporaryDirectory(prefix="status349-broker-") as temporary:
        asyncio.run(asyncio.wait_for(scenario(Path(temporary)), timeout=30))
