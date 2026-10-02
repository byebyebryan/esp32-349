"""Dashboard telemetry contract, sanitization, and smoothing tests."""

import asyncio
import threading

import pytest

from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon, _TelemetrySample


async def _wait_until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


def test_sample_generation_rejects_delayed_older_result_at_equal_time():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        newer = _TelemetrySample(2, {"mem": 0.75}, 10.0)
        older = _TelemetrySample(1, {"mem": 0.25}, 10.0)
        async with daemon._state_lock:
            assert daemon._publish_sample_locked(newer)
            assert not daemon._publish_sample_locked(older)
        assert daemon._latest_sample == {"mem": 0.75}
        assert daemon._latest_sample_result is newer
        assert daemon._next_sample_mono == 10.0 + daemon.cfg.daemon.tick_s

    asyncio.run(scenario())


def test_dashboard_payload_has_nullable_finite_identifier_free_fields():
    payload = proto.dashboard_payload(
        {
            "cpu": float("nan"),
            "mem": True,
            "network": "online",
            "rx_bytes_per_s": True,
            "tx_bytes_per_s": float("inf"),
            "battery": {"level": float("inf"), "charging": "yes"},
            "volume": {"level": 1.7, "mute": True},
            "bluetooth": False,
            "interface": "wlan0",
        }
    )

    assert payload == {
        "cpu": None,
        "cpu_freq_mhz": None,
        "mem": None,
        "mem_used_bytes": None,
        "network": None,
        "rx_bytes_per_s": None,
        "tx_bytes_per_s": None,
        "battery": None,
        "volume": {"level": 1.0, "mute": True},
        "bluetooth": None,
    }
    assert proto.dashboard_message(payload) == {"t": "dashboard", **payload}
    assert b"wlan0" not in proto.encode(proto.dashboard_message(payload))
    assert proto.dashboard_payload({"bluetooth": 1000})["bluetooth"] is None


def test_dashboard_cpu_uses_time_aware_three_second_ema(monkeypatch):
    daemon = Daemon(default_config(), asyncio.Event())
    current = [0.0]
    monkeypatch.setattr("status349.daemon.time.monotonic", lambda: current[0])

    first = proto.dashboard_payload(daemon._dashboard_payload({"cpu": 0.0}))
    current[0] = 3.0
    second = proto.dashboard_payload(daemon._dashboard_payload({"cpu": 1.0}))
    current[0] = 6.0
    third = proto.dashboard_payload(daemon._dashboard_payload({"cpu": 1.0}))

    assert first["cpu"] == 0.0
    assert second["cpu"] == 0.63
    assert 0.85 <= third["cpu"] <= 0.88


@pytest.mark.parametrize("field,valid,invalid", [
    ("cpu_freq_mhz", (0, 3600.5, proto.DASHBOARD_CPU_FREQ_MAX_MHZ),
     (True, -1, 100001, "3600", float("nan"), float("inf"), 10**1000)),
    ("mem_used_bytes", (0, 24 * 1024**3, proto.DASHBOARD_MEMORY_MAX_BYTES),
     (True, -1, 1.5, "24G", float("nan"), float("inf"), 2**50 + 1, 10**1000)),
])
def test_dashboard_details_validate_payload_and_optional_device_readback(field, valid, invalid):
    base = {"count": 0, "overflow": 0, "ids": [], "capacity": 32}
    dashboard = {"cpu": None, "mem": None, "network": None,
                 "rx_bytes_per_s": None, "tx_bytes_per_s": None}
    # Previous firmware need not include the two new optional fields.
    assert proto.card_status({**base, "dashboard": dashboard})["dashboard"] == dashboard
    for value in (*valid, None):
        assert proto.dashboard_payload({field: value})[field] == value
        result = proto.card_status({**base, "dashboard": {**dashboard, field: value}})
        assert result["dashboard"][field] == value
    for value in invalid:
        assert proto.dashboard_payload({field: value})[field] is None
        assert proto.card_status({**base, "dashboard": {**dashboard, field: value}}) is None


