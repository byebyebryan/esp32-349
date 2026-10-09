"""Serialized telemetry collection, dashboard smoothing and screen following.

Internal methods assembled by Daemon; its shared model and locks remain the
single authority for cross-worker ordering and lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass

from . import proto

log = logging.getLogger("349d")
DASHBOARD_CPU_EMA_TAU_S = 3.0


@dataclass(frozen=True, slots=True)
class _TelemetrySample:
    generation: int
    values: dict
    completed_mono: float


class TelemetryMixin:
    """Serialized telemetry collection, dashboard smoothing and screen following."""

    def _sample(self) -> dict:
        values: dict = {}
        values.update(self.sysinfo.read())
        values.update(self.volume.read())
        values.update(self.power.read())
        values.update(self.network.read())
        values.update(self.bluetooth.read())
        return values

    def _monotonic(self) -> float:
        return time.monotonic()

    def _sample_serialized(self) -> dict:
        # Shielded collection survives cancellation of a tick or link task.
        # Keep the lock at the thread boundary as a final guard against source
        # overlap while an executor call is still finishing.
        with self._sample_thread_lock:
            return self._sample()

    async def _collect_sample_worker(self, generation: int) -> _TelemetrySample:
        values = await asyncio.to_thread(self._sample_serialized)
        return _TelemetrySample(generation, values, self._monotonic())

    async def _collect_sample(self) -> _TelemetrySample:
        """Return one shared in-flight sample without blocking the event loop."""
        task = self._sample_task
        if task is None:
            self._sample_generation += 1
            task = asyncio.create_task(
                self._collect_sample_worker(self._sample_generation), name="telemetry-sample"
            )
            self._sample_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The executor thread cannot be stopped. Keep its task published so
            # a later caller waits for this same collection instead of starting
            # another one over stateful sources.
            raise
        except Exception:
            if self._sample_task is task:
                self._sample_task = None
            raise

    def _publish_sample_locked(self, sample: _TelemetrySample) -> bool:
        """Atomically install one completed sample and its next deadline."""
        previous = self._latest_sample_result
        if previous is sample or (previous is not None and sample.generation <= previous.generation):
            return False
        self._latest_sample_result = sample
        self._latest_sample = sample.values
        self._latest_sample_mono = sample.completed_mono
        self._next_sample_mono = sample.completed_mono + float(self.cfg.daemon.tick_s)
        return True

    def _finish_sample_locked(self, sample: _TelemetrySample) -> None:
        task = self._sample_task
        if task is not None and task.done() and not task.cancelled() and task.exception() is None:
            if task.result() is sample:
                self._sample_task = None

    async def _prime_sample(self) -> None:
        """Establish the first shared sample before callers take state lock."""
        async with self._state_lock:
            if self._latest_sample is not None:
                return
        sample = await self._collect_sample()
        async with self._state_lock:
            if self._latest_sample is None and self._publish_sample_locked(sample):
                await self._update_dashboard_locked(
                    sample.values, sampled_mono=sample.completed_mono, emit_delta=False
                )
                self._finish_sample_locked(sample)

    async def _drain_sample_worker(self) -> None:
        """Wait for the uncancellable source thread before daemon teardown."""
        task = self._sample_task
        if task is None:
            return
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("telemetry sampling failed during shutdown")
        finally:
            if self._sample_task is task:
                self._sample_task = None

    @staticmethod
    def _ratio(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            numeric = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return max(0.0, min(1.0, numeric))

    def _dashboard_payload(self, values: dict, *, sampled_mono: float | None = None) -> dict:
        now = time.monotonic() if sampled_mono is None else sampled_mono
        cpu = self._ratio(values.get("cpu"))
        if cpu is None:
            self._cpu_ema = None
            self._cpu_ema_mono = None
            smoothed_cpu = None
        elif self._cpu_ema is None or self._cpu_ema_mono is None:
            self._cpu_ema = cpu
            self._cpu_ema_mono = now
            smoothed_cpu = cpu
        else:
            elapsed = max(0.0, now - self._cpu_ema_mono)
            alpha = -math.expm1(-elapsed / DASHBOARD_CPU_EMA_TAU_S)
            self._cpu_ema += alpha * (cpu - self._cpu_ema)
            self._cpu_ema_mono = now
            smoothed_cpu = self._cpu_ema

        battery_level = self._ratio(values.get("batt"))
        volume_level = self._ratio(values.get("vol"))
        return {
            "cpu": smoothed_cpu,
            "cpu_freq_mhz": values.get("cpu_freq_mhz"),
            "mem": values.get("mem"),
            "mem_used_bytes": values.get("mem_used_bytes"),
            "network": values.get("network"),
            "rx_bytes_per_s": values.get("rx_bytes_per_s"),
            "tx_bytes_per_s": values.get("tx_bytes_per_s"),
            "battery": (
                {"level": battery_level, "charging": values.get("charging")}
                if battery_level is not None
                else None
            ),
            "volume": (
                {"level": volume_level, "mute": values.get("mute")}
                if volume_level is not None
                else None
            ),
            "bluetooth": values.get("bluetooth"),
        }

    async def _update_dashboard_locked(
        self, values: dict, *, sampled_mono: float | None = None, emit_delta: bool = True
    ) -> bool:
        """Update dashboard state while holding ``_state_lock``."""
        changed = self.model.set_dashboard(self._dashboard_payload(values, sampled_mono=sampled_mono))
        if changed and emit_delta and self._dashboard_capable:
            await self.send(proto.dashboard_message(self.model.dashboard))
        return changed

    def _display_payload(self) -> dict:
        message = {
            "t": "display",
            "on": self._screen_power_sample["on"] if self.cfg.display.follow_host_screen else True,
            "brightness": self.cfg.display.brightness_percent,
            "disconnect_s": self.cfg.display.disconnect_timeout_s,
        }
        if self._backlight_boost_capable:
            message["boost_s"] = self.cfg.display.notification_boost_s
        return message

    async def _refresh_display(self) -> None:
        sample = await asyncio.to_thread(self.screen_power.read)
        async with self._state_lock:
            self._screen_power_sample = sample
            message = self._display_payload()
            if self._backlight_capable and message != self._last_display_sent:
                if await self.send(message):
                    self._last_display_sent = message

    async def _display_loop(self) -> None:
        while True:
            await self._refresh_display()
            await asyncio.sleep(1.0)
