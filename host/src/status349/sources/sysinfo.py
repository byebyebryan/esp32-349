"""One-second host samples: CPU load/current frequency and memory usage."""

from __future__ import annotations

import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)


class SysinfoSource:
    def __init__(self, proc_root: str | Path = "/proc") -> None:
        self._prev: tuple[int, int] | None = None
        self._proc = Path(proc_root)

    def read(self) -> dict:
        mem, used_bytes = self._memory()
        return {
            "cpu": self._cpu(),
            "cpu_freq_mhz": self._cpu_frequency(),
            "mem": mem,
            "mem_used_bytes": used_bytes,
        }

    def _cpu(self) -> float | None:
        try:
            with (self._proc / "stat").open(encoding="ascii") as fh:
                parts = fh.readline().split()
        except OSError:
            return None
        if not parts or parts[0] != "cpu":
            return None

        try:
            values = [int(x) for x in parts[1:]]
        except ValueError:
            return None
        if len(values) < 4:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        busy = total - idle

        if self._prev is None:
            self._prev = (busy, total)
            return None

        d_busy = busy - self._prev[0]
        d_total = total - self._prev[1]
        self._prev = (busy, total)
        if d_total <= 0:
            return None
        return max(0.0, min(1.0, d_busy / d_total))

    def _cpu_frequency(self) -> float | None:
        """Mean current MHz across the logical CPUs reported by /proc."""
        frequencies: list[float] = []
        try:
            with (self._proc / "cpuinfo").open(encoding="ascii") as fh:
                for line in fh:
                    key, _, value = line.partition(":")
                    if key.strip() != "cpu MHz":
                        continue
                    frequency = float(value.strip())
                    if not math.isfinite(frequency) or not 0 < frequency <= 100000:
                        return None
                    frequencies.append(frequency)
        except (OSError, ValueError):
            return None
        return round(math.fsum(frequencies) / len(frequencies), 1) if frequencies else None

    def _memory(self) -> tuple[float | None, int | None]:
        """Derive used bytes and fraction from one MemTotal/MemAvailable read."""
        info: dict[str, int] = {}
        try:
            with (self._proc / "meminfo").open(encoding="ascii") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    if key not in {"MemTotal", "MemAvailable"}:
                        continue
                    amount, unit = rest.split()
                    if unit != "kB":
                        return None, None
                    info[key] = int(amount) * 1024
        except (OSError, ValueError):
            return None, None

        total = info.get("MemTotal")
        available = info.get("MemAvailable")
        if total is None or total <= 0 or available is None or not 0 <= available <= total:
            return None, None
        used = total - available
        return used / total, used