def test_dashboard_delta_is_emitted_only_when_quantized_payload_changes():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._dashboard_capable = True
        sent = []

        async def capture(message):
            sent.append(message)
            return True

        daemon.send = capture
        first = {
            "cpu": 0.504,
            "cpu_freq_mhz": 3600.5,
            "mem": 0.25,
            "mem_used_bytes": 8 * 1024**3,
            "network": True,
            "rx_bytes_per_s": 2500.0,
            "tx_bytes_per_s": 0.0,
            "batt": 0.78,
            "charging": False,
            "vol": 0.33,
            "mute": False,
            "bluetooth": 2,
        }
        async with daemon._state_lock:
            assert await daemon._update_dashboard_locked(first)
            assert not await daemon._update_dashboard_locked({**first, "cpu": 0.503})
            assert await daemon._update_dashboard_locked({**first, "network": False})

        assert sent == [
            {
                "t": "dashboard",
                "cpu": 0.5,
                "cpu_freq_mhz": 3600.5,
                "mem": 0.25,
                "mem_used_bytes": 8 * 1024**3,
                "network": True,
                "rx_bytes_per_s": 2500.0,
                "tx_bytes_per_s": 0.0,
                "battery": {"level": 0.78, "charging": False},
                "volume": {"level": 0.33, "mute": False},
                "bluetooth": 2,
            },
            {
                "t": "dashboard",
                "cpu": 0.5,
                "cpu_freq_mhz": 3600.5,
                "mem": 0.25,
                "mem_used_bytes": 8 * 1024**3,
                "network": False,
                "rx_bytes_per_s": 2500.0,
                "tx_bytes_per_s": 0.0,
                "battery": {"level": 0.78, "charging": False},
                "volume": {"level": 0.33, "mute": False},
                "bluetooth": 2,
            },
        ]

    asyncio.run(scenario())


def test_tick_sampling_deadline_ignores_event_and_full_sync_wakeups(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon.cfg.daemon.sync_interval_s = 30.0
        daemon._needs_sync = True
        now = [0.0]
        samples = []

        monkeypatch.setattr(daemon, "_monotonic", lambda: now[0])

        def sample():
            samples.append(now[0])
            return {
                "cpu": 0.5,
                "mem": 0.25,
                "network": True,
                "rx_bytes_per_s": 100.0,
                "tx_bytes_per_s": 50.0,
            }

        daemon._sample = sample
        task = asyncio.create_task(daemon._tick_loop())

        async def wake(at, generation):
            now[0] = at
            daemon._tick_wakeup.set()
            await _wait_until(lambda: not daemon._tick_wakeup.is_set())
            if daemon._needs_sync:
                await _wait_until(lambda: not daemon._needs_sync)
            if generation is not None:
                await _wait_until(
                    lambda: daemon._latest_sample_result is not None
                    and daemon._latest_sample_result.generation == generation
                    and daemon._sample_task is None
                )

        try:
            await _wait_until(
                lambda: daemon._latest_sample_result is not None
                and daemon._latest_sample_result.generation == 1
                and daemon._sample_task is None
                and not daemon._needs_sync
            )
            assert samples == [0.0]  # Startup full sync establishes one baseline.
            assert daemon._next_sample_mono == 1.0

            await wake(0.2, None)
            await wake(0.65, None)
            assert samples == [0.0]
            await daemon._send_sync()  # A non-periodic sync reuses the latest sample.
            assert samples == [0.0]
            assert daemon._next_sample_mono == 1.0

            await wake(0.999, None)
            assert samples == [0.0]
            await wake(1.0, 2)
            assert samples == [0.0, 1.0]

            await wake(4.5, 3)  # A delayed tick takes one sample, with no catch-up burst.
            assert samples == [0.0, 1.0, 4.5]
            assert daemon._next_sample_mono == 5.5
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_sampling_runs_off_loop_and_leaves_state_and_ipc_responsive():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def slow_sample():
            calls.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return {"cpu": 0.5, "mem": 0.25}

        daemon._sample = slow_sample
        tick = asyncio.create_task(daemon._tick_loop())
        try:
            await _wait_until(entered.is_set)
            status = await asyncio.wait_for(daemon._ipc_handler({"cmd": "status"}), timeout=0.2)
            assert status["ok"]
            await asyncio.wait_for(
                daemon._device_notify(proto.notify(1, "test", "while sampling", "body", 1, -1, 1)),
                timeout=0.2,
            )
            assert 1 in daemon.model.notifs
            await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.2)
            assert len(calls) == 1
        finally:
            release.set()
            tick.cancel()
            await asyncio.gather(tick, return_exceptions=True)
            await daemon._drain_sample_worker()

    asyncio.run(scenario())


