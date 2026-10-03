"""Pairing commits and port ownership survive cancellation races."""

import asyncio
import os
import pty
import threading
from types import SimpleNamespace

import pytest

from status349 import daemon as daemon_module, discovery, pairing, proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.ipc import pause_path
from status349.link import LinkError


OLD = pairing.UsbIdentity("old-board")
NEW = pairing.UsbIdentity("new-board")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


class VerifiedProbe:
    path = "/dev/test-new"
    identity = NEW
    hello = {"t": "hello", "proto": 1, "fw": "test", "cap": ["link", "bar"]}

    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


async def wait_for(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


def test_unpaired_daemon_never_scans_or_opens(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())

        def forbidden(*args, **kwargs):
            raise AssertionError("ordinary unpaired startup must not discover")

        monkeypatch.setattr(discovery, "enumerate_candidates", forbidden)
        monkeypatch.setattr(discovery, "probe", forbidden)
        task = asyncio.create_task(daemon._link_loop())
        try:
            await wait_for(lambda: daemon._link_error is not None)
            assert "not paired" in daemon._link_error
            assert daemon._pairing_status()["state"] == "unpaired"
        finally:
            daemon.stop.set()
            daemon._link_wakeup.set()
            await task

    asyncio.run(scenario())


def test_real_open_descriptor_prevents_probe_before_open_or_write(monkeypatch):
    master, slave = pty.openpty()
    path = os.ttyname(slave)

    def forbidden(*args, **kwargs):
        raise AssertionError("busy target must not be opened")

    monkeypatch.setattr(discovery, "_open_serial", forbidden)

    async def scenario():
        with pytest.raises(discovery.DiscoveryError, match="already open"):
            await discovery.probe(path)
        assert os.getpid() in discovery._visible_port_owners(path)

    try:
        asyncio.run(scenario())
    finally:
        os.close(slave)
        os.close(master)


def test_absent_binding_waits_for_exact_serial_and_pair_is_idempotent(monkeypatch):
    pairing.PairingStore().save(OLD)

    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        requested = []

        def resolve(serial):
            requested.append(serial)
            return None

        async def forbidden(*args, **kwargs):
            raise AssertionError("absent paired board must not open another board")

        monkeypatch.setattr(discovery, "resolve_serial", resolve)
        monkeypatch.setattr(discovery, "probe", forbidden)
        reply = await daemon._start_pairing(False)
        assert reply == {"ok": True, "done": True, "serial": OLD.serial}
        task = asyncio.create_task(daemon._link_loop())
        try:
            await wait_for(lambda: bool(requested))
            assert set(requested) == {OLD.serial}
        finally:
            daemon.stop.set()
            daemon._link_wakeup.set()
            await task

    asyncio.run(scenario())


def test_paused_pairing_is_rejected_without_discovery(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        pause_path().touch()
        reply = await daemon._start_pairing(True)
        assert not reply["ok"] and "paused" in reply["error"]
        assert daemon._pair_task is None

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
def test_repeated_cancellation_reaps_save_and_respects_commit_point(after_commit):
    store = pairing.PairingStore()
    store.save(OLD)

    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._pair_cancel = threading.Event()
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        original_save = store.save

        def delayed_save(identity, before_commit):
            try:
                if after_commit:
                    original_save(identity, before_commit)
                entered.set()
                release.wait(2)
                if not after_commit:
                    original_save(identity, before_commit)
            finally:
                finished.set()

        store.save = delayed_save
        daemon._pair_store = store
        task = asyncio.create_task(daemon._save_pairing(NEW, daemon._pair_operation, 0))
        try:
            await wait_for(entered.is_set)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()  # pause followed by shutdown must not orphan fsync.
            await asyncio.sleep(0.01)
            assert not task.done()
            assert not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            expected = NEW if after_commit else OLD
            assert store.load() == expected
            assert daemon._paired_identity == expected
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_failed_replacement_preserves_binding_and_closes_candidate(monkeypatch):
    pairing.PairingStore().save(OLD)

    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        result = VerifiedProbe()
        monkeypatch.setattr(discovery, "enumerate_candidates", lambda: [result])

        async def probe(*args, **kwargs):
            return result

        def fail_save(*args, **kwargs):
            raise pairing.PairingError("disk full")

        monkeypatch.setattr(discovery, "probe", probe)
        monkeypatch.setattr(daemon._pair_store, "save", fail_save)
        reply = await daemon._start_pairing(True)
        assert reply["ok"]
        await daemon._pair_task
        assert "disk full" in daemon._pair_result["error"]
        assert daemon._paired_identity == OLD
        assert daemon._pair_store.load() == OLD
        assert result.closed == 1
        assert daemon._pending_probe is None

    asyncio.run(scenario())


def test_cancelled_replacement_closes_late_probe_without_saving(monkeypatch):
    pairing.PairingStore().save(OLD)

    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        result = VerifiedProbe()
        entered = asyncio.Event()
        monkeypatch.setattr(discovery, "enumerate_candidates", lambda: [result])

        async def probe(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return result  # Worker finished its handshake at cancellation.

        monkeypatch.setattr(discovery, "probe", probe)
        await daemon._start_pairing(True)
        await entered.wait()
        daemon._link_port_generation += 1
        daemon._cancel_pairing()
        await asyncio.gather(daemon._pair_task, return_exceptions=True)
        assert daemon._pair_store.load() == OLD
        assert daemon._pending_probe is None
        assert result.closed == 1

    asyncio.run(scenario())


def test_pause_releases_staged_verified_handle():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        result = VerifiedProbe()
        daemon._pending_probe = (result, 0)
        pause_path().touch()
        task = asyncio.create_task(daemon._link_loop())
        try:
            await wait_for(lambda: result.closed == 1)
            assert daemon._pending_probe is None
        finally:
            daemon.stop.set()
            daemon._link_wakeup.set()
            await task

    asyncio.run(scenario())


def test_pause_acknowledges_only_after_owned_port_is_released():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._link_idle.clear()
        result = VerifiedProbe()
        daemon._pending_probe = (result, 0)
        task = asyncio.create_task(daemon._ipc_handler({"cmd": "pause"}))
        try:
            await wait_for(lambda: result.closed == 1)
            assert not task.done()
            daemon._link_idle.set()
            assert await task == {"ok": True}
            assert daemon._pending_probe is None
        finally:
            daemon._link_idle.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_target_change_reaps_late_probe_through_repeated_cancellation(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        result = VerifiedProbe()
        entered = asyncio.Event()
        closing = asyncio.Event()
        release = asyncio.Event()

        async def close():
            closing.set()
            await release.wait()
            result.closed += 1

        result.close = close

        async def probe(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return result

        monkeypatch.setattr(discovery, "probe", probe)
        task = asyncio.create_task(daemon._probe_target(result.path, 0))
        try:
            await entered.wait()
            daemon._link_port_generation += 1
            daemon._link_wakeup.set()
            await closing.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert result.closed == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_active_writes_have_deadline_and_release_wire_lock(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())

        async def drain():
            await asyncio.Future()

        daemon._writer = SimpleNamespace(write=lambda data: None, drain=drain)
        monkeypatch.setattr(daemon_module, "WRITE_TIMEOUT_S", 0.02)
        assert not await asyncio.wait_for(daemon.send({"t": "ping", "ts": 1}), 0.2)
        assert not daemon._wire_lock.locked()
        assert daemon._link_failed

    asyncio.run(scenario())


def test_only_matching_numeric_pong_refreshes_liveness():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._last_pong_mono = 10
        daemon._monotonic = lambda: 20
        waiter = asyncio.get_running_loop().create_future()
        daemon._pong_waiters[123] = waiter
        for message in ["debug output", proto.encode({"t": "pong", "ts": True}).decode(),
                        proto.encode({"t": "pong", "ts": 124}).decode()]:
            await daemon._on_line(message)
        assert daemon._last_pong_mono == 10
        assert not waiter.done()
        await daemon._on_line(proto.encode({"t": "pong", "ts": 123.0}).decode())
        assert waiter.done()
        assert daemon._last_pong_mono == 20

    asyncio.run(scenario())


def test_debug_logs_do_not_prevent_keepalive_expiry(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._writer = object()
        daemon._last_pong_mono = 1
        daemon._monotonic = lambda: 20
        monkeypatch.setattr(daemon_module, "PING_INTERVAL_S", 0.001)

        async def no_pong():
            await daemon._on_line("device is still printing debug logs")
            raise LinkError("no matching pong")

        daemon._request_pong = no_pong
        task = asyncio.create_task(daemon._ping_loop())
        try:
            await wait_for(lambda: daemon._link_failed)
            assert daemon._last_rx_mono is not None
            assert daemon._last_pong_mono == 1
            assert "keepalive" in daemon._link_error
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
