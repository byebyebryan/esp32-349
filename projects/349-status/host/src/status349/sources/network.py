"""Local host default-route and interface state."""

from __future__ import annotations

import re
import time
from collections import deque
from pathlib import Path

_ROUTE_FLAG_UP = 0x1
_ROUTE_FLAG_REJECT = 0x200
_RATE_WINDOW_S = 2.0
_RATE_MAX_BPS = 1_000_000_000_000
_COUNTER_MAX = 0xFFFFFFFFFFFFFFFF
_MAX_UPLINKS = 32
_SAMPLE_HISTORY = 64
_COUNTER_TEXT = re.compile(r"[0-9]+\Z")


class NetworkSource:
    """Report default-route link state and physical-uplink byte rates.

    Rates sum unique active physical default-route interfaces over at least two
    seconds. This does not probe Internet access. Paths are injectable so link
    selection and counter behavior can be exercised with fixtures.
    """

    def __init__(
        self,
        ipv4_routes: str = "/proc/net/route",
        ipv6_routes: str = "/proc/net/ipv6_route",
        net_root: str = "/sys/class/net",
    ) -> None:
        self._ipv4_routes = Path(ipv4_routes)
        self._ipv6_routes = Path(ipv6_routes)
        self._net_root = Path(net_root)
        self._counter_interfaces: tuple[str, ...] | None = None
        self._counter_history: deque[tuple[float, dict[str, tuple[int, int]]]] = deque(
            maxlen=_SAMPLE_HISTORY
        )

    def read(self, now: float | None = None) -> dict:
        interfaces: list[str] = []
        sources_available = 0
        sources_unavailable = 0

        for path, parser in (
            (self._ipv4_routes, self._ipv4_defaults),
            (self._ipv6_routes, self._ipv6_defaults),
        ):
            try:
                contents = path.read_text(encoding="ascii")
            except (OSError, UnicodeError):
                sources_unavailable += 1
                continue
            sources_available += 1
            interfaces.extend(parser(contents))

        states = [self._interface_up(interface) for interface in interfaces]
        if any(state is True for state in states):
            network = True
        elif any(state is None for state in states) or sources_unavailable:
            network = None
        elif sources_available:
            network = False
        else:
            network = None

        sample_time = time.monotonic() if now is None else now
        rx_rate, tx_rate = self._traffic_rates(
            interfaces,
            route_sources_complete=(sources_available == 2 and sources_unavailable == 0),
            now=sample_time,
        )
        return {
            "network": network,
            "rx_bytes_per_s": rx_rate,
            "tx_bytes_per_s": tx_rate,
        }

    def _traffic_rates(
        self, interfaces: list[str], *, route_sources_complete: bool, now: float
    ) -> tuple[float | None, float | None]:
        if not route_sources_complete:
            self._reset_counters()
            return None, None

        active: list[str] = []
        for interface in dict.fromkeys(interfaces):
            if not self._valid_interface_name(interface):
                continue
            if not (self._net_root / interface / "device").exists():
                continue
            state = self._interface_up(interface)
            if state is None:
                self._reset_counters()
                return None, None
            if state:
                active.append(interface)
                if len(active) > _MAX_UPLINKS:
                    self._reset_counters()
                    return None, None

        if not active:
            self._reset_counters()
            return None, None

        names = tuple(sorted(active))
        if names != self._counter_interfaces:
            self._counter_interfaces = names
            self._counter_history.clear()

        counters: dict[str, tuple[int, int]] = {}
        try:
            for interface in names:
                stats = self._net_root / interface / "statistics"
                counters[interface] = (
                    self._read_counter(stats / "rx_bytes"),
                    self._read_counter(stats / "tx_bytes"),
                )
        except (OSError, UnicodeError, ValueError):
            self._reset_counters()
            return None, None

        if self._counter_history:
            previous = self._counter_history[-1][1]
            if any(
                current[direction] < previous[interface][direction]
                for interface, current in counters.items()
                for direction in (0, 1)
            ):
                self._counter_history.clear()

        self._counter_history.append((now, counters))
        baseline = next(
            (
                sample
                for sample in reversed(self._counter_history)
                if now - sample[0] >= _RATE_WINDOW_S
            ),
            None,
        )
        if baseline is None:
            return None, None

        elapsed = now - baseline[0]
        rx_bytes = sum(counters[name][0] - baseline[1][name][0] for name in names)
        tx_bytes = sum(counters[name][1] - baseline[1][name][1] for name in names)
        rx_rate, tx_rate = rx_bytes / elapsed, tx_bytes / elapsed
        if rx_rate > _RATE_MAX_BPS or tx_rate > _RATE_MAX_BPS:
            return None, None
        return rx_rate, tx_rate

    def _reset_counters(self) -> None:
        self._counter_interfaces = None
        self._counter_history.clear()

    @staticmethod
    def _valid_interface_name(interface: str) -> bool:
        return (
            0 < len(interface.encode("utf-8", "ignore")) <= 15
            and "/" not in interface
            and interface not in {".", ".."}
        )

    @staticmethod
    def _read_counter(path: Path) -> int:
        raw = path.read_text(encoding="ascii").strip()
        if not _COUNTER_TEXT.fullmatch(raw):
            raise ValueError("invalid network byte counter")
        value = int(raw)
        if value > _COUNTER_MAX:
            raise ValueError("network byte counter exceeds uint64")
        return value

    @staticmethod
    def _ipv4_defaults(contents: str) -> list[str]:
        interfaces: list[str] = []
        for line in contents.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 8 or fields[1] != "00000000" or fields[7] != "00000000":
                continue
            try:
                flags = int(fields[3], 16)
            except ValueError:
                continue
            if (flags & _ROUTE_FLAG_UP) and not (flags & _ROUTE_FLAG_REJECT) and fields[0]:
                interfaces.append(fields[0])
        return interfaces

    @staticmethod
    def _ipv6_defaults(contents: str) -> list[str]:
        interfaces: list[str] = []
        for line in contents.splitlines():
            fields = line.split()
            if len(fields) < 10 or fields[1] != "00" or fields[0] != "0" * 32:
                continue
            try:
                flags = int(fields[8], 16)
            except ValueError:
                continue
            if (flags & _ROUTE_FLAG_UP) and not (flags & _ROUTE_FLAG_REJECT) and fields[9]:
                interfaces.append(fields[9])
        return interfaces

    def _interface_up(self, interface: str) -> bool | None:
        try:
            state = (self._net_root / interface / "operstate").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None

        if state == "up":
            return True
        if state == "unknown":
            try:
                carrier = (self._net_root / interface / "carrier").read_text(encoding="ascii").strip()
            except (OSError, UnicodeError):
                return None
            if carrier == "1":
                return True
            if carrier == "0":
                return False
            return None
        if state in {"down", "dormant", "lowerlayerdown", "notpresent", "testing"}:
            return False
        return None
