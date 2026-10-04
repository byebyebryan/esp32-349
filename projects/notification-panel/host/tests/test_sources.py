import subprocess
import time

import pytest

from status349.sources.bluetooth import BluetoothSource, parse_connected_count
from status349.sources.network import NetworkSource
from status349.sources.power import parse_power_supply
from status349.sources.sysinfo import SysinfoSource
from status349.sources.volume import parse_wpctl


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
    assert set(first) == {"cpu", "cpu_freq_mhz", "mem", "mem_used_bytes"}
    assert first["cpu"] is None  # no delta on the first sample
    assert first["mem"] is None or 0.0 <= first["mem"] <= 1.0

    time.sleep(0.02)
    second = source.read()
    assert second["cpu"] is None or 0.0 <= second["cpu"] <= 1.0


def test_sysinfo_frequency_and_memory_details_share_one_sample(tmp_path):
    (tmp_path / "stat").write_text("cpu 10 0 0 90 0\n")
    (tmp_path / "cpuinfo").write_text(
        "model name : CPU @ 9.9GHz\ncpu MHz : 800.0\ncpu MHz : 4000.0\n"
    )
    (tmp_path / "meminfo").write_text("MemTotal: 33554432 kB\nMemAvailable: 8388608 kB\n")
    source = SysinfoSource(tmp_path)
    assert source.read() == {
        "cpu": None, "cpu_freq_mhz": 2400.0,
        "mem": .75, "mem_used_bytes": 24 * 1024**3,
    }
    (tmp_path / "stat").write_text("cpu 15 0 0 95 0\n")
    (tmp_path / "cpuinfo").write_text("cpu MHz : 3500.1\ncpu MHz : 3500.3\n")
    (tmp_path / "meminfo").write_text("MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n")
    assert source.read() == {
        "cpu": .5, "cpu_freq_mhz": 3500.2,
        "mem": .5, "mem_used_bytes": 16 * 1024**3,
    }


@pytest.mark.parametrize("cpuinfo", [
    "", "model name : 3.5GHz\n", "cpu MHz : junk\n", "cpu MHz : nan\n",
    "cpu MHz : inf\n", "cpu MHz : 0\n", "cpu MHz : -1\n",
    "cpu MHz : 100001\n", "cpu MHz : 3500\ncpu MHz : junk\n",
])
def test_sysinfo_unavailable_frequency_does_not_hide_memory(tmp_path, cpuinfo):
    (tmp_path / "cpuinfo").write_text(cpuinfo)
    (tmp_path / "meminfo").write_text("MemTotal: 1024 kB\nMemAvailable: 1024 kB\n")
    value = SysinfoSource(tmp_path).read()
    assert value["cpu_freq_mhz"] is None
    assert value["mem"] == 0 and value["mem_used_bytes"] == 0


@pytest.mark.parametrize("meminfo", [
    "", "MemTotal: 1024 kB\n", "MemTotal: 0 kB\nMemAvailable: 0 kB\n",
    "MemTotal: 1024 kB\nMemAvailable: -1 kB\n",
    "MemTotal: 1024 kB\nMemAvailable: 1025 kB\n",
    "MemTotal: junk kB\nMemAvailable: 1 kB\n",
    "MemTotal: 1024 bytes\nMemAvailable: 1 kB\n",
    "MemTotal: 1024 kB extra\nMemAvailable: 1 kB\n",
])
def test_sysinfo_bad_memory_keeps_both_memory_fields_unavailable(tmp_path, meminfo):
    (tmp_path / "cpuinfo").write_text("cpu MHz : 3600\n")
    (tmp_path / "meminfo").write_text(meminfo)
    value = SysinfoSource(tmp_path).read()
    assert value["mem"] is None and value["mem_used_bytes"] is None
    assert value["cpu_freq_mhz"] == 3600


def test_sysinfo_missing_proc_files_are_unavailable(tmp_path):
    assert SysinfoSource(tmp_path).read() == {
        "cpu": None, "cpu_freq_mhz": None, "mem": None, "mem_used_bytes": None,
    }


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
    assert source.read() == {
        "network": True, "rx_bytes_per_s": None, "tx_bytes_per_s": None,
    }

    ipv4.write_text("Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n")
    ipv6.write_text(
        "0" * 32
        + " 00 "
        + "0" * 32
        + " 00 "
        + "0" * 32
        + " 00000000 00000000 00000000 00000001 eth0\n"
    )
    assert source.read()["network"] is True

    ipv6.write_text(
        "0" * 32
        + " 00 "
        + "0" * 32
        + " 00 "
        + "0" * 32
        + " 00000000 00000000 00000000 00000201 eth0\n"
    )
    assert source.read()["network"] is False


