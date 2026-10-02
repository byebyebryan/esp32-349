"""Reload applies live settings while preserving source ownership boundaries."""

import asyncio
import time

from dbus_next import Message

from status349.config import load_config
from status349.daemon import Daemon
from status349 import proto
from status349.fake import FakeDevice


def _wait_for(predicate, timeout=3.0):
    async def wait():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("condition not met in time")

    return wait()


def test_reload_changes_notification_mode_and_preserves_cli_port(tmp_path):
    cfg_path = tmp_path / "349d.toml"
    cfg_path.write_text(
        "[link]\nport = '/from/config'\n\n"
        "[notifications]\nmode = 'mirror'\nmax_visible = 3\ncache_limit = 8\n\n"
        "[daemon]\ntick_s = 0.05\nsync_interval_s = 0.5\n"
    )
    cfg = load_config(str(cfg_path))

    async def scenario():
        daemon = Daemon(cfg, asyncio.Event(), str(cfg_path), port_override="/from/cli")
        sent = []
        close_started = asyncio.Event()
        finish_close = asyncio.Event()

        async def capture(message):
            sent.append(message)
            if message.get("t") == "close" and message.get("id") == 1:
                close_started.set()
                await finish_close.wait()
            return True

        async def park_monitor():
            await asyncio.Future()

        daemon.send = capture
        daemon.notifications._monitor_loop = park_monitor
        await daemon.notifications.start()
        daemon.model.add_notification(proto.notify(1, "desktop", "mirrored", "body", 1, 5000, 1))
        daemon.model.add_notification(proto.notify(100000, "349ctl", "injected", "body", 1, 5000, 1))
        daemon.notifications._mirrored_local_ids.add(1)

        cfg_path.write_text(
            "[link]\nport = '/replacement-from-config'\n\n"
            "[notifications]\nmode = 'off'\nmax_visible = 2\ncache_limit = 4\n\n"
            "[daemon]\ntick_s = 0.2\nsync_interval_s = 0.8\n\n"
            "[bar]\npreset = [{ id = 'greet', kind = 'text', w = 80, text = 'hi' }]\n"
        )
        reload_task = asyncio.create_task(daemon.reload())
        await asyncio.wait_for(close_started.wait(), 1)
        daemon.notifications._messages.put_nowait(
            Message(
                destination="org.freedesktop.Notifications",
                path="/org/freedesktop/Notifications",
                interface="org.freedesktop.Notifications",
                member="Notify",
                signature="susssasa{sv}i",
                sender=":1.90",
                serial=1,
                body=["desktop", 0, "", "arrives during off transition", "body", [], {}, 5000],
            )
        )
        await asyncio.sleep(0.02)
        assert set(daemon.model.notifs) == {100000}
        finish_close.set()
        assert await reload_task

        assert daemon.cfg.link.port == "/from/cli"
        assert daemon.model.max_visible == 2
        assert daemon.model.cache_limit == 4
        assert set(daemon.model.notifs) == {100000}
        assert {message["id"] for message in sent if message["t"] == "close"} == {1}
        assert daemon.notifications._process_task is None
        assert daemon.notifications._messages.empty()
        assert daemon.notifications.cfg is daemon.cfg.notifications

        # A later valid notification still works after the close was retired.
        assert daemon.model.add_notification(proto.notify(2, "desktop", "new", "body", 1, 5000, 1))
        daemon.notifications._mirrored_local_ids.add(2)

        # Arrange inert loops so this test verifies mode reconciliation without
        # requiring a real session bus.
        async def park():
            await asyncio.Future()

        daemon.notifications._process_loop = park
        daemon.notifications._send_loop = park
        daemon.notifications._monitor_loop = park
        cfg_path.write_text(
            "[link]\nport = '/another-config-port'\n\n"
            "[notifications]\nmode = 'mirror'\nmax_visible = 2\n\n"
            "[daemon]\ntick_s = 0.2\nsync_interval_s = 0.8\n"
        )
        assert await daemon.reload()
        assert daemon.notifications._process_task is not None
        assert daemon.cfg.link.port == "/from/cli"
        assert daemon.model.cache_limit == 32
        await daemon.notifications.stop()

    asyncio.run(scenario())


