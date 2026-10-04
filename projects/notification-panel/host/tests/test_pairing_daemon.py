"""Pairing and reconnect behavior with real PTYs and injected USB metadata."""

import asyncio
import json
import time
from types import SimpleNamespace

from status349 import __main__ as cli
from status349 import discovery, ipc
from status349.config import default_config, load_config
from status349.daemon import Daemon
from status349.fake import FakeDevice
from status349.pairing import PairingStore, UsbIdentity


async def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


async def _ipc(request):
    reader, writer = await asyncio.open_unix_connection(str(ipc.socket_path()))
    try:
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        return json.loads((await reader.readline()).decode())
    finally:
        writer.close()
        await writer.wait_closed()


def _port(fake, serial):
    return SimpleNamespace(
        device=fake.path,
        vid=0x303A,
        pid=0x1001,
        serial_number=serial,
        interface="Espressif USB JTAG/serial debug unit",
    )


def _write_config(path):
    path.write_text(
        "[link]\nreconnect_min_s = 0.05\nreconnect_max_s = 0.1\n\n"
        "[daemon]\ntick_s = 0.05\nsync_interval_s = 0.5\n\n"
        "[notifications]\nmode = 'off'\n"
    )


def test_pair_cli_commits_and_adopts_same_handle_then_pause_resume_reload(tmp_path, monkeypatch):
    fake = FakeDevice().start()
    calls = [0]
    probe_handles = []
    port_info = _port(fake, "board-opaque")

    def comports():
        calls[0] += 1
        return [port_info]

    monkeypatch.setattr(discovery.list_ports, "comports", comports)
    probe_real = discovery.probe

    async def recording_probe(path, identity=None):
        result = await probe_real(path, identity=identity)
        probe_handles.append(result.handle)
        return result

    monkeypatch.setattr(discovery, "probe", recording_probe)
    config_path = tmp_path / "349d.toml"
    _write_config(config_path)
    cfg = load_config(str(config_path))

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop, str(config_path))
        task = asyncio.create_task(daemon.run())
        socket_path = ipc.socket_path()
        try:
            await _wait_for(socket_path.exists)
            await _wait_for(lambda: "not paired" in (daemon._link_error or ""))
            assert fake.received == []  # Startup leaves an unpaired target untouched.

            result = await asyncio.to_thread(
                cli.main, ["--socket", str(socket_path), "pair", "--wait-timeout", "5"]
            )
            assert result == 0
            await _wait_for(lambda: daemon._writer is not None and daemon._active_port == fake.path)
            assert len(probe_handles) == 1
            assert daemon._session._serial is probe_handles[0]
            assert PairingStore().load() == UsbIdentity("board-opaque")
            saved = json.loads(PairingStore().path.read_text())
            assert saved == {"version": 1, "vid": "303a", "pid": "1001", "serial": "board-opaque"}
            assert sum(message.get("t") == "hello" for message in fake.received) == 1
            assert sum(message.get("t") == "ping" for message in fake.received) == 1

            enumerations_before_idempotent = calls[0]
            assert await asyncio.to_thread(cli.main, ["--socket", str(socket_path), "pair"]) == 0
            assert calls[0] == enumerations_before_idempotent

            # A failed replacement must leave the prior binding intact.
            monkeypatch.setattr(discovery.list_ports, "comports", lambda: [])
            assert await asyncio.to_thread(
                cli.main,
                ["--socket", str(socket_path), "pair", "--replace", "--wait-timeout", "5"],
            ) == 1
            assert PairingStore().load() == UsbIdentity("board-opaque")
            monkeypatch.setattr(discovery.list_ports, "comports", comports)
            await _wait_for(lambda: daemon._active_port == fake.path)

            assert (await _ipc({"cmd": "pause"}))["ok"]
            await _wait_for(lambda: daemon._writer is None and daemon._status()["paused"])
            assert (await _ipc({"cmd": "resume"}))["ok"]
            await _wait_for(lambda: daemon._active_port == fake.path)

            assert (await _ipc({"cmd": "reload"}))["ok"]
            await _wait_for(lambda: daemon._writer is not None and daemon._active_port == fake.path)
            assert PairingStore().load() == UsbIdentity("board-opaque")

            stop.set()
            await asyncio.wait_for(task, 5)
            assert daemon._session is None
            assert daemon._writer is None
        finally:
            if not task.done():
                stop.set()
                await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        fake.stop()