def test_cancelled_sample_waiter_reuses_one_uncancellable_collection():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        entered = threading.Event()
        release = threading.Event()
        active = 0
        max_active = 0
        calls = 0

        def slow_sample():
            nonlocal active, max_active, calls
            calls += 1
            active += 1
            max_active = max(max_active, active)
            entered.set()
            try:
                release.wait(2)
                return {"cpu": 0.25}
            finally:
                active -= 1

        daemon._sample = slow_sample
        first = asyncio.create_task(daemon._collect_sample())
        try:
            await _wait_until(entered.is_set)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

            second = asyncio.create_task(daemon._collect_sample())
            await asyncio.sleep(0.01)
            assert calls == 1
            assert max_active == 1
            release.set()
            sample = await asyncio.wait_for(second, timeout=1)
            async with daemon._state_lock:
                assert daemon._publish_sample_locked(sample)
                daemon._finish_sample_locked(sample)
            assert sample.values == {"cpu": 0.25}
            assert daemon._sample_task is None
            assert calls == max_active == 1
        finally:
            release.set()
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await daemon._drain_sample_worker()

    asyncio.run(scenario())


def test_status_includes_only_the_latest_sanitized_rail_dashboard():
    daemon = Daemon(default_config(), asyncio.Event())
    daemon.model.set_dashboard({
        "cpu": 0.5,
        "cpu_freq_mhz": 3600.5,
        "mem": 0.25,
        "mem_used_bytes": 8 * 1024**3,
        "network": True,
        "rx_bytes_per_s": 12_345.5,
        "tx_bytes_per_s": True,
        "interface": "private-name",
    })

    assert daemon._status()["dashboard"] == {
        "cpu": 0.5,
        "cpu_freq_mhz": 3600.5,
        "mem": 0.25,
        "mem_used_bytes": 8 * 1024**3,
        "network": True,
        "rx_bytes_per_s": 12_345.5,
        "tx_bytes_per_s": None,
    }