def test_reload_rejects_invalid_file_and_keeps_previous_config(tmp_path):
    cfg_path = tmp_path / "349d.toml"
    cfg_path.write_text("[daemon]\ntick_s = 0.1\nsync_interval_s = 0.5\n")
    cfg = load_config(str(cfg_path))

    async def scenario():
        daemon = Daemon(cfg, asyncio.Event(), str(cfg_path), port_override="/cli-port")
        before = (cfg.daemon.tick_s, cfg.daemon.sync_interval_s, cfg.notifications.mode, cfg.link.port)
        cfg_path.write_text("[notifications]\nmax_visible = 9\n")
        assert not await daemon.reload()
        assert (cfg.daemon.tick_s, cfg.daemon.sync_interval_s, cfg.notifications.mode, cfg.link.port) == before
        assert daemon.cfg.link.port == "/cli-port"

    asyncio.run(scenario())


def test_tick_and_sync_intervals_take_effect_after_reload(tmp_path):
    cfg_path = tmp_path / "349d.toml"
    cfg_path.write_text(
        "[notifications]\nmode = 'off'\n\n[daemon]\ntick_s = 0.05\nsync_interval_s = 0.2\n"
    )
    cfg = load_config(str(cfg_path))

    async def scenario():
        daemon = Daemon(cfg, asyncio.Event(), str(cfg_path))
        sample_times = []
        sync_times = []

        def sample():
            sample_times.append(time.monotonic())
            return {}

        async def send_sync():
            sync_times.append(time.monotonic())

        daemon._sample = sample
        daemon._send_sync_locked = send_sync
        tick_task = asyncio.create_task(daemon._tick_loop())
        try:
            await _wait_for(lambda: len(sample_times) >= 3)
            cfg_path.write_text(
                "[notifications]\nmode = 'off'\n\n[daemon]\ntick_s = 0.2\nsync_interval_s = 0.8\n"
            )
            syncs_before_reload = len(sync_times)
            assert await daemon.reload()
            await _wait_for(lambda: len(sample_times) >= 6)
            post_reload_samples = sample_times[-3:]
            gaps = [b - a for a, b in zip(post_reload_samples, post_reload_samples[1:])]
            assert all(gap >= 0.15 for gap in gaps)

            await _wait_for(lambda: len(sync_times) >= syncs_before_reload + 2, timeout=2.0)
            assert 0.7 <= sync_times[-1] - sync_times[-2] <= 1.1
        finally:
            tick_task.cancel()
            try:
                await tick_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def _write_link_config(path, port, *, minimum=0.05, maximum=0.1):
    path.write_text(
        f"[link]\nport = {port!r}\nreconnect_min_s = {minimum}\nreconnect_max_s = {maximum}\n\n"
        "[daemon]\ntick_s = 0.05\nsync_interval_s = 0.5\n\n"
        "[notifications]\nmode = 'off'\n"
    )


