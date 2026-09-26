"""349d - host daemon: sources -> state model -> USB-Serial-JTAG.

The device never polls. The daemon pushes a full `sync` on connect and every
`sync_interval_s`, and deltas (`clock` on offset change, `bar` on change)
otherwise. Dashboard-capable firmware also receives changed host readings as
an independent dashboard delta.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import secrets
import signal
import time
from collections import deque

import serial_asyncio

from . import proto
from .composition import build_zones
from .config import Config, apply_config, load_config, validate_config
from .ipc import IpcServer, pause_path
from .link import LinkError, find_port, reset_to_normal_boot_async
from .sources.bluetooth import BluetoothSource
from .sources.clock import ClockSource
from .sources.network import NetworkSource
from .sources.notifications import NotificationSource
from .sources.power import PowerSource
from .sources.sysinfo import SysinfoSource
from .sources.volume import VolumeSource
from .state import StateModel

log = logging.getLogger("349d")

BAUDRATE = 115200
PING_INTERVAL_S = 4.0
PORT_SCAN_INTERVAL_S = 0.5
CARD_STATUS_TIMEOUT_S = 2.0
DASHBOARD_CPU_EMA_TAU_S = 3.0
NORMAL_PRESENT_COALESCE_S = 0.300
SESSION_MAX = proto.IDENTITY_MAX


class Daemon:
    def __init__(
        self, cfg: Config, stop: asyncio.Event, cfg_path: str | None = None, port_override: str | None = None
    ):
        validate_config(cfg)
        self.cfg = cfg
        self.cfg_path = cfg_path
        self._port_override = port_override
        if port_override is not None:
            self.cfg.link.port = port_override
        self.stop = stop
        self.model = StateModel(max_visible=cfg.notifications.max_visible, cache_limit=cfg.notifications.cache_limit)
        self.clock = ClockSource()
        self.sysinfo = SysinfoSource()
        self.volume = VolumeSource()
        self.power = PowerSource()
        self.network = NetworkSource()
        self.bluetooth = BluetoothSource()
        self._cpu_ema: float | None = None
        self._cpu_ema_mono: float | None = None
        self.notifications = NotificationSource(
            cfg.notifications,
            self._device_notify,
            self._device_close,
            on_active_expire=self._device_active_expire,
            on_expire=self._device_expire,
            on_monitor_reset=self._device_monitor_reset,
            grouped_mode=lambda: self._grouped_mode,
            allocate_local_id=self._allocate_local_notification_id,
        )
        self._writer: asyncio.StreamWriter | None = None
        self._state_lock = asyncio.Lock()
        self._wire_lock = asyncio.Lock()
        self._card_sync_capacity: int | None = None
        self._dashboard_capable = False
        self._grouped_enabled = False
        self._grouped_mode = False
        self.grouped_session = secrets.randbelow(SESSION_MAX) + 1
        self._grouped_generation = 0
        self._device_expected_generation = 0
        self._grouped_group = "home"
        self._manual_notifications = False
        self._presentation: dict | None = None
        self._pending_present_id: int | None = None
        self._pending_present_due: float | None = None
        self._sync_tx = 0
        self._device_boot_id: int | None = None
        self._cards_query_lock = asyncio.Lock()
        self._cards_status_waiter: asyncio.Future[dict] | None = None
        self._devlog: deque[str] = deque(maxlen=200)
        self._last_rx_mono: float | None = None
        self._needs_sync = False
        self._tick_wakeup = asyncio.Event()
        self._next_local_notification_id = 100000
        self._injected_expiry: dict[int, float] = {}
        self._ipc = IpcServer(self._ipc_handler)

    def _allocate_local_notification_id(self) -> int:
        """Allocate a daemon-unique positive 31-bit notification ID."""
        if self._next_local_notification_id > proto.IDENTITY_MAX:
            raise OverflowError("local notification ID space exhausted")
        local_id = self._next_local_notification_id
        self._next_local_notification_id += 1
        return local_id

    async def run(self) -> None:
        try:
            await self.notifications.start()
            await self._ipc.start()
            workers = {
                asyncio.create_task(self._link_loop(), name="link"),
                asyncio.create_task(self._tick_loop(), name="tick"),
                asyncio.create_task(self._ping_loop(), name="ping"),
            }
            stop_waiter = asyncio.create_task(self.stop.wait(), name="stop")
            notification_failure = asyncio.create_task(self.notifications.failed.wait(), name="notification-failure")
            try:
                done, _ = await asyncio.wait(
                    workers | {stop_waiter, notification_failure}, return_when=asyncio.FIRST_COMPLETED
                )
                for worker in workers & done:
                    worker.result()  # Re-raise a failed worker's exception.
                    raise RuntimeError(f"{worker.get_name()} stopped unexpectedly")
                if notification_failure in done:
                    raise RuntimeError("notification source stopped unexpectedly") from self.notifications.failure
            finally:
                for task in workers | {stop_waiter, notification_failure}:
                    task.cancel()
                await asyncio.gather(*workers, stop_waiter, notification_failure, return_exceptions=True)
        finally:
            if self._writer is not None:
                self._writer.close()
            await self.notifications.stop()
            await self._ipc.stop()

    async def _device_notify(self, message: dict) -> None:
        local_id = int(message["id"])
        if self.notifications.is_locally_removed(local_id) or self.notifications.is_forgotten(local_id):
            return
        async with self._state_lock:
            if self.notifications.is_locally_removed(local_id) or self.notifications.is_forgotten(local_id):
                return
            was_grouped = self._grouped_enabled
            previous_cached = self.model.cached_notification_ids(
                self._card_sync_capacity, retained=was_grouped
            )
            attention_expired = self._grouped_mode and self.notifications.attention_expired(local_id)
            active_changed = False if attention_expired else self.model.add_notification(message)
            retained_changed, evicted = self.model.retain_notification(message, bump_rev=False)
            if retained_changed and not active_changed:
                self.model.rev += 1
            for evicted_id in evicted:
                self.notifications.forget(evicted_id)
                self._injected_expiry.pop(evicted_id, None)
                if self._grouped_mode:
                    self.model.close_active_notification(evicted_id)

            update = dict(message)
            if was_grouped:
                cached_ids = self.model.cached_notification_ids(
                    self._card_sync_capacity, retained=True
                )
                update["session"] = self.grouped_session
                update["total"] = len(self.model.retained_notifs)
                update["cached"] = local_id in cached_ids
                # A retained replacement may move the cache boundary. Remove
                # cards that have fallen out before publishing the new record.
                for old_id in previous_cached:
                    if old_id not in cached_ids:
                        await self.send(
                            proto.close(old_id, total=len(self.model.retained_notifs), session=self.grouped_session)
                        )
            else:
                cached_ids = self.model.cached_notification_ids(self._card_sync_capacity)
                update["total"] = len(self.model.notifs)
                if self._card_sync_capacity is not None:
                    update["cached"] = local_id in cached_ids

            sent = await self.send(update)
            if was_grouped and sent and not self.notifications.attention_expired(local_id):
                await self._queue_presentation_locked(message, time.monotonic())

    async def _device_close(self, local_id: int) -> None:
        async with self._state_lock:
            self._injected_expiry.pop(local_id, None)
            grouped = self._grouped_enabled
            collection = self.model.retained_notifs if grouped else self.model.notifs
            cached_ids = self.model.cached_notification_ids(self._card_sync_capacity, retained=grouped)
            overflowed = len(collection) > len(cached_ids)
            if self.model.close_notification(local_id):
                if self._pending_present_id == local_id:
                    self._pending_present_id = None
                    self._pending_present_due = None
                if self._presentation is not None and self._presentation["id"] == local_id:
                    await self._end_presentation_locked(send_end=False)
                await self.send(
                    proto.close(
                        local_id,
                        total=len(self.model.retained_notifs if grouped else self.model.notifs),
                        session=self.grouped_session if grouped else None,
                    )
                )
                if self._card_sync_capacity is not None and overflowed and local_id in cached_ids:
                    # Closing a cached card exposes a vacancy when older active
                    # cards were omitted. The tick loop coalesces close bursts.
                    self._needs_sync = True
                    self._tick_wakeup.set()

    async def _device_expire(self, local_id: int, *, complete_presentation: bool = True) -> None:
        """End popup attention while retaining grouped record text."""
        if not self._grouped_mode:
            await self._device_close(local_id)
            return
        async with self._state_lock:
            self._injected_expiry.pop(local_id, None)
            self.model.close_active_notification(local_id)
            if self._pending_present_id == local_id:
                self._pending_present_id = None
                self._pending_present_due = None
            if (
                complete_presentation
                and self._presentation is not None
                and self._presentation["id"] == local_id
            ):
                await self._end_presentation_locked(send_end=self._grouped_enabled)

    async def _device_active_expire(self, local_id: int) -> None:
        """Expire only the legacy active projection, preserving grouped attention."""
        async with self._state_lock:
            self.model.close_active_notification(local_id)
            if self._pending_present_id == local_id:
                self._pending_present_id = None
                self._pending_present_due = None

    async def _device_monitor_reset(self, local_ids: list[int]) -> None:
        """Archive grouped text and cancel actions tied to the lost monitor."""
        async with self._state_lock:
            for local_id in local_ids:
                self.model.close_active_notification(local_id)
            self._pending_present_id = None
            self._pending_present_due = None
            if self._presentation is not None:
                await self._end_presentation_locked(send_end=self._grouped_enabled)

    def _presentation_timeout_ms(self, message: dict) -> int | None:
        requested = message.get("expire", -1)
        if isinstance(requested, bool) or not isinstance(requested, int):
            requested = -1
        if requested > 0:
            return requested
        if requested == 0:
            return None
        urgency = message.get("urgency", 1)
        fallback = (
            self.cfg.notifications.critical_popup_timeout_ms
            if isinstance(urgency, int) and not isinstance(urgency, bool) and urgency >= 2
            else self.cfg.notifications.popup_timeout_ms
        )
        return fallback if fallback > 0 else None

    async def _queue_presentation_locked(self, message: dict, now: float) -> None:
        """Apply critical attention immediately and coalesce normal arrivals."""
        urgency = int(message.get("urgency", 1))
        if urgency >= 2:
            self._pending_present_id = None
            self._pending_present_due = None
            await self._present_notification_locked(message, now)
            return
        if self._manual_notifications:
            self._pending_present_id = None
            self._pending_present_due = None
            return
        if self._presentation is not None and self._presentation["urgency"] >= 2:
            return
        self._pending_present_id = int(message["id"])
        self._pending_present_due = now + NORMAL_PRESENT_COALESCE_S
        self._tick_wakeup.set()

    async def _present_notification_locked(self, message: dict, now: float) -> None:
        local_id = int(message["id"])
        if not self._grouped_enabled or self._writer is None:
            return
        cached = self.model.cached_notification_ids(self._card_sync_capacity, retained=True)
        if local_id not in cached or local_id not in self.model.retained_notifs:
            return
        if self._grouped_generation >= SESSION_MAX:
            log.error("grouped presentation generation exhausted for session %d", self.grouped_session)
            return
        self._grouped_generation += 1
        timeout_ms = self._presentation_timeout_ms(message)
        self._presentation = {
            "id": local_id,
            "generation": self._grouped_generation,
            "urgency": min(2, max(0, int(message.get("urgency", 1)))),
            "deadline": None if timeout_ms is None else now + timeout_ms / 1000.0,
        }
        self._grouped_group = "notifications"
        self._manual_notifications = False
        sent = await self._write_presentation_locked(self._presentation)
        if not sent:
            self._presentation = None
        else:
            self._device_expected_generation = self._presentation["generation"]

    async def _write_presentation_locked(self, presentation: dict, *, end: bool = False) -> bool:
        if not self._grouped_enabled or self._writer is None:
            return False
        async with self._wire_lock:
            deadline = presentation["deadline"]
            if end or deadline is None:
                remaining_ms = 0 if end else -1
            else:
                remaining_ms = max(0, math.ceil((deadline - time.monotonic()) * 1000))
            message = proto.present(
                self.grouped_session,
                presentation["generation"],
                presentation["id"],
                remaining_ms,
                presentation["urgency"],
            )
            return await self._write_message(message)

    async def _end_presentation_locked(self, *, send_end: bool) -> None:
        presentation = self._presentation
        self._presentation = None
        if presentation is not None and send_end:
            await self._write_presentation_locked(presentation, end=True)

    async def _publish_pending_presentation_locked(self, now: float) -> None:
        local_id = self._pending_present_id
        self._pending_present_id = None
        self._pending_present_due = None
        if local_id is None or self._manual_notifications or not self._grouped_enabled:
            return
        message = self.model.retained_notifs.get(local_id)
        if message is None:
            return
        if self._presentation is not None and self._presentation["urgency"] >= 2:
            return
        await self._present_notification_locked(message, now)

    async def _expire_presentation_locked(self, now: float) -> None:
        presentation = self._presentation
        if presentation is not None and presentation["deadline"] is not None and presentation["deadline"] <= now:
            await self._end_presentation_locked(send_end=True)

    async def send(self, message: dict) -> bool:
        async with self._wire_lock:
            return await self._write_message(message)

    async def _write_message(self, message: dict) -> bool:
        if self._writer is None:
            return False
        try:
            payload = proto.encode(message)
        except ValueError as exc:
            log.error("refusing invalid protocol message: %s", exc)
            return False
        try:
            self._writer.write(payload)
            await self._writer.drain()
            return True
        except (ConnectionError, OSError) as exc:
            log.debug("send failed: %s", exc)
            return False

    def _sample(self) -> dict:
        values: dict = {}
        values.update(self.sysinfo.read())
        values.update(self.volume.read())
        values.update(self.power.read())
        values.update(self.network.read())
        values.update(self.bluetooth.read())
        return values

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

    def _dashboard_payload(self, values: dict) -> dict:
        now = time.monotonic()
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
            "mem": values.get("mem"),
            "network": values.get("network"),
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

    async def _update_dashboard_locked(self, values: dict, *, emit_delta: bool = True) -> bool:
        """Update dashboard state while holding ``_state_lock``."""
        changed = self.model.set_dashboard(self._dashboard_payload(values))
        if changed and emit_delta and self._dashboard_capable:
            await self.send(proto.dashboard_message(self.model.dashboard))
        return changed

    async def _send_sync(self) -> None:
        async with self._state_lock:
            self._needs_sync = False
            await self._send_sync_locked()

    async def _send_sync_locked(self) -> None:
        epoch, offset = self.clock.read()
        self.model.set_clock(epoch, offset)
        values = self._sample()
        self.model.set_zones(build_zones(self.cfg.bar.preset, values))
        await self._update_dashboard_locked(values, emit_delta=False)
        if self._card_sync_capacity is None:
            messages = [self.model.snapshot()]
        else:
            self._sync_tx += 1
            grouped = self._grouped_enabled
            snapshot = self.model.card_snapshot(self._card_sync_capacity, retained=grouped)
            messages = proto.card_sync_messages(
                snapshot,
                self._sync_tx,
                include_dashboard=self._dashboard_capable,
                grouped_session=self.grouped_session if grouped else None,
            )

        # Hold the wire lock across the full transaction, including begin and
        # commit, so pings and IPC output cannot split its staging sequence.
        async with self._wire_lock:
            for message in messages:
                if not await self._write_message(message):
                    break

    async def _link_loop(self) -> None:
        min_backoff = max(0.05, float(self.cfg.link.reconnect_min_s))
        max_backoff = max(min_backoff, float(self.cfg.link.reconnect_max_s))
        backoff = min_backoff

        while not self.stop.is_set():
            if pause_path().exists():
                if self._writer is not None:
                    self._writer.close()
                backoff = min_backoff
                await asyncio.sleep(0.5)
                continue

            path = self.cfg.link.port
            if path is None:
                try:
                    path = find_port()
                except LinkError as exc:
                    log.debug("no device: %s", exc)
                    # Device discovery is cheap; do not let open-error backoff
                    # delay a replug after the by-id path reappears.
                    backoff = min_backoff
                    await asyncio.sleep(PORT_SCAN_INTERVAL_S)
                    continue

            try:
                reader, writer = await serial_asyncio.open_serial_connection(url=path, baudrate=BAUDRATE)
            except Exception as exc:  # serial.SerialException and friends
                log.warning("open %s failed: %s", path, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue

            log.info("link up on %s", path)
            backoff = min_backoff
            async with self._state_lock:
                self._grouped_enabled = False
                self._device_expected_generation = 0
                self._pending_present_id = None
                self._pending_present_due = None
                await self._end_presentation_locked(send_end=False)
                self._grouped_group = "home"
                self._manual_notifications = False
                self._writer = writer
                self._card_sync_capacity = None
                self._dashboard_capable = False
                self._sync_tx = 0
                self._device_boot_id = None
            # Opening the port resets the chip, but the kernel's DTR/RTS raise
            # can land it in download mode; force a normal boot, then the
            # device announces itself.
            serial_port = getattr(writer.transport, "serial", None)
            if serial_port is not None:
                await reset_to_normal_boot_async(serial_port)
            await self.send(proto.hello())
            await self.send({"t": "ping", "ts": int(time.time())})

            try:
                await self._read_loop(reader)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("link error: %s", exc)
            finally:
                async with self._state_lock:
                    self._writer = None
                    self._grouped_enabled = False
                    self._pending_present_id = None
                    self._pending_present_due = None
                    await self._end_presentation_locked(send_end=False)
                    self._grouped_group = "home"
                    self._manual_notifications = False
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

            if not self.stop.is_set():
                log.info("link down, retrying")
                await asyncio.sleep(backoff)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        buf = b""
        async for chunk in reader:
            buf += chunk
            if len(buf) > proto.LINE_MAX * 2:
                log.warning("dropping oversized line buffer")
                buf = b""
            while b"\n" in buf:
                raw, _, buf = buf.partition(b"\n")
                await self._on_line(raw.decode("utf-8", "replace").rstrip("\r"))
        if buf:
            await self._on_line(buf.decode("utf-8", "replace"))

    async def _on_line(self, line: str) -> None:
        self._last_rx_mono = time.monotonic()
        is_data, message = proto.classify(line)
        if not is_data:
            if line:
                self._devlog.append(line)
                log.debug("dev: %s", line)
            return
        if message is None:
            log.warning("malformed data line: %.80s", line)
            return

        kind = message.get("t")
        if kind == "hello":
            boot_id = message.get("boot_id")
            if isinstance(boot_id, bool) or not isinstance(boot_id, int):
                boot_id = None
            log.info(
                "device hello: fw=%s build=%s sha=%s proto=%s cap=%s",
                message.get("fw"),
                message.get("build"),
                message.get("build_sha"),
                message.get("proto"),
                message.get("cap"),
            )
            async with self._state_lock:
                new_capacity = proto.card_sync_capacity(message)
                new_dashboard = new_capacity is not None and proto.dashboard_capable(message)
                new_grouped = new_capacity is not None and proto.grouped_ui_capable(message)
                first_grouped = new_grouped and not self._grouped_enabled
                changed_boot = boot_id is not None and boot_id != self._device_boot_id
                capabilities_changed = (
                    new_capacity != self._card_sync_capacity
                    or new_dashboard != self._dashboard_capable
                    or new_grouped != self._grouped_enabled
                )
                self._card_sync_capacity = new_capacity
                self._dashboard_capable = new_dashboard
                self._grouped_enabled = new_grouped
                self._grouped_mode = new_grouped
                if first_grouped or changed_boot:
                    self._device_expected_generation = 0
                if changed_boot:
                    self._pending_present_id = None
                    self._pending_present_due = None
                    await self._end_presentation_locked(send_end=False)
                    self._grouped_group = "home"
                    self._manual_notifications = False
                if new_grouped:
                    unassociated = self.notifications.enforce_grouped_bounds()
                    for local_id in unassociated:
                        self.model.close_active_notification(local_id)
                    for local_id in tuple(self.model.notifs):
                        if local_id not in self.model.retained_notifs:
                            self.model.close_active_notification(local_id)
                if not new_grouped:
                    self._pending_present_id = None
                    self._pending_present_due = None
                    await self._end_presentation_locked(send_end=False)
                    self._grouped_group = "home"
                    self._manual_notifications = False
                if boot_id is not None and boot_id == self._device_boot_id and not capabilities_changed:
                    log.debug("ignoring repeated hello for device boot_id=%s", boot_id)
                else:
                    # A missing ID keeps the pre-dedup legacy behavior: every
                    # hello requests a fresh full sync.
                    self._device_boot_id = boot_id
                    await self._send_sync_locked()
        elif kind == "input":
            await self._handle_input(message)
        elif kind == "resync":
            log.info("device requested resync (%s)", message.get("reason"))
            await self._send_sync()
        elif kind == "ack":
            log.debug("device ack: %s", message.get("v"))
        elif kind == "cards_status":
            status = proto.card_status(message)
            if status is None:
                log.warning("malformed cards_status response")
                return
            waiter = self._cards_status_waiter
            if waiter is not None and not waiter.done():
                waiter.set_result(status)
            else:
                log.debug("unsolicited cards_status: %s", status)
        else:
            log.debug("unknown device message: %s", message)

    async def _handle_input(self, message: dict) -> None:
        action = message.get("action")
        if action == "browse" and self._grouped_enabled:
            session = message.get("session")
            generation = message.get("generation")
            group = message.get("group")
            if (
                isinstance(session, bool)
                or not isinstance(session, int)
                or session != self.grouped_session
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or not 0 <= generation <= SESSION_MAX
                or not isinstance(group, str)
                or group not in {"home", "notifications"}
            ):
                return
            async with self._state_lock:
                if not self._grouped_enabled or generation != self._device_expected_generation:
                    return
                self._pending_present_id = None
                self._pending_present_due = None
                self._grouped_group = group
                self._manual_notifications = group == "notifications"
                if self._presentation is not None:
                    await self._end_presentation_locked(send_end=True)
            return

        if action == "dismiss" and self._grouped_enabled:
            session = message.get("session")
            generation = message.get("generation")
            local_id = message.get("id")
            if (
                isinstance(session, bool)
                or not isinstance(session, int)
                or session != self.grouped_session
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or not 0 <= generation <= SESSION_MAX
                or isinstance(local_id, bool)
                or not isinstance(local_id, int)
                or not 1 <= local_id <= SESSION_MAX
            ):
                return
            propagate = False
            async with self._state_lock:
                if not self._grouped_enabled or local_id not in self.model.retained_notifs:
                    return
                cached = self.model.cached_notification_ids(self._card_sync_capacity, retained=True)
                if local_id not in cached:
                    return
                overflowed = len(self.model.retained_notifs) > len(cached)
                self.notifications.hide_locally(local_id)
                self._injected_expiry.pop(local_id, None)
                if not self.model.close_notification(local_id):
                    return
                if self._pending_present_id == local_id:
                    self._pending_present_id = None
                    self._pending_present_due = None
                if self._presentation is not None and self._presentation["id"] == local_id:
                    self._presentation = None
                await self.send(
                    proto.close(
                        local_id,
                        total=len(self.model.retained_notifs),
                        session=self.grouped_session,
                    )
                )
                if overflowed:
                    self._needs_sync = True
                    self._tick_wakeup.set()
                propagate = self.cfg.notifications.device_dismiss == "propagate"
            if propagate:
                await self.notifications.dismiss(local_id)
            return

        if action == "dismiss" and "id" in message:
            try:
                await self.notifications.dismiss(int(message["id"]))
            except (TypeError, ValueError):
                return
        else:
            log.info("device input: %s", message)

    async def _query_device_cards(self) -> dict:
        async with self._cards_query_lock:
            if self._card_sync_capacity is None:
                return {"ok": False, "error": "device does not advertise card-sync-v1"}

            waiter: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            self._cards_status_waiter = waiter
            try:
                if not await self.send({"t": "cards_query"}):
                    return {"ok": False, "error": "device is disconnected"}
                try:
                    status = await asyncio.wait_for(waiter, timeout=CARD_STATUS_TIMEOUT_S)
                except asyncio.TimeoutError:
                    return {"ok": False, "error": "timed out waiting for cards_status"}
                return {"ok": True, "device_cards": status}
            finally:
                if self._cards_status_waiter is waiter:
                    self._cards_status_waiter = None

    async def reload(self) -> bool:
        if self.cfg_path is None:
            log.info("no config file to reload")
            return False
        try:
            new = load_config(self.cfg_path)
        except Exception as exc:  # noqa: BLE001 - a bad file must not kill the daemon
            log.error("config reload failed: %s", exc)
            return False
        async with self._state_lock:
            apply_config(self.cfg, new)
            if self._port_override is not None:
                self.cfg.link.port = self._port_override
            self.model.max_visible = self.cfg.notifications.max_visible
            self.model.cache_limit = self.cfg.notifications.cache_limit
            self._needs_sync = True
            self._tick_wakeup.set()
        await self.notifications.reconfigure()
        log.info("config reloaded from %s", self.cfg_path)
        return True

    def pause(self) -> None:
        pause_path().touch()
        if self._writer is not None:
            self._writer.close()
        log.info("paused: serial port released for flashing")

    def resume(self) -> None:
        try:
            pause_path().unlink()
        except FileNotFoundError:
            pass
        log.info("resumed")

    def _status(self) -> dict:
        age = None if self._last_rx_mono is None else round(time.monotonic() - self._last_rx_mono, 1)
        presentation = None
        if self._presentation is not None:
            deadline = self._presentation["deadline"]
            remaining_ms = -1 if deadline is None else max(0, math.ceil((deadline - time.monotonic()) * 1000))
            presentation = {
                "session": self.grouped_session,
                "generation": self._presentation["generation"],
                "id": self._presentation["id"],
                "remaining_ms": remaining_ms,
            }
        return {
            "ok": True,
            "paused": pause_path().exists(),
            "link": self._writer is not None,
            "port": self.cfg.link.port,
            "rev": self.model.rev,
            "notifs": len(self.model.notifs),
            "retained_notifs": len(self.model.retained_notifs),
            "grouped": self._grouped_enabled,
            "presentation": presentation,
            "last_rx_s": age,
            "config": self.cfg_path,
        }

    async def _ipc_handler(self, request: dict) -> dict:
        cmd = request.get("cmd")
        if cmd == "status":
            return self._status()
        if cmd == "device_cards":
            return await self._query_device_cards()
        if cmd == "text":
            await self.send({"t": "text", "v": str(request.get("value", ""))})
            return {"ok": True}
        if cmd == "notify":
            nid = self._allocate_local_notification_id()
            message = proto.notify(
                nid,
                str(request.get("app", "349ctl")),
                str(request.get("summary", "")),
                str(request.get("body", "")),
                int(request.get("urgency", 1)),
                int(request.get("expire", 5000)),
                int(time.time()),
            )
            await self._device_notify(message)
            injected_timeout = self._presentation_timeout_ms(message)
            if injected_timeout is not None:
                self._injected_expiry[nid] = time.monotonic() + injected_timeout / 1000.0
                self._tick_wakeup.set()
            return {"ok": True, "id": nid}
        if cmd == "pause":
            self.pause()
            return {"ok": True}
        if cmd == "resume":
            self.resume()
            return {"ok": True}
        if cmd == "reload":
            return {"ok": await self.reload()}
        if cmd == "log":
            count = max(1, min(200, int(request.get("lines", 50))))
            return {"ok": True, "lines": list(self._devlog)[-count:]}
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    async def _tick_loop(self) -> None:
        last_offset: int | None = None
        interval_settings: tuple[float, float] | None = None
        next_sync = time.monotonic()

        while True:
            tick = float(self.cfg.daemon.tick_s)
            sync_interval = float(self.cfg.daemon.sync_interval_s)
            settings = (tick, sync_interval)
            if settings != interval_settings:
                next_sync = time.monotonic() + sync_interval
                interval_settings = settings

            wait_s = tick
            if self._injected_expiry:
                wait_s = min(wait_s, max(0.0, min(self._injected_expiry.values()) - time.monotonic()))
            attention_deadlines = []
            if self._pending_present_due is not None:
                attention_deadlines.append(self._pending_present_due)
            if self._presentation is not None and self._presentation["deadline"] is not None:
                attention_deadlines.append(self._presentation["deadline"])
            if attention_deadlines:
                wait_s = min(wait_s, max(0.0, min(attention_deadlines) - time.monotonic()))
            try:
                await asyncio.wait_for(self._tick_wakeup.wait(), timeout=wait_s)
            except asyncio.TimeoutError:
                pass
            else:
                self._tick_wakeup.clear()

            # A reload may have changed both intervals while this wait was
            # active. Reset the sync deadline before processing this tick.
            tick = float(self.cfg.daemon.tick_s)
            sync_interval = float(self.cfg.daemon.sync_interval_s)
            settings = (tick, sync_interval)
            if settings != interval_settings:
                next_sync = time.monotonic() + sync_interval
                interval_settings = settings

            now = time.monotonic()
            for nid, deadline in tuple(self._injected_expiry.items()):
                if deadline <= now:
                    self._injected_expiry.pop(nid, None)
                    if self._grouped_mode:
                        await self._device_expire(nid, complete_presentation=False)
                    else:
                        await self._device_close(nid)

            if self._needs_sync:
                await self._send_sync()

            async with self._state_lock:
                attention_now = time.monotonic()
                await self._expire_presentation_locked(attention_now)
                if self._pending_present_due is not None and self._pending_present_due <= attention_now:
                    await self._publish_pending_presentation_locked(attention_now)

            values = self._sample()

            epoch, offset = self.clock.read()
            async with self._state_lock:
                if offset != last_offset:
                    self.model.set_clock(epoch, offset)
                    await self.send(proto.clock(epoch, offset))
                    last_offset = offset

                await self._update_dashboard_locked(values)
                if self.model.set_zones(build_zones(self.cfg.bar.preset, values)):
                    await self.send(proto.bar(self.model.zones, self.model.rev))

            if time.monotonic() >= next_sync:
                await self._send_sync()
                next_sync = time.monotonic() + float(self.cfg.daemon.sync_interval_s)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            if self._writer is not None:
                await self.send({"t": "ping", "ts": int(time.time())})


async def _run(cfg: Config, stop: asyncio.Event, cfg_path: str | None = None, port_override: str | None = None) -> None:
    loop = asyncio.get_running_loop()
    daemon = Daemon(cfg, stop, cfg_path, port_override)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass
    try:
        loop.add_signal_handler(signal.SIGHUP, lambda: asyncio.create_task(daemon.reload()))
    except (NotImplementedError, AttributeError):  # pragma: no cover
        pass
    await daemon.run()


def default_config_path() -> str | None:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "349d", "config.toml")
    return path if os.path.exists(path) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="349d", description=__doc__)
    parser.add_argument("--config", help="TOML config file (default: ~/.config/349d/config.toml)")
    parser.add_argument("--port", help="serial port (default: auto-detect by-id)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg_path = args.config or default_config_path()
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        parser.error(f"invalid configuration: {exc}")

    stop = asyncio.Event()
    try:
        asyncio.run(_run(cfg, stop, cfg_path, args.port))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