def test_slow_sample_does_not_trigger_an_immediate_second_refresh(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._needs_sync = True
        now = [0.0]
        samples = []
        monkeypatch.setattr(daemon, "_monotonic", lambda: now[0])

        def sample():
            samples.append(now[0])
            if len(samples) == 2:
                now[0] += 2.5  # One source takes longer than the one-second tick.
            return {"cpu": 0.5, "mem": len(samples) / 10}

        daemon._sample = sample
        task = asyncio.create_task(daemon._tick_loop())
        try:
            await _wait_until(
                lambda: daemon._latest_sample_result is not None
                and daemon._latest_sample_result.generation == 1
                and daemon._sample_task is None
            )
            assert samples == [0.0]
            now[0] = 1.0
            daemon._tick_wakeup.set()
            await _wait_until(
                lambda: daemon._latest_sample_result is not None
                and daemon._latest_sample_result.generation == 2
                and daemon._sample_task is None
            )
            assert samples == [0.0, 1.0]
            assert daemon._next_sample_mono == 4.5
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_interval_changes_do_not_resample_on_full_sync(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._needs_sync = True
        now = [0.0]
        samples = []
        monkeypatch.setattr(daemon, "_monotonic", lambda: now[0])

        def sample():
            samples.append(now[0])
            return {"cpu": 0.5, "mem": 0.25}

        daemon._sample = sample
        task = asyncio.create_task(daemon._tick_loop())

        async def wake(at, generation):
            now[0] = at
            daemon._tick_wakeup.set()
            await _wait_until(lambda: not daemon._tick_wakeup.is_set())
            if daemon._needs_sync:
                await _wait_until(lambda: not daemon._needs_sync)
            if generation is not None:
                await _wait_until(
                    lambda: daemon._latest_sample_result is not None
                    and daemon._latest_sample_result.generation == generation
                    and daemon._sample_task is None
                )

        try:
            await wake(0.0, 1)
            assert samples == [0.0]
            daemon.cfg.daemon.sync_interval_s = 30.0
            daemon._needs_sync = True
            await wake(0.2, None)
            assert daemon._next_sample_mono == 1.0
            daemon.cfg.daemon.tick_s = 2.0
            daemon._needs_sync = True
            await wake(0.3, None)
            assert samples == [0.0]
            assert daemon._next_sample_mono == 2.3
            await wake(1.0, None)
            assert samples == [0.0]
            await wake(2.3, 2)
            assert samples == [0.0, 2.3]
            assert daemon._next_sample_mono == 4.3
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_queued_sync_cannot_mix_new_zones_with_old_dashboard(monkeypatch):
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._needs_sync = True
        daemon._dashboard_capable = True
        daemon._card_sync_capacity = 32
        now = [0.0]
        sent = []
        queued = []
        sample_started = threading.Event()
        release_sample = threading.Event()
        first_sync = asyncio.Event()
        monkeypatch.setattr(daemon, "_monotonic", lambda: now[0])

        def sample():
            if now[0] >= 1:
                sample_started.set()
                release_sample.wait(2)
            return {
                "cpu": 0.5, "cpu_freq_mhz": 800.0 if now[0] < 1 else 3600.0,
                "mem": 0.25 if now[0] < 1 else 0.75,
                "mem_used_bytes": (8 if now[0] < 1 else 24) * 1024**3,
            }

        daemon._sample = sample

        async def capture(message):
            sent.append(message)
            if message["t"] == "sync_commit":
                first_sync.set()
            return True

        async def queue_sync(_now):
            if now[0] >= 1 and not queued:
                queued.append(asyncio.create_task(daemon._send_sync()))
                await asyncio.sleep(0)  # Full sync waits behind the held state lock.

        daemon._write_message = capture
        daemon._expire_presentation_locked = queue_sync
        task = asyncio.create_task(daemon._tick_loop())
        try:
            await asyncio.wait_for(first_sync.wait(), timeout=2)
            await _wait_until(lambda: daemon._next_sample_mono == 1.0)
            assert daemon.model.dashboard["mem"] == 0.25
            assert next(zone["value"] for zone in daemon.model.zones if zone["id"] == "mem") == 0.25

            now[0] = 1.0
            daemon._tick_wakeup.set()
            await _wait_until(sample_started.is_set)
            await _wait_until(lambda: len(queued) == 1)
            await asyncio.wait_for(queued[0], timeout=2)
            assert not release_sample.is_set()

            old_begin = [message for message in sent if message["t"] == "sync_begin"][-1]
            old_zone = next(zone for zone in old_begin["bar"]["zones"] if zone["id"] == "mem")
            assert old_begin["dashboard"]["mem"] == old_zone["value"] == 0.25

            release_sample.set()
            await _wait_until(
                lambda: daemon.model.dashboard["mem"] == 0.75
                and next(zone["value"] for zone in daemon.model.zones if zone["id"] == "mem") == 0.75
            )
            assert daemon._next_sample_mono == 2.0

            await daemon._send_sync()
            assert len(queued) == 1
            begin = [message for message in sent if message["t"] == "sync_begin"][-1]
            mem_zone = next(zone for zone in begin["bar"]["zones"] if zone["id"] == "mem")
            assert begin["dashboard"]["mem"] == mem_zone["value"] == 0.75
            assert begin["dashboard"]["mem_used_bytes"] == 24 * 1024**3
            assert begin["dashboard"]["cpu_freq_mhz"] == 3600.0
        finally:
            release_sample.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            for sync in queued:
                if not sync.done():
                    sync.cancel()
                    try:
                        await sync
                    except asyncio.CancelledError:
                        pass

    asyncio.run(scenario())
