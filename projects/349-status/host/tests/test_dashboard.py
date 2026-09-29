"""Dashboard telemetry contract, sanitization, and smoothing tests."""

import asyncio

from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon


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
        "mem": None,
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
            "mem": 0.25,
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
                "mem": 0.25,
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
                "mem": 0.25,
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

        async def settle():
            for _ in range(4):
                await asyncio.sleep(0)

        async def wake(at):
            now[0] = at
            daemon._tick_wakeup.set()
            await settle()

        try:
            await settle()
            assert samples == [0.0]  # Startup full sync establishes one baseline.
            assert daemon._next_sample_mono == 1.0

            await wake(0.2)
            await wake(0.65)
            assert samples == [0.0]
            await daemon._send_sync()  # A non-periodic sync reuses the latest sample.
            assert samples == [0.0]
            assert daemon._next_sample_mono == 1.0

            await wake(0.999)
            assert samples == [0.0]
            await wake(1.0)
            assert samples == [0.0, 1.0]

            await wake(4.5)  # A delayed tick takes one sample, with no catch-up burst.
            assert samples == [0.0, 1.0, 4.5]
            assert daemon._next_sample_mono == 5.5
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_status_includes_only_the_latest_sanitized_rail_dashboard():
    daemon = Daemon(default_config(), asyncio.Event())
    daemon.model.set_dashboard({
        "cpu": 0.5,
        "mem": 0.25,
        "network": True,
        "rx_bytes_per_s": 12_345.5,
        "tx_bytes_per_s": True,
        "interface": "private-name",
    })

    assert daemon._status()["dashboard"] == {
        "cpu": 0.5,
        "mem": 0.25,
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
            for _ in range(8):
                await asyncio.sleep(0)
            assert samples == [0.0]
            now[0] = 1.0
            daemon._tick_wakeup.set()
            for _ in range(8):
                await asyncio.sleep(0)
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

        async def wake(at):
            now[0] = at
            daemon._tick_wakeup.set()
            for _ in range(8):
                await asyncio.sleep(0)

        try:
            await wake(0.0)
            assert samples == [0.0]
            daemon.cfg.daemon.sync_interval_s = 30.0
            daemon._needs_sync = True
            await wake(0.2)
            assert daemon._next_sample_mono == 1.0
            daemon.cfg.daemon.tick_s = 2.0
            daemon._needs_sync = True
            await wake(0.3)
            assert samples == [0.0]
            assert daemon._next_sample_mono == 2.3
            await wake(1.0)
            assert samples == [0.0]
            await wake(2.3)
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
        monkeypatch.setattr(daemon, "_monotonic", lambda: now[0])
        daemon._sample = lambda: {"cpu": 0.5, "mem": 0.25 if now[0] < 1 else 0.75}

        async def capture(message):
            sent.append(message)
            return True

        async def queue_sync(_now):
            if now[0] >= 1 and not queued:
                queued.append(asyncio.create_task(daemon._send_sync()))
                await asyncio.sleep(0)  # Full sync waits behind the held state lock.

        daemon._write_message = capture
        daemon._expire_presentation_locked = queue_sync
        task = asyncio.create_task(daemon._tick_loop())
        try:
            for _ in range(8):
                await asyncio.sleep(0)
            now[0] = 1.0
            daemon._tick_wakeup.set()
            for _ in range(8):
                await asyncio.sleep(0)
            assert len(queued) == 1
            await queued[0]
            begin = [message for message in sent if message["t"] == "sync_begin"][-1]
            mem_zone = next(zone for zone in begin["bar"]["zones"] if zone["id"] == "mem")
            assert begin["dashboard"]["mem"] == mem_zone["value"] == 0.75
        finally:
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
