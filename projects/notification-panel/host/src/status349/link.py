"""USB-Serial-JTAG transport for the 349-status display."""

from __future__ import annotations

import glob
import time
from collections.abc import Iterator

import serial

from .proto import classify, encode

PORT_PATTERNS = (
    "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00",
    "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*",
)


class LinkError(RuntimeError):
    pass


def find_port() -> str:
    for pattern in PORT_PATTERNS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    raise LinkError("device not found: no Espressif USB-Serial-JTAG port")


def reset_to_normal_boot(port: serial.Serial) -> None:
    """Request a normal reboot using USB Serial/JTAG control lines.

    Reset is explicit: opening with both DTR and RTS asserted need not reset
    the S3. RTS asserted with DTR deasserted requests reset; deasserting both
    clears the download mode flag (ESP32-S3 TRM Table 33.3-2).
    """
    try:
        port.dtr = False
        port.rts = True  # RTS asserted / DTR deasserted: reset
        time.sleep(0.1)
        port.rts = False  # Both deasserted: clear download mode flag
        time.sleep(0.05)
        port.dtr = False
    except (OSError, serial.SerialException):
        pass


def open_port(path: str | None = None) -> serial.Serial:
    # This direct-command path deliberately resets the target after opening.
    # Deasserting the lines on open can also pass through the S3 reset
    # combination as PySerial updates DTR before RTS. A discovery probe should
    # keep both asserted and avoid resetting an unverified candidate.
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2)
    port.dtr = False
    port.rts = False
    port.port = path or find_port()
    try:
        port.open()
    except serial.SerialException as exc:
        raise LinkError(str(exc)) from exc
    reset_to_normal_boot(port)
    return port


class Link:
    def __init__(self, port: serial.Serial):
        self.port = port
        self._buf = bytearray()

    def __enter__(self) -> Link:
        return self

    def __exit__(self, *exc: object) -> None:
        self.port.close()

    def send(self, obj: dict) -> None:
        self.port.write(encode(obj))

    def lines(self, timeout: float = 0.2) -> Iterator[str]:
        """Yield complete lines received within `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while True:
            while (idx := self._buf.find(b"\n")) >= 0:
                raw = bytes(self._buf[:idx])
                del self._buf[: idx + 1]
                yield raw.decode("utf-8", "replace").rstrip("\r")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.port.timeout = min(remaining, 0.2)
            chunk = self.port.read(256)
            if not chunk:
                continue
            self._buf.extend(chunk)


__all__ = [
    "Link",
    "LinkError",
    "classify",
    "encode",
    "find_port",
    "open_port",
    "reset_to_normal_boot",
]