def test_network_source_distinguishes_no_route_from_unavailable(tmp_path):
    ipv4 = tmp_path / "route"
    ipv6 = tmp_path / "ipv6_route"
    net = tmp_path / "net"
    ipv4.write_text("Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n")
    ipv6.write_text("")
    source = NetworkSource(str(ipv4), str(ipv6), str(net))
    assert source.read()["network"] is False

    unavailable = NetworkSource(str(tmp_path / "missing4"), str(tmp_path / "missing6"), str(net))
    assert unavailable.read() == {
        "network": None, "rx_bytes_per_s": None, "tx_bytes_per_s": None,
    }

    (net / "eth0").mkdir(parents=True)
    (net / "eth0" / "operstate").write_text("down\n")
    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0003 0 0 100 00000000 0 0 0\n"
    )
    assert source.read()["network"] is False

    (net / "eth0" / "operstate").write_text("up\n")
    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0003 0 0 100 00000080 0 0 0\n"
    )
    assert source.read()["network"] is False

    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0201 0 0 100 00000000 0 0 0\n"
    )
    assert source.read()["network"] is False


def _write_routes(ipv4, ipv6, v4_interfaces, v6_interfaces=()):
    ipv4.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        + "".join(
            f"{name} 00000000 0100000A 0003 0 0 100 00000000 0 0 0\n"
            for name in v4_interfaces
        )
    )
    ipv6.write_text(
        "".join(
            "0" * 32 + " 00 " + "0" * 32 + " 00 " + "0" * 32
            + " 00000000 00000000 00000000 00000001 " + name + "\n"
            for name in v6_interfaces
        )
    )


def _physical_interface(net, name, rx=100, tx=200):
    interface = net / name
    (interface / "device").mkdir(parents=True)
    (interface / "statistics").mkdir()
    (interface / "operstate").write_text("up\n")
    (interface / "statistics" / "rx_bytes").write_text(f"{rx}\n")
    (interface / "statistics" / "tx_bytes").write_text(f"{tx}\n")
    return interface


def test_network_rates_deduplicate_dual_stack_and_sum_active_physical_uplinks(tmp_path):
    ipv4, ipv6, net = tmp_path / "route", tmp_path / "ipv6_route", tmp_path / "net"
    eth0 = _physical_interface(net, "eth0", rx=1000, tx=2000)
    wlan0 = _physical_interface(net, "wlan0", rx=3000, tx=4000)
    _write_routes(ipv4, ipv6, ["eth0", "wlan0"], ["eth0", "wlan0"])
    source = NetworkSource(str(ipv4), str(ipv6), str(net))

    first = source.read(now=10.0)
    assert first == {"network": True, "rx_bytes_per_s": None, "tx_bytes_per_s": None}
    (eth0 / "statistics" / "rx_bytes").write_text("1100\n")
    (eth0 / "statistics" / "tx_bytes").write_text("2020\n")
    (wlan0 / "statistics" / "rx_bytes").write_text("3200\n")
    (wlan0 / "statistics" / "tx_bytes").write_text("4040\n")
    assert source.read(now=11.0)["rx_bytes_per_s"] is None

    (eth0 / "statistics" / "rx_bytes").write_text("1200\n")
    (eth0 / "statistics" / "tx_bytes").write_text("2040\n")
    (wlan0 / "statistics" / "rx_bytes").write_text("3400\n")
    (wlan0 / "statistics" / "tx_bytes").write_text("4080\n")
    rates = source.read(now=12.0)
    assert rates == {"network": True, "rx_bytes_per_s": 300.0, "tx_bytes_per_s": 60.0}


def test_network_rates_report_zero_after_baseline_and_suppress_counter_reset(tmp_path):
    ipv4, ipv6, net = tmp_path / "route", tmp_path / "ipv6_route", tmp_path / "net"
    eth0 = _physical_interface(net, "eth0", rx=500, tx=900)
    _write_routes(ipv4, ipv6, ["eth0"])
    source = NetworkSource(str(ipv4), str(ipv6), str(net))

    assert source.read(now=0.0)["rx_bytes_per_s"] is None
    assert source.read(now=1.0)["rx_bytes_per_s"] is None
    assert source.read(now=2.0) == {"network": True, "rx_bytes_per_s": 0.0, "tx_bytes_per_s": 0.0}

    (eth0 / "statistics" / "rx_bytes").write_text("3\n")
    (eth0 / "statistics" / "tx_bytes").write_text("4\n")
    assert source.read(now=3.0)["rx_bytes_per_s"] is None
    assert source.read(now=4.0)["tx_bytes_per_s"] is None
    (eth0 / "statistics" / "rx_bytes").write_text("103\n")
    (eth0 / "statistics" / "tx_bytes").write_text("204\n")
    assert source.read(now=5.0) == {"network": True, "rx_bytes_per_s": 50.0, "tx_bytes_per_s": 100.0}


