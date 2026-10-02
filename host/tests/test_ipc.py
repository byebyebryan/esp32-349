"""End-to-end IPC tests: daemon + fake device + Unix socket control."""

import asyncio
import json
import socket
import time

import pytest

from status349 import ipc
from status349.config import default_config
from status349.daemon import Daemon
from status349.fake import FakeDevice


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def _wait_status(path, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await _ipc({"cmd": "status"}, path)
        if last.get("ok") and predicate(last):
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"status condition not met: {last}")


async def _ipc(request: dict, path) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        return json.loads(line.decode())
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _cfg(tmp_path, fake):
    cfg_path = tmp_path / "349d.toml"
    cfg_path.write_text("[bar]\npreset = [{ id = 'clock', kind = 'clock', w = 80 }]\n")
    cfg = default_config()
    cfg.daemon.tick_s = 0.05
    cfg.daemon.sync_interval_s = 0.5
    return cfg, cfg_path


def test_ipc_commands_and_sticky_pause(tmp_path):
    fake = FakeDevice().start()
    try:
        cfg, cfg_path = _cfg(tmp_path, fake)

        async def scenario():
            stop = asyncio.Event()
            daemon = Daemon(cfg, stop, str(cfg_path), port_override=fake.path)
            task = asyncio.create_task(daemon.run())
            path = ipc.socket_path()
            await _wait_for(path.exists)
            await _wait_for(lambda: any(m["t"] == "sync" for m in fake.received))

            status = await _wait_status(path, lambda s: s["link"])
            assert status["config"] == str(cfg_path)

            assert (await _ipc({"cmd": "text", "value": "hi"}, path))["ok"]
            await _wait_for(lambda: any(m["t"] == "text" and m.get("v") == "hi" for m in fake.received))

            note = await _ipc({"cmd": "notify", "summary": "ipc test", "body": "b"}, path)
            assert note["ok"]
            await _wait_for(lambda: any(m["t"] == "notify" and m["summary"] == "ipc test" for m in fake.received))

            log = await _ipc({"cmd": "log", "lines": 10}, path)
            assert log["ok"] and isinstance(log["lines"], list)

            cfg_path.write_text(
                "[bar]\npreset = [{ id = 'greet', kind = 'text', w = 80, text = 'hi' }]\n"
            )
            assert (await _ipc({"cmd": "reload"}, path))["ok"]
            assert (await _ipc({"cmd": "status"}, path))["port"] == fake.path
            await _wait_for(
                lambda: any(
                    m["t"] == "sync" and any(z.get("id") == "greet" for z in m["bar"]["zones"])
                    for m in fake.received
                )
            )

            assert (await _ipc({"cmd": "pause"}, path))["ok"]
            await _wait_status(path, lambda s: s["paused"] and not s["link"])

            stop.set()
            await asyncio.wait_for(task, 5)

        asyncio.run(scenario())

        # Pause is sticky: a fresh daemon must stay off the port.
        async def restarted():
            stop = asyncio.Event()
            daemon = Daemon(cfg, stop, str(cfg_path), port_override=fake.path)
            task = asyncio.create_task(daemon.run())
            path = ipc.socket_path()
            await _wait_for(path.exists)
            await _wait_status(path, lambda s: s["paused"] and not s["link"])

            assert (await _ipc({"cmd": "resume"}, path))["ok"]
            await _wait_status(path, lambda s: not s["paused"] and s["link"])

            stop.set()
            await asyncio.wait_for(task, 5)

        asyncio.run(restarted())
    finally:
        fake.stop()


def test_duplicate_ipc_start_and_stop_leave_owner_endpoint_intact():
    async def handler(_request):
        return {"ok": True, "owner": 1}

    async def scenario():
        owner = ipc.IpcServer(handler)
        contender = ipc.IpcServer(handler)
        await owner.start()
        path = ipc.socket_path()
        owner_identity = (path.stat().st_dev, path.stat().st_ino)

        with pytest.raises(RuntimeError, match="already running"):
            await contender.start()
        await contender.stop()

        assert (path.stat().st_dev, path.stat().st_ino) == owner_identity
        assert await _ipc({"cmd": "status"}, path) == {"ok": True, "owner": 1}
        await owner.stop()
        assert not path.exists()

    asyncio.run(scenario())


def test_existing_lockless_listener_is_refused_without_unlinking_socket():
    async def handler(reader, writer):
        await reader.readline()
        writer.write(b'{"ok":true}\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def scenario():
        path = ipc.socket_path()
        listener = await asyncio.start_unix_server(handler, path=str(path))
        identity = (path.stat().st_dev, path.stat().st_ino)
        contender = ipc.IpcServer(lambda _request: asyncio.sleep(0, result={"ok": True}))
        try:
            with pytest.raises(RuntimeError, match="listener already owns"):
                await contender.start()
            await contender.stop()
            assert (path.stat().st_dev, path.stat().st_ino) == identity
            assert await _ipc({"cmd": "status"}, path) == {"ok": True}
        finally:
            listener.close()
            await listener.wait_closed()
            path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_stale_socket_recovery_keeps_stable_lock_inode():
    async def handler(_request):
        return {"ok": True}

    async def scenario():
        path = ipc.socket_path()
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()

        first = ipc.IpcServer(handler)
        await first.start()
        lock_identity = (ipc.lock_path().stat().st_dev, ipc.lock_path().stat().st_ino)
        await first.stop()
        assert not path.exists()
        assert (ipc.lock_path().stat().st_dev, ipc.lock_path().stat().st_ino) == lock_identity

        second = ipc.IpcServer(handler)
        await second.start()
        assert (ipc.lock_path().stat().st_dev, ipc.lock_path().stat().st_ino) == lock_identity
        await second.stop()

    asyncio.run(scenario())


def test_ipc_cleanup_preserves_replacement_socket_and_regular_file():
    async def handler(_request):
        return {"ok": True}

    async def noop(_reader, writer):
        writer.close()
        await writer.wait_closed()

    async def scenario():
        path = ipc.socket_path()
        owner = ipc.IpcServer(handler)
        await owner.start()
        path.unlink()
        replacement = await asyncio.start_unix_server(noop, path=str(path))
        replacement_identity = (path.stat().st_dev, path.stat().st_ino)
        await owner.stop()
        assert (path.stat().st_dev, path.stat().st_ino) == replacement_identity
        replacement.close()
        await replacement.wait_closed()
        path.unlink(missing_ok=True)

        path.write_text("ordinary file")
        refused = ipc.IpcServer(handler)
        with pytest.raises(RuntimeError, match="non-socket"):
            await refused.start()
        await refused.stop()
        assert path.read_text() == "ordinary file"

    asyncio.run(scenario())
