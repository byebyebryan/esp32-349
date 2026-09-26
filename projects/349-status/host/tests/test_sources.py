import time
import subprocess

from status349.sources.power import parse_power_supply
from status349.sources.sysinfo import SysinfoSource
from status349.sources.volume import parse_wpctl
from status349.sources.bluetooth import BluetoothSource, parse_connected_count
from status349.sources.network import NetworkSource


def test_parse_wpctl():
    assert parse_wpctl("Volume: 0.42\n") == (0.42, False)
    assert parse_wpctl("Volume: 0.42 [MUTED]\n") == (0.42, True)
    assert parse_wpctl("garbage") == (None, False)


def test_parse_power_supply():
    assert parse_power_supply("78\n", "Charging\n") == (0.78, True)
    assert parse_power_supply("78\n", "Full\n") == (0.78, True)
    assert parse_power_supply("78\n", "Discharging\n") == (0.78, False)
    assert parse_power_supply("junk", "Charging") == (None, None)


def test_sysinfo_read():
    source = SysinfoSource()
    first = source.read()
    assert set(first) == {"cpu", "mem"}
    assert first["cpu"] is None  # no delta on the first sample
    assert first["mem"] is None or 0.0 <= first["mem"] <= 1.0

    time.sleep(0.02)
    second = source.read()
    assert second["cpu"] is None or 0.0 <= second["cpu"] <= 1.0


def test_network_source_reads_active_ipv4_and_ipv6_defaults(tmp_path):
    ipv4 = tmp_path / "route"
    ipv6 = tmp_path / "ipv6_route"
    net = tmp_path / "net"
    (net / "eth0").mkdir(parents=True)
    (net / "eth0" / "operstate").write_text("up\n")
    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0003 0 0 100 00000000 0 0 0\n"
    )
    ipv6.write_text("")
    source = NetworkSource(str(ipv4), str(ipv6), str(net))
    assert source.read() == {"network": True}

    ipv4.write_text("Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n")
    ipv6.write_text(
        "0" * 32
        + " 00 "
        + "0" * 32
        + " 00 "
        + "0" * 32
        + " 00000000 00000000 00000000 00000001 eth0\n"
    )
    assert source.read() == {"network": True}

    ipv6.write_text(
        "0" * 32
        + " 00 "
        + "0" * 32
        + " 00 "
        + "0" * 32
        + " 00000000 00000000 00000000 00000201 eth0\n"
    )
    assert source.read() == {"network": False}


def test_network_source_distinguishes_no_route_from_unavailable(tmp_path):
    ipv4 = tmp_path / "route"
    ipv6 = tmp_path / "ipv6_route"
    net = tmp_path / "net"
    ipv4.write_text("Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n")
    ipv6.write_text("")
    source = NetworkSource(str(ipv4), str(ipv6), str(net))
    assert source.read() == {"network": False}

    unavailable = NetworkSource(str(tmp_path / "missing4"), str(tmp_path / "missing6"), str(net))
    assert unavailable.read() == {"network": None}

    (net / "eth0").mkdir(parents=True)
    (net / "eth0" / "operstate").write_text("down\n")
    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0003 0 0 100 00000000 0 0 0\n"
    )
    assert source.read() == {"network": False}

    (net / "eth0" / "operstate").write_text("up\n")
    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0003 0 0 100 00000080 0 0 0\n"
    )
    assert source.read() == {"network": False}

    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0201 0 0 100 00000000 0 0 0\n"
    )
    assert source.read() == {"network": False}


def test_bluetooth_source_caches_count_and_uses_bounded_poll(monkeypatch):
    source = BluetoothSource()
    now = [0.0]
    calls = []

    class Result:
        returncode = 0
        stdout = "Device AA:BB:CC:DD:EE:FF Headphones\nDevice 11:22:33:44:55:66 Keyboard\n"

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr("status349.sources.bluetooth.time.monotonic", lambda: now[0])
    monkeypatch.setattr("status349.sources.bluetooth.subprocess.run", run)

    assert source.read() == {"bluetooth": 2}
    now[0] = 9.99
    assert source.read() == {"bluetooth": 2}
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == ["bluetoothctl", "devices", "Connected"]
    assert kwargs["timeout"] <= 0.3
    assert "AA:BB:CC:DD:EE:FF" not in str(source.read())

    now[0] = 10.0
    assert source.read() == {"bluetooth": 2}
    assert len(calls) == 2

    assert parse_connected_count("Device AA:BB:CC:DD:EE:FF Earbuds\nnoise\n") == 1


def test_bluetooth_source_returns_none_when_unavailable(monkeypatch):
    source = BluetoothSource()

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("bluetoothctl", 0.3)

    monkeypatch.setattr("status349.sources.bluetooth.subprocess.run", timeout)
    assert source.read() == {"bluetooth": None}
