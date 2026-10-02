"""The service must fail visibly and injected cards must expire."""

import asyncio
import gc
import threading
import time

import pytest

from status349.config import default_config
from status349.daemon import Daemon
from status349.ipc import IpcServer
from status349.link import LinkError


def test_failed_tick_exits_daemon_and_cleans_up():
    async def scenario():
        cfg = default_config()
        cfg.notifications.mode = "off"
        daemon = Daemon(cfg, asyncio.Event())
        cleaned = []

        async def ready():
            pass

        async def stop_notifications():
            cleaned.append("notifications")

        async def stop_ipc():
            cleaned.append("ipc")

        async def park():
            await asyncio.Future()

        async def fail():
            raise RuntimeError("tick failed")

        daemon.notifications.start = ready
        daemon.notifications.stop = stop_notifications
        daemon._ipc.start = ready
        daemon._ipc.stop = stop_ipc
        daemon._link_loop = park
        daemon._tick_loop = fail
        daemon._ping_loop = park

        with pytest.raises(RuntimeError, match="tick failed"):
            await asyncio.wait_for(daemon.run(), 1)
        assert cleaned == ["notifications", "ipc"]

    asyncio.run(scenario())


def test_shutdown_drains_the_uncancellable_telemetry_thread():
    async def scenario():
        cfg = default_config()
        cfg.notifications.mode = "off"
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop)
        entered = threading.Event()
        release = threading.Event()

        def slow_sample():
            entered.set()
            release.wait(2)
            return {"cpu": 0.5}

        daemon._sample = slow_sample

        async def ready():
            pass

        async def park():
            await asyncio.Future()

        daemon._ipc.start = ready
        daemon._ipc.stop = ready
        daemon.notifications.start = ready
        daemon.notifications.stop = ready
        daemon._link_loop = park
        daemon._ping_loop = park
        task = asyncio.create_task(daemon.run())
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            stop.set()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            await asyncio.wait_for(task, timeout=1)
            assert daemon._sample_task is None
            assert not daemon._sample_thread_lock.locked()
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_failed_notification_worker_exits_daemon():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())

        async def ready():
            pass

        async def park():
            await asyncio.Future()

        async def fail_monitor():
            raise RuntimeError("monitor failed")

        daemon._ipc.start = ready
        daemon._ipc.stop = ready
        daemon._link_loop = park
        daemon._tick_loop = park
        daemon._ping_loop = park
        daemon.notifications._monitor_loop = fail_monitor
        daemon.notifications._process_loop = park
        daemon.notifications._send_loop = park

        with pytest.raises(RuntimeError, match="notification source stopped unexpectedly"):
            await asyncio.wait_for(daemon.run(), 1)
        assert isinstance(daemon.notifications.failure, RuntimeError)
        assert str(daemon.notifications.failure) == "monitor failed"

    asyncio.run(scenario())


def test_ipc_ownership_releases_when_notification_shutdown_fails():
    async def handler(_request):
        return {"ok": True}

    async def scenario():
        stop = asyncio.Event()
        stop.set()
        daemon = Daemon(default_config(), stop)

        async def ready():
            pass

        async def broken_stop():
            raise RuntimeError("notification shutdown failed")

        async def park():
            await asyncio.Future()

        daemon.notifications.start = ready
        daemon.notifications.stop = broken_stop
        daemon._link_loop = park
        daemon._tick_loop = park
        daemon._ping_loop = park

        with pytest.raises(RuntimeError, match="notification shutdown failed"):
            await asyncio.wait_for(daemon.run(), 1)

        replacement = IpcServer(handler)
        await replacement.start()
        await replacement.stop()

    asyncio.run(scenario())


def test_cancelled_link_reader_consumes_completed_read_failure():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        loop = asyncio.get_running_loop()
        errors = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context["message"]))
        parent = None

        async def fail_read(_reader):
            parent.cancel()
            raise RuntimeError("serial read failed during shutdown")

        daemon._read_loop = fail_read
        try:
            parent = asyncio.create_task(daemon._read_until_link_change(object(), object(), 0))
            await asyncio.gather(parent, return_exceptions=True)
            assert parent.cancelled()
            # Drop the cancelled parent's traceback so uncollected child
            # exceptions reach the loop handler within this test.
            parent = None
            for _ in range(3):
                gc.collect()
                await asyncio.sleep(0)
            assert errors == []
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


def test_injected_notification_expires_and_sends_close():
    async def scenario():
        cfg = default_config()
        cfg.notifications.mode = "off"
        daemon = Daemon(cfg, asyncio.Event())
        sent = []

        async def capture(message):
            sent.append(message)
            return True

        daemon.send = capture
        daemon._sample = lambda: {}
        task = asyncio.create_task(daemon._tick_loop())
        try:
            reply = await daemon._ipc_handler({"cmd": "notify", "summary": "test", "expire": 20})
            nid = reply["id"]
            assert nid in daemon.model.notifs
            for _ in range(20):
                if nid not in daemon.model.notifs:
                    break
                await asyncio.sleep(0.02)
            assert nid not in daemon.model.notifs
            assert any(message.get("t") == "close" and message.get("id") == nid for message in sent)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_missing_port_uses_fast_discovery_even_with_long_open_backoff(monkeypatch):
    async def scenario():
        cfg = default_config()
        cfg.link.reconnect_min_s = 5.0
        cfg.link.reconnect_max_s = 10.0
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop)
        attempts = 0

        def missing_port():
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                stop.set()
            raise LinkError("absent")

        monkeypatch.setattr("status349.daemon.find_port", missing_port)
        started = time.monotonic()
        await asyncio.wait_for(daemon._link_loop(), 2)
        assert attempts == 2
        assert time.monotonic() - started < 1.5

    asyncio.run(scenario())
