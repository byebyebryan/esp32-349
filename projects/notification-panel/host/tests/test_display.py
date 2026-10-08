"""Monitor power, capability negotiation and backlight policy transport."""

import asyncio

import pytest

from status349 import proto
from status349.config import apply_config, default_config, load_config, validate_config
from status349.daemon import Daemon
from status349.sources.screen_power import ScreenPowerSource


def connector(root, name, *, status="connected", enabled="enabled", dpms="On"):
    path = root / name
    path.mkdir()
    for key, value in (("status", status), ("enabled", enabled), ("dpms", dpms)):
        if value is not None:
            (path / key).write_text(value + "\n")
    return path


def test_screen_power_combines_active_monitors_and_ignores_closed_laptop_and_writeback(tmp_path):
    connector(tmp_path, "card2-DP-2")
    connector(tmp_path, "card2-DP-3", dpms="Off")
    connector(tmp_path, "card2-eDP-1", enabled="disabled", dpms="Off")
    connector(tmp_path, "card2-HDMI-A-1", status="disconnected")
    connector(tmp_path, "card0-Writeback-1", status="unknown")
    source = ScreenPowerSource(tmp_path)
    assert source.read()["on"] is True
    (tmp_path / "card2-DP-2" / "dpms").write_text("Off\n")
    assert source.read()["on"] is False
    (tmp_path / "card2-DP-3" / "dpms").write_text("On\n")
    assert source.read()["on"] is True


def test_screen_power_never_confuses_unavailable_state_with_off(tmp_path):
    source = ScreenPowerSource(tmp_path)
    assert source.read()["on"] is None
    first = connector(tmp_path, "card1-DP-1", dpms="Off")
    second = connector(tmp_path, "card1-DP-2", dpms=None)
    assert source.read()["on"] is None
    (first / "dpms").write_text("On\n")
    assert source.read()["on"] is True  # Known lit monitor dominates uncertainty.
    (first / "dpms").write_text("Off\n")
    (second / "dpms").write_text("unsupported\n")
    assert source.read()["on"] is None
    assert ScreenPowerSource(tmp_path / "missing").read()["on"] is None


@pytest.mark.parametrize("name,value", [
    ("brightness_percent", 0), ("brightness_percent", 101),
    ("brightness_percent", True), ("brightness_percent", 50.5),
    ("disconnect_timeout_s", 0), ("disconnect_timeout_s", 86401),
    ("disconnect_timeout_s", False), ("disconnect_timeout_s", float("inf")),
    ("follow_host_screen", "true"), ("follow_host_screen", 1),
])
def test_invalid_display_config(name, value):
    cfg = default_config()
    setattr(cfg.display, name, value)
    with pytest.raises(ValueError, match="display\\."):
        validate_config(cfg)


def test_display_config_defaults_and_reload(tmp_path):
    cfg = default_config()
    assert (cfg.display.brightness_percent, cfg.display.disconnect_timeout_s,
            cfg.display.follow_host_screen) == (50, 300, True)
    path = tmp_path / "config.toml"
    path.write_text("[display]\nbrightness_percent=65\ndisconnect_timeout_s=300\nfollow_host_screen=false\n")
    apply_config(cfg, load_config(str(path)))
    assert cfg.display.brightness_percent == 65
    assert cfg.display.follow_host_screen is False


def test_display_transport_is_capability_gated_changed_only_and_replayed_before_sync():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._latest_sample = {}
        sample = {"on": False, "outputs": [], "error": None}
        daemon.screen_power.read = lambda: dict(sample)
        sent = []

        async def capture(message):
            sent.append(message)
            return True

        daemon.send = capture
        daemon._write_message = capture
        await daemon._refresh_display()
        assert not sent  # Older firmware receives no unknown message type.
        await daemon._apply_hello_locked({"boot_id": 17, "cap": ["backlight-v1"]})
        assert sent[0] == {"t": "display", "on": False, "brightness": 50, "disconnect_s": 300}
        assert sent[1]["t"] == "sync"
        sent.clear()
        await daemon._refresh_display()
        assert not sent
        sample["on"] = True
        await daemon._refresh_display()
        assert [m["on"] for m in sent] == [True]
        sent.clear()
        sample["on"] = None
        await daemon._refresh_display()
        assert sent == [{"t": "display", "on": None, "brightness": 50, "disconnect_s": 300}]
        sent.clear()
        await daemon._send_sync_locked()
        assert sent[0]["t"] == "display"  # Timeout/reconnect can require fresh control.
        daemon.cfg.display.follow_host_screen = False
        daemon.cfg.display.brightness_percent = 65
        sent.clear()
        await daemon._refresh_display()
        assert sent == [{"t": "display", "on": True, "brightness": 65, "disconnect_s": 300}]

    asyncio.run(scenario())


def test_failed_display_write_is_retried_and_unavailable_source_is_reported():
    async def scenario():
        daemon = Daemon(default_config(), asyncio.Event())
        daemon._backlight_capable = True
        sample = {"on": None, "outputs": [], "error": "unavailable"}
        daemon.screen_power.read = lambda: sample
        sent = []

        async def fail_then_send(message):
            sent.append(message)
            return len(sent) > 1

        daemon.send = fail_then_send
        await daemon._refresh_display()
        assert daemon._last_display_sent is None
        await daemon._refresh_display()
        assert len(sent) == 2
        assert daemon._last_display_sent["on"] is None
        assert daemon._status()["display"]["host_screen"]["error"] == "unavailable"

    asyncio.run(scenario())


def test_backlight_readback_validates_without_breaking_older_peers():
    base = {"count": 0, "ids": [], "overflow": 0, "capacity": 32}
    assert "backlight" not in proto.card_status(base)
    power = {"percent": 0, "target_percent": 0, "brightness": 65, "disconnect_s": 300,
             "host_screen_on": False, "reason": "host_screen_off"}
    assert proto.card_status({**base, "backlight": power})["backlight"] == power
    for key, value in (("percent", -1), ("target_percent", 101), ("brightness", True),
                       ("disconnect_s", 0), ("host_screen_on", 0), ("reason", "unknown")):
        assert proto.card_status({**base, "backlight": {**power, key: value}}) is None


def test_local_button_readback_and_validation():
    base = {"count": 0, "ids": [], "overflow": 0, "capacity": 32}
    buttons = {"brightness_clicks": 4, "power_clicks": 1,
               "brightness_pressed": False, "power_pressed": True}
    power = {"percent": 0, "target_percent": 0, "brightness": 75, "disconnect_s": 300,
             "host_screen_on": True, "reason": "manual_off", "host_brightness": 50,
             "manual_off": True, "buttons": buttons}
    assert proto.card_status({**base, "backlight": power})["backlight"] == power
    for key, value in (("host_brightness", 0), ("host_brightness", True),
                       ("host_brightness", 101), ("manual_off", 1), ("buttons", [])):
        assert proto.card_status({**base, "backlight": {**power, key: value}}) is None
    for key, value in (("brightness_clicks", -1), ("power_clicks", 0x100000000),
                       ("power_clicks", True), ("brightness_pressed", 0),
                       ("power_pressed", "false")):
        assert proto.card_status({**base, "backlight": {
            **power, "buttons": {**buttons, key: value}}}) is None
    missing = dict(power)
    del missing["manual_off"]
    assert proto.card_status({**base, "backlight": missing}) is None
