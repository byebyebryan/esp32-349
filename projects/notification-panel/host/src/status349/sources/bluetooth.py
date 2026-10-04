"""Connected Bluetooth device count from bluetoothctl."""

from __future__ import annotations

import subprocess
import time

POLL_INTERVAL_S = 10.0
COMMAND_TIMEOUT_S = 0.3


def parse_connected_count(output: str) -> int:
    """Count connected-device rows without retaining their identifiers."""
    return sum(line.lstrip().startswith("Device ") for line in output.splitlines())


class BluetoothSource:
    def __init__(self) -> None:
        self._last_poll: float | None = None
        self._cached_count: int | None = None

    def read(self) -> dict:
        now = time.monotonic()
        if self._last_poll is not None and now - self._last_poll < POLL_INTERVAL_S:
            return {"bluetooth": self._cached_count}

        self._last_poll = now
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices", "Connected"],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            self._cached_count = None
            return {"bluetooth": None}

        if result.returncode != 0:
            self._cached_count = None
            return {"bluetooth": None}

        self._cached_count = parse_connected_count(result.stdout)
        return {"bluetooth": self._cached_count}
