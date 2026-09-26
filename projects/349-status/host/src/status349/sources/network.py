"""Local host default-route and interface state."""

from __future__ import annotations

from pathlib import Path

_ROUTE_FLAG_UP = 0x1
_ROUTE_FLAG_REJECT = 0x200


class NetworkSource:
    """Report whether a local IPv4 or IPv6 default route has an active link.

    This reports route and link state only; it does not probe Internet access.
    Paths are injectable so the route parser can be exercised with fixtures.
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

    def read(self) -> dict:
        interfaces: list[str] = []
        sources_available = 0
        sources_unavailable = 0

        for path, parser in (
            (self._ipv4_routes, self._ipv4_defaults),
            (self._ipv6_routes, self._ipv6_defaults),
        ):
            try:
                contents = path.read_text(encoding="ascii")
            except OSError:
                sources_unavailable += 1
                continue
            sources_available += 1
            interfaces.extend(parser(contents))

        states = [self._interface_up(interface) for interface in interfaces]
        if any(state is True for state in states):
            return {"network": True}

        if any(state is None for state in states) or sources_unavailable:
            return {"network": None}
        if sources_available:
            return {"network": False}
        return {"network": None}

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
        except OSError:
            return None

        if state == "up":
            return True
        if state == "unknown":
            try:
                carrier = (self._net_root / interface / "carrier").read_text(encoding="ascii").strip()
            except OSError:
                return None
            if carrier == "1":
                return True
            if carrier == "0":
                return False
            return None
        if state in {"down", "dormant", "lowerlayerdown", "notpresent", "testing"}:
            return False
        return None
