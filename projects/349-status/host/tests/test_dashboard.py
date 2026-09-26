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
                "battery": {"level": 0.78, "charging": False},
                "volume": {"level": 0.33, "mute": False},
                "bluetooth": 2,
            },
            {
                "t": "dashboard",
                "cpu": 0.5,
                "mem": 0.25,
                "network": False,
                "battery": {"level": 0.78, "charging": False},
                "volume": {"level": 0.33, "mute": False},
                "bluetooth": 2,
            },
        ]

    asyncio.run(scenario())