def test_network_rates_reset_on_interface_change_and_required_counter_failure(tmp_path):
    ipv4, ipv6, net = tmp_path / "route", tmp_path / "ipv6_route", tmp_path / "net"
    eth0 = _physical_interface(net, "eth0")
    wlan0 = _physical_interface(net, "wlan0", rx=1000, tx=2000)
    _write_routes(ipv4, ipv6, ["eth0", "wlan0"])
    source = NetworkSource(str(ipv4), str(ipv6), str(net))

    source.read(now=0.0)
    source.read(now=2.0)
    (wlan0 / "statistics" / "tx_bytes").unlink()
    assert source.read(now=3.0)["rx_bytes_per_s"] is None
    (wlan0 / "statistics" / "tx_bytes").write_text("2000\n")
    assert source.read(now=4.0)["rx_bytes_per_s"] is None
    assert source.read(now=6.0)["rx_bytes_per_s"] == 0.0

    _write_routes(ipv4, ipv6, ["eth0"])
    assert source.read(now=7.0)["rx_bytes_per_s"] is None
    assert source.read(now=9.0)["rx_bytes_per_s"] == 0.0

    (eth0 / "statistics" / "rx_bytes").write_text("not-a-counter\n")
    assert source.read(now=10.0)["rx_bytes_per_s"] is None
    (eth0 / "statistics" / "rx_bytes").write_text("100\n")
    assert source.read(now=11.0)["rx_bytes_per_s"] is None
    assert source.read(now=13.0)["rx_bytes_per_s"] == 0.0


def test_network_delayed_sample_uses_actual_elapsed_time_and_virtual_interfaces_are_excluded(tmp_path):
    ipv4, ipv6, net = tmp_path / "route", tmp_path / "ipv6_route", tmp_path / "net"
    phys = _physical_interface(net, "enp1s0", rx=100, tx=200)
    virtual = net / "br0"
    virtual.mkdir(parents=True)
    (virtual / "operstate").write_text("up\n")
    _write_routes(ipv4, ipv6, ["enp1s0", "br0"])
    source = NetworkSource(str(ipv4), str(ipv6), str(net))

    assert source.read(now=0.0)["rx_bytes_per_s"] is None
    (phys / "statistics" / "rx_bytes").write_text("200\n")
    (phys / "statistics" / "tx_bytes").write_text("300\n")
    assert source.read(now=1.0)["rx_bytes_per_s"] is None
    (phys / "statistics" / "rx_bytes").write_text("700\n")
    (phys / "statistics" / "tx_bytes").write_text("900\n")
    assert source.read(now=5.0) == {"network": True, "rx_bytes_per_s": 125.0, "tx_bytes_per_s": 150.0}


@pytest.mark.parametrize("broken", ["ipv4", "ipv6", "operstate", "carrier"])
def test_network_unreadable_text_returns_unavailable_and_restarts_baseline(tmp_path, broken):
    ipv4, ipv6, net = tmp_path / "route", tmp_path / "ipv6_route", tmp_path / "net"
    phys = _physical_interface(net, "eth0")
    _write_routes(ipv4, ipv6, ["eth0"])
    if broken == "carrier":
        (phys / "operstate").write_text("unknown\n")
        (phys / "carrier").write_text("1\n")
    path = {"ipv4": ipv4, "ipv6": ipv6,
            "operstate": phys / "operstate", "carrier": phys / "carrier"}[broken]
    original = path.read_bytes()
    source = NetworkSource(str(ipv4), str(ipv6), str(net))
    source.read(now=0.0)
    assert source.read(now=2.0)["rx_bytes_per_s"] == 0.0

    path.write_bytes(b"\xff\n")
    unavailable = source.read(now=3.0)
    assert unavailable["rx_bytes_per_s"] is None and unavailable["tx_bytes_per_s"] is None
    path.write_bytes(original)
    assert source.read(now=4.0)["rx_bytes_per_s"] is None
    assert source.read(now=6.0)["rx_bytes_per_s"] == 0.0


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