def test_port_reload_reconnects_and_restores_retained_card(tmp_path):
    hello = {
        "t": "hello", "proto": 1,
        "cap": [
            "link", "bar", "card-sync-v1", "dashboard-v1", "grouped-ui-v1",
            "notification-history-v1",
        ],
        "cache_cards": 32, "boot_id": 1,
    }
    first = FakeDevice(hello).start()
    second = FakeDevice(dict(hello, boot_id=2)).start()
    cfg_path = tmp_path / "349d.toml"
    _write_link_config(cfg_path, first.path, minimum=5.0, maximum=5.0)
    cfg = load_config(str(cfg_path))

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop, str(cfg_path))
        task = asyncio.create_task(daemon.run())
        try:
            await _wait_for(
                lambda: daemon._active_port == first.path
                and any(message.get("t") == "sync_commit" for message in first.received)
            )
            status = daemon._status()
            assert status["port"] == first.path
            assert status["active_port"] == first.path

            await daemon._device_notify(proto.notify(31, "test", "retained", "body", 1, 5000, 1))
            await _wait_for(lambda: any(message.get("t") == "notify" for message in first.received))
            receipt = daemon.model.retained_received_mono[31]
            revision = daemon.model.retained_history_rev[31]
            initial_history = next(
                message["history"] for message in first.received
                if message.get("t") == "notify" and message.get("id") == 31
            )
            hello_count = sum(message.get("t") == "hello" for message in first.received)

            # A same-port reload may publish config changes, but it keeps the
            # current board connection and the card's original retention time.
            _write_link_config(cfg_path, first.path, minimum=5.0, maximum=5.0)
            assert await daemon.reload()
            await asyncio.sleep(0.15)
            assert sum(message.get("t") == "hello" for message in first.received) == hello_count
            assert daemon.model.retained_received_mono[31] == receipt

            missing = str(tmp_path / "missing-serial-device")
            _write_link_config(cfg_path, missing, minimum=5.0, maximum=5.0)
            assert await daemon.reload()
            await _wait_for(lambda: daemon._active_port is None and not daemon._status()["link"])
            assert daemon._status()["port"] == missing
            assert daemon._status()["active_port"] is None

            started = time.monotonic()
            _write_link_config(cfg_path, second.path, minimum=5.0, maximum=5.0)
            assert await daemon.reload()
            await _wait_for(
                lambda: daemon._active_port == second.path
                and any(
                    message.get("t") == "sync_cards"
                    and any(card.get("id") == 31 for card in message.get("notifs", []))
                    for message in second.received
                ),
                timeout=3.0,
            )
            assert time.monotonic() - started < 3.0
            assert 31 in daemon.model.retained_notifs
            assert daemon.model.retained_received_mono[31] == receipt
            assert daemon.model.retained_history_rev[31] == revision
            restored_history = next(
                card["history"] for message in second.received
                if message.get("t") == "sync_cards"
                for card in message["notifs"] if card["id"] == 31
            )
            assert restored_history["rev"] == revision
            assert restored_history["age_ms"] > initial_history["age_ms"]
            assert 0 < restored_history["remaining_ms"] < initial_history["remaining_ms"]
            assert daemon._status()["port"] == second.path
            assert daemon._status()["active_port"] == second.path
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())
    finally:
        first.stop()
        second.stop()


def test_cli_port_override_and_invalid_reload_keep_live_link(tmp_path):
    first = FakeDevice().start()
    other = FakeDevice().start()
    cfg_path = tmp_path / "349d.toml"
    _write_link_config(cfg_path, first.path)
    cfg = load_config(str(cfg_path))

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop, str(cfg_path), port_override=first.path)
        task = asyncio.create_task(daemon.run())
        try:
            await _wait_for(
                lambda: daemon._active_port == first.path
                and any(message.get("t") == "sync" for message in first.received)
            )
            initial_hellos = sum(message.get("t") == "hello" for message in first.received)

            _write_link_config(cfg_path, other.path)
            assert await daemon.reload()
            await asyncio.sleep(0.15)
            assert daemon.cfg.link.port == first.path
            assert daemon._status()["port"] == first.path
            assert daemon._status()["active_port"] == first.path
            assert sum(message.get("t") == "hello" for message in first.received) == initial_hellos
            assert not any(message.get("t") == "hello" for message in other.received)

            cfg_path.write_text("[link]\nreconnect_min_s = 5.0\nreconnect_max_s = 1.0\n")
            assert not await daemon.reload()
            assert daemon._status()["port"] == first.path
            assert daemon._status()["active_port"] == first.path
            assert sum(message.get("t") == "hello" for message in first.received) == initial_hellos
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())
    finally:
        first.stop()
        other.stop()