def test_paired_reconnect_uses_exact_serial_and_never_falls_back(tmp_path, monkeypatch):
    wrong = FakeDevice().start()
    right = FakeDevice().start()
    wrong_info = _port(wrong, "board-wrong")
    right_info = _port(right, "board-right")
    metadata = [wrong_info, right_info]
    monkeypatch.setattr(discovery.list_ports, "comports", lambda: metadata)
    enumerate_real = discovery.enumerate_candidates

    def wrong_first(**kwargs):
        candidates = enumerate_real(**kwargs)
        return sorted(candidates, key=lambda item: item.identity.serial != "board-wrong")

    monkeypatch.setattr(discovery, "enumerate_candidates", wrong_first)
    PairingStore().save(UsbIdentity("board-right"))
    cfg = default_config()
    cfg.notifications.mode = "off"

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop)
        task = asyncio.create_task(daemon.run())
        try:
            await _wait_for(lambda: daemon._active_port == right.path)
            assert sum(message.get("t") == "hello" for message in right.received) == 1
            assert wrong.received == []

            # The bound board disappears while another compatible USB tty is
            # still present. Reconnect stays idle instead of opening it.
            right.stop()
            metadata[:] = [wrong_info]
            await _wait_for(lambda: daemon._writer is None)
            await _wait_for(lambda: "paired device is not connected" in (daemon._link_error or ""))
            assert wrong.received == []
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())
    finally:
        wrong.stop()
        right.stop()


def test_setup_pair_skips_wrong_first_device_hello(tmp_path, monkeypatch):
    wrong = FakeDevice({
        "t": "hello", "proto": 2, "fw": "not-349", "cap": ["link", "bar"],
    }).start()
    right = FakeDevice().start()
    wrong_info = _port(wrong, "board-incompatible")
    right_info = _port(right, "board-compatible")
    monkeypatch.setattr(discovery.list_ports, "comports", lambda: [wrong_info, right_info])
    enumerate_real = discovery.enumerate_candidates

    def wrong_first(**kwargs):
        candidates = enumerate_real(**kwargs)
        return sorted(candidates, key=lambda item: item.identity.serial != "board-incompatible")

    monkeypatch.setattr(discovery, "enumerate_candidates", wrong_first)
    monkeypatch.setattr(discovery, "ATTEMPT_TIMEOUT_S", 0.25)
    cfg = default_config()
    cfg.notifications.mode = "off"

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop)
        task = asyncio.create_task(daemon.run())
        try:
            await _wait_for(lambda: "not paired" in (daemon._link_error or ""))
            result = await asyncio.to_thread(
                cli.main, ["--socket", str(ipc.socket_path()), "pair", "--wait-timeout", "5"]
            )
            assert result == 0
            await _wait_for(lambda: daemon._active_port == right.path)
            assert PairingStore().load() == UsbIdentity("board-compatible")
            assert [message.get("t") for message in wrong.received] == ["hello"]
            assert sum(message.get("t") == "hello" for message in right.received) == 1
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())
    finally:
        wrong.stop()
        right.stop()


def test_pairing_active_explicit_target_reuses_verified_session(tmp_path, monkeypatch):
    fake = FakeDevice().start()
    monkeypatch.setattr(discovery.list_ports, "comports", lambda: [_port(fake, "board-explicit")])
    config_path = tmp_path / "349d-explicit.toml"
    _write_config(config_path)
    cfg = load_config(str(config_path))
    cfg.link.port = fake.path

    async def scenario():
        stop = asyncio.Event()
        daemon = Daemon(cfg, stop, str(config_path), port_override=fake.path)
        task = asyncio.create_task(daemon.run())
        try:
            await _wait_for(lambda: daemon._session is not None and daemon._active_port == fake.path)
            session = daemon._session
            serial_handle = session._serial
            initial_hellos = sum(message.get("t") == "hello" for message in fake.received)
            initial_pings = sum(message.get("t") == "ping" for message in fake.received)
            assert initial_hellos == 1

            result = await asyncio.to_thread(
                cli.main, ["--socket", str(ipc.socket_path()), "pair", "--wait-timeout", "5"]
            )
            assert result == 0
            assert daemon._session is session
            assert daemon._session._serial is serial_handle
            assert PairingStore().load() == UsbIdentity("board-explicit")
            assert sum(message.get("t") == "hello" for message in fake.received) == initial_hellos
            assert sum(message.get("t") == "ping" for message in fake.received) == initial_pings + 1
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())
    finally:
        fake.stop()