def test_port_reload_while_paused_waits_for_resume(tmp_path):
    first = FakeDevice().start()
    second = FakeDevice().start()
    cfg_path = tmp_path / "349d.toml"
    _write_link_config(cfg_path, first.path)
    cfg = load_config(str(cfg_path))

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop, str(cfg_path))
        task = asyncio.create_task(daemon.run())
        try:
            await _wait_for(
                lambda: daemon._active_port == first.path
                and any(message.get("t") == "sync" for message in first.received)
            )
            daemon.pause()
            await _wait_for(lambda: daemon._active_port is None and not daemon._status()["link"])

            _write_link_config(cfg_path, second.path)
            assert await daemon.reload()
            await asyncio.sleep(0.2)
            assert daemon._status()["paused"]
            assert daemon._status()["port"] == second.path
            assert daemon._status()["active_port"] is None
            assert not any(message.get("t") == "hello" for message in second.received)

            daemon.resume()
            await _wait_for(
                lambda: daemon._active_port == second.path
                and any(message.get("t") == "sync" for message in second.received)
            )
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())
    finally:
        first.stop()
        second.stop()


def test_retry_timing_reload_does_not_reconnect_healthy_link_and_applies_after_loss(
    tmp_path, monkeypatch
):
    cfg_path = tmp_path / "349d.toml"
    _write_link_config(cfg_path, "/fake-device", minimum=0.5, maximum=0.5)
    cfg = load_config(str(cfg_path))

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop, str(cfg_path))
        disconnected = asyncio.Event()
        opened = []

        class Writer:
            transport = object()

            def write(self, _payload):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

            async def wait_closed(self):
                pass

        writer = Writer()

        async def open_connection(**_kwargs):
            opened.append(time.monotonic())
            if len(opened) == 1:
                return object(), writer
            stop.set()
            raise OSError("fake open failure")

        async def read_connection(_reader):
            await disconnected.wait()

        monkeypatch.setattr("status349.daemon.serial_asyncio.open_serial_connection", open_connection)
        daemon._read_loop = read_connection
        task = asyncio.create_task(daemon._link_loop())
        try:
            await _wait_for(lambda: daemon._writer is writer)
            _write_link_config(cfg_path, "/fake-device", minimum=0.1, maximum=0.2)
            assert await daemon.reload()
            await asyncio.sleep(0.05)
            assert len(opened) == 1
            assert daemon._writer is writer

            disconnected_at = time.monotonic()
            disconnected.set()
            await _wait_for(lambda: len(opened) >= 2, timeout=1.0)
            assert opened[1] - disconnected_at < 0.4
        finally:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_reconfiguration_while_opening_never_installs_stale_port(tmp_path, monkeypatch):
    cfg_path = tmp_path / "349d.toml"
    _write_link_config(cfg_path, "/device-a")
    cfg = load_config(str(cfg_path))

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop, str(cfg_path))
        opening_a = asyncio.Event()
        release_a = asyncio.Event()
        opened = []

        class Reader:
            def __init__(self):
                self.closed = asyncio.Event()

        class Writer:
            def __init__(self, reader):
                self.reader = reader
                self.transport = object()
                self.closed = False
                self.messages = []

            def write(self, payload):
                self.messages.append(payload)

            async def drain(self):
                pass

            def close(self):
                self.closed = True
                self.reader.closed.set()

            async def wait_closed(self):
                pass

        reader_a, reader_b = Reader(), Reader()
        writer_a, writer_b = Writer(reader_a), Writer(reader_b)

        async def open_connection(*, url, **_kwargs):
            opened.append(url)
            if url == "/device-a":
                opening_a.set()
                await release_a.wait()
                return reader_a, writer_a
            return reader_b, writer_b

        async def read_connection(reader):
            await reader.closed.wait()

        monkeypatch.setattr("status349.daemon.serial_asyncio.open_serial_connection", open_connection)
        daemon._read_loop = read_connection
        task = asyncio.create_task(daemon._link_loop())
        try:
            await asyncio.wait_for(opening_a.wait(), 1)
            _write_link_config(cfg_path, "/device-b")
            assert await daemon.reload()
            release_a.set()

            await _wait_for(lambda: daemon._active_port == "/device-b")
            assert opened[:2] == ["/device-a", "/device-b"]
            assert writer_a.closed
            assert not writer_a.messages
            assert writer_b.messages
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert daemon._active_port is None
        assert daemon._writer is None
        assert not any(
            task.get_name() in {"serial-reader", "link-reconfigure"}
            for task in asyncio.all_tasks() if not task.done()
        )

    asyncio.run(scenario())
