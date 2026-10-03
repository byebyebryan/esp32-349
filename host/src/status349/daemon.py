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
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from . import discovery, pairing, proto
from .actions import BridgeClient, NotificationActionManager
from .composition import build_zones
from .config import Config, apply_config, load_config, validate_config
from .ipc import IpcServer, pause_path
from .link import LinkError
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
PONG_TIMEOUT_S = 2.0
LINK_LIVENESS_TIMEOUT_S = 12.0
WRITE_TIMEOUT_S = 2.0
PORT_SCAN_INTERVAL_S = 0.5
CARD_STATUS_TIMEOUT_S = 2.0
DASHBOARD_CPU_EMA_TAU_S = 3.0
NORMAL_PRESENT_COALESCE_S = 0.300
SESSION_MAX = proto.IDENTITY_MAX
ACTION_RESULT_LIMIT = 64


@dataclass(frozen=True, slots=True)
class _TelemetrySample:
    generation: int
    values: dict
    completed_mono: float


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
        self.model = StateModel(
            max_visible=cfg.notifications.max_visible,
            cache_limit=cfg.notifications.cache_limit,
            retention_s=cfg.notifications.retention_s,
        )
        self.clock = ClockSource()
        self.sysinfo = SysinfoSource()
        self.volume = VolumeSource()
        self.power = PowerSource()
        self.network = NetworkSource()
        self.bluetooth = BluetoothSource()
        self._cpu_ema: float | None = None
        self._cpu_ema_mono: float | None = None
        self._latest_sample: dict | None = None
        self._latest_sample_mono: float | None = None
        self._latest_sample_result: _TelemetrySample | None = None
        self._next_sample_mono: float | None = None
        self._sample_thread_lock = threading.Lock()
        self._sample_task: asyncio.Task[_TelemetrySample] | None = None
        self._sample_generation = 0
        self._notification_peer_known = False
        self.notifications = NotificationSource(
            cfg.notifications,
            self._device_notify,
            self._device_close,
            on_active_expire=self._device_active_expire,
            on_expire=self._device_expire,
            on_monitor_reset=self._device_monitor_reset,
            grouped_mode=lambda: self._grouped_mode,
            bounded_mode=self._notification_bounds_required,
            allocate_local_id=self._allocate_local_notification_id,
        )
        self._actions_capable = False
        self._actions_negotiated = False
        self._action_ledger_boot_id: int | None = None
        self._action_highwater = 0
        self._action_results: OrderedDict[int, tuple[tuple[int, int, int, int, int], str]] = OrderedDict()
        self._action_pending: dict | None = None
        self._action_tasks: set[asyncio.Task] = set()
        self.action_manager = NotificationActionManager(
            self.notifications,
            BridgeClient(),
            enabled=lambda: (
                self.cfg.notifications.device_open == "dms"
                and self._actions_negotiated and self._actions_capable
                and self._action_ledger_boot_id is not None
                and self._device_boot_id == self._action_ledger_boot_id
                and self._writer is not None
            ),
            context=lambda: (self.grouped_session, self._action_ledger_boot_id),
            retained_ids=lambda: list(self.model.retained_notifs),
            on_change=self._action_state_changed,
        )
        self._writer: asyncio.StreamWriter | None = None
        self._state_lock = asyncio.Lock()
        self._wire_lock = asyncio.Lock()
        self._card_sync_capacity: int | None = None
        self._dashboard_capable = False
        self._grouped_enabled = False
        self._grouped_mode = False
        self._history_enabled = False
        self._history_mode = False
        self._body_style_enabled = False
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
        self._active_port: str | None = None
        self._link_wakeup = asyncio.Event()
        self._link_port_generation = 0
        self._link_settings_generation = 0
        self._session: discovery.Session | None = None
        self._pending_probe: tuple[discovery.ProbeResult, int] | None = None
        self._link_idle = asyncio.Event()
        self._link_idle.set()
        self._link_failed = False
        self._link_error: str | None = None
        self._pong_waiters: dict[int, asyncio.Future] = {}
        self._last_pong_mono: float | None = None
        self._pair_store = pairing.PairingStore()
        self._paired_identity: pairing.UsbIdentity | None = None
        self._pair_load_error: str | None = None
        try:
            self._paired_identity = self._pair_store.load()
        except (OSError, ValueError, pairing.PairingError) as exc:
            self._pair_load_error = str(exc)
        self._pair_task: asyncio.Task | None = None
        self._pair_cancel: threading.Event | None = None
        # A waiting CLI must not mistake a new daemon's job for its old job.
        self._pair_operation = secrets.randbits(52)
        self._pair_result: dict | None = None
        self._pair_scanning = False

    def _notification_bounds_required(self) -> bool:
        """Bound startup buffering until a peer selects its notification mode."""
        return not self._notification_peer_known or self._grouped_mode

    def _allocate_local_notification_id(self) -> int:
        """Allocate a daemon-unique positive 31-bit notification ID."""
        if self._next_local_notification_id > proto.IDENTITY_MAX:
            raise OverflowError("local notification ID space exhausted")
        local_id = self._next_local_notification_id
        self._next_local_notification_id += 1
        return local_id

    async def _action_state_changed(self, local_id: int, opened: dict) -> None:
        """Publish only metadata for a retained card in the committed device cache."""
        async with self._state_lock:
            await self._expire_history_locked(self._monotonic())
            if (
                not self._actions_negotiated or not self._grouped_enabled
                or local_id not in self.model.retained_notifs
                or local_id not in self.model.cached_notification_ids(self._card_sync_capacity, retained=True)
            ):
                return
            entry = self.action_manager.entries.get(local_id)
            if entry is None or opened != {"rev": entry.revision, "state": entry.state}:
                return
            await self.send(proto.card_action(self.grouped_session, local_id, entry.revision, entry.state))

    async def run(self) -> None:
        notifications_start_attempted = False
        try:
            # Claim the runtime directory before starting any workers or
            # touching the serial device. A rejected second daemon stays inert.
            await self._ipc.start()
            notifications_start_attempted = True
            await self.notifications.start()
            workers = {
                asyncio.create_task(self._link_loop(), name="link"),
                asyncio.create_task(self._tick_loop(), name="tick"),
                asyncio.create_task(self._ping_loop(), name="ping"),
                asyncio.create_task(self.action_manager.run(self.stop), name="notification-actions"),
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
                self._cancel_pairing()
                for task in workers | {stop_waiter, notification_failure}:
                    task.cancel()
                await asyncio.gather(*workers, stop_waiter, notification_failure, return_exceptions=True)
        finally:
            try:
                await self._drain_pairing()
                if self._pending_probe is not None:
                    pending, self._pending_probe = self._pending_probe, None
                    await pending[0].close()
                await self._drain_sample_worker()
                for task in tuple(self._action_tasks):
                    task.cancel()
                if self._action_tasks:
                    await asyncio.gather(*self._action_tasks, return_exceptions=True)
                if self._session is not None:
                    await self._session.close()
                if notifications_start_attempted:
                    await self.notifications.stop()
            finally:
                await self._ipc.stop()

    async def _device_notify(self, message: dict) -> None:
        local_id = int(message["id"])
        if self.notifications.is_locally_removed(local_id) or self.notifications.is_forgotten(local_id):
            return
        async with self._state_lock:
            if self.notifications.is_locally_removed(local_id) or self.notifications.is_forgotten(local_id):
                return
            now = self._monotonic()
            receipt_mono = message.get("_received_mono", now)
            try:
                parsed_receipt = float(receipt_mono)
            except (OverflowError, TypeError, ValueError):
                receipt_mono = now
            else:
                if isinstance(receipt_mono, bool) or not math.isfinite(parsed_receipt):
                    receipt_mono = now
                else:
                    receipt_mono = parsed_receipt
            clean_message = {key: value for key, value in message.items() if not key.startswith("_")}
            clean_message.pop("history", None)
            if self._history_mode and (now - receipt_mono) * 1000 >= self.cfg.notifications.retention_s * 1000:
                self.notifications.forget(local_id)
                return
            await self._expire_history_locked(now, exclude_ids={local_id})
            was_grouped = self._grouped_enabled
            previous_cached = self.model.cached_notification_ids(
                self._card_sync_capacity, retained=was_grouped
            )
            attention_expired = self._grouped_mode and self.notifications.attention_expired(local_id)
            active_changed = False if attention_expired else self.model.add_notification(clean_message)
            retained_changed, evicted = self.model.retain_notification(
                clean_message, receipt_mono=receipt_mono, bump_rev=False
            )
            if retained_changed and not active_changed:
                self.model.rev += 1
            for evicted_id in evicted:
                self._cancel_unsent_action(evicted_id)
                if self._notification_bounds_required():
                    self.notifications.forget(evicted_id)
                    self._injected_expiry.pop(evicted_id, None)
                    self.model.close_active_notification(evicted_id)

            update = self._notification_projection(
                clean_message, history=False, now_mono=now
            )
            if update is None:
                return
            if was_grouped:
                cached_ids = self.model.cached_notification_ids(
                    self._card_sync_capacity, retained=True
                )
                update["session"] = self.grouped_session
                update["total"] = len(self.model.retained_notifs)
                update["cached"] = local_id in cached_ids
                if self._actions_negotiated:
                    update["open"] = self.action_manager.open_for(local_id)
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

            if self._history_enabled:
                send_now = self._monotonic()
                history = self.model.history_metadata(local_id, send_now)
                if history is None:
                    await self._expire_history_locked(send_now)
                    return
                update["body"] = clean_message.get("body", "")
                if "body_runs" in clean_message:
                    update["body_runs"] = clean_message["body_runs"]
                proto.project_notification_body(
                    update, proto.NOTIFICATION_BODY_HISTORY_BYTES,
                    include_styles=self._body_style_enabled,
                )
                update["history"] = history
            sent = await self.send(update)
            if was_grouped and sent and not self.notifications.attention_expired(local_id):
                await self._queue_presentation_locked(clean_message, now)

    def _notification_projection(self, message: dict, *, history: bool, now_mono: float) -> dict | None:
        """Build one peer-specific notification projection without retained internals."""
        projected = {key: value for key, value in message.items() if not key.startswith("_") and key != "history"}
        body_limit = (
            proto.NOTIFICATION_BODY_HISTORY_BYTES if history else proto.NOTIFICATION_BODY_LEGACY_BYTES
        )
        proto.project_notification_body(
            projected, body_limit, include_styles=history and self._body_style_enabled,
        )
        if history:
            local_id = int(projected["id"])
            metadata = self.model.history_metadata(local_id, now_mono)
            if metadata is None:
                return None
            projected["history"] = metadata
        return projected

    async def _expire_history_locked(self, now_mono: float, *, exclude_ids: set[int] | None = None) -> list[int]:
        """Expire retained history and release its source/action identities."""
        if not self._history_mode:
            return []
        exclude_ids = exclude_ids or set()
        expired = [
            nid for nid, receipt in self.model.retained_received_mono.items()
            if nid not in exclude_ids
            and (now_mono - receipt) * 1000 >= self.cfg.notifications.retention_s * 1000
        ]
        if not expired:
            return []

        was_grouped = self._grouped_enabled
        previous_cached = self.model.cached_notification_ids(self._card_sync_capacity, retained=was_grouped)
        previous_overflow = len(self.model.retained_notifs) > len(previous_cached)
        for local_id in expired:
            self._cancel_unsent_action(local_id)
            self._injected_expiry.pop(local_id, None)
            self.notifications.forget(local_id)
            if self._pending_present_id == local_id:
                self._pending_present_id = None
                self._pending_present_due = None
            if self._presentation is not None and self._presentation["id"] == local_id:
                await self._end_presentation_locked(send_end=self._history_enabled)
        self.model.expire_retained(now_mono, exclude_ids=exclude_ids)
        if not self.model.retained_notifs:
            self._manual_notifications = False
        if self._history_enabled:
            total = len(self.model.retained_notifs)
            for local_id in expired:
                await self.send(proto.close(local_id, total=total, session=self.grouped_session))
        if was_grouped and previous_cached.intersection(expired):
            current_cached = self.model.cached_notification_ids(self._card_sync_capacity, retained=True)
            current_overflow = len(self.model.retained_notifs) > len(current_cached)
            if previous_overflow or current_overflow:
                self._needs_sync = True
                self._tick_wakeup.set()
        return expired

    async def _device_close(self, local_id: int) -> None:
        async with self._state_lock:
            self._cancel_unsent_action(local_id)
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
                if grouped and not self.model.retained_notifs:
                    if not self._history_mode:
                        self._grouped_group = "home"
                    self._manual_notifications = False
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
                self._cancel_unsent_action(local_id)
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
        if self._history_enabled and self._manual_notifications:
            self._pending_present_id = None
            self._pending_present_due = None
            return
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
        if self._writer is None or self._link_failed or pause_path().exists():
            return False
        writer = self._writer
        try:
            payload = proto.encode(message)
        except ValueError as exc:
            log.error("refusing invalid protocol message: %s", exc)
            return False
        try:
            writer.write(payload)
            async with asyncio.timeout(WRITE_TIMEOUT_S):
                await writer.drain()
            return True
        except (ConnectionError, OSError, TimeoutError) as exc:
            log.debug("send failed: %s", exc)
            if self._writer is writer:
                self._fail_link(f"serial write failed: {exc}")
            return False

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

    async def _send_sync(self) -> None:
        await self._prime_sample()
        async with self._state_lock:
            self._needs_sync = False
            await self._send_sync_locked()

    async def _send_sync_locked(self) -> None:
        await self._expire_history_locked(self._monotonic())
        epoch, offset = self.clock.read()
        self.model.set_clock(epoch, offset)
        values = self._latest_sample
        if values is None:
            raise RuntimeError("full sync requested before telemetry was sampled")
        self.model.set_zones(build_zones(self.cfg.bar.preset, values))
        if self._card_sync_capacity is None:
            messages = [self.model.snapshot()]
        else:
            self._sync_tx += 1
            grouped = self._grouped_enabled
            history_now = self._monotonic()
            await self._expire_history_locked(history_now)
            snapshot = self.model.card_snapshot(self._card_sync_capacity, retained=grouped)
            snapshot["notifs"] = [
                self._notification_projection(card, history=self._history_enabled, now_mono=history_now)
                for card in snapshot["notifs"]
            ]
            snapshot["notifs"] = [card for card in snapshot["notifs"] if card is not None]
            if grouped and self._actions_negotiated:
                snapshot["notifs"] = [
                    {**card, "open": self.action_manager.open_for(int(card["id"]))}
                    for card in snapshot["notifs"]
                ]
            messages = proto.card_sync_messages(
                snapshot,
                self._sync_tx,
                include_dashboard=self._dashboard_capable,
                grouped_session=self.grouped_session if grouped else None,
                include_actions=bool(grouped and self._actions_negotiated),
                include_history=self._history_enabled,
                include_body_styles=self._body_style_enabled,
            )

        # Hold the wire lock across the full transaction, including begin and
        # commit, so pings and IPC output cannot split its staging sequence.
        async with self._wire_lock:
            for message in messages:
                if not await self._write_message(message):
                    break

    def _pairing_status(self) -> dict:
        running = self._pair_task is not None and not self._pair_task.done()
        state = "pairing" if running else (
            "paired" if self._paired_identity is not None else
            "error" if self._pair_load_error else "unpaired"
        )
        return {
            "state": state,
            "serial": self._paired_identity.serial if self._paired_identity else None,
            "error": self._pair_load_error,
            "operation": self._pair_operation,
            "result": self._pair_result,
        }

    def _cancel_pairing(self) -> None:
        if self._pair_cancel is not None:
            self._pair_cancel.set()
        if self._pair_task is not None and not self._pair_task.done():
            self._pair_task.cancel()

    async def _drain_pairing(self) -> None:
        self._cancel_pairing()
        if self._pair_task is not None:
            await asyncio.gather(self._pair_task, return_exceptions=True)

    def _check_pairing(self, operation: int, generation: int, session=None) -> None:
        if (
            operation != self._pair_operation or generation != self._link_port_generation
            or self.stop.is_set() or pause_path().exists()
            or self._pair_cancel is None or self._pair_cancel.is_set()
        ):
            raise LinkError("pairing cancelled or target changed")
        if session is not None and (self._session is not session or self._link_failed):
            raise LinkError("verified connection changed during pairing")

    async def _save_pairing(self, identity, operation: int, generation: int, session=None) -> None:
        # Serialize the commit with configuration mutations. The guard also
        # observes synchronous pause/shutdown while fsync runs off the loop.
        async with self._state_lock:
            self._check_pairing(operation, generation, session)
            save_task = asyncio.create_task(asyncio.to_thread(
                self._pair_store.save, identity,
                before_commit=lambda: self._check_pairing(operation, generation, session),
            ))
            cancelled = False
            while not save_task.done():
                try:
                    await asyncio.shield(save_task)
                except asyncio.CancelledError:
                    cancelled = True
                    self._pair_cancel.set()
                except Exception:
                    break
            try:
                save_task.result()
            except Exception as exc:
                if cancelled:
                    raise asyncio.CancelledError() from exc
                raise
            # A completed rename remains committed even if cancellation
            # arrived immediately afterward. In-memory state must agree.
            self._paired_identity = identity
            self._pair_load_error = None
            if cancelled:
                raise asyncio.CancelledError()

    async def _request_pong(self, timeout: float = PONG_TIMEOUT_S) -> None:
        writer = self._writer
        if writer is None:
            raise LinkError("device is disconnected")
        nonce = secrets.randbelow(0x7ffffffe) + 1
        while nonce in self._pong_waiters:
            nonce = secrets.randbelow(0x7ffffffe) + 1
        waiter = asyncio.get_running_loop().create_future()
        self._pong_waiters[nonce] = waiter
        try:
            async with asyncio.timeout(timeout):
                async with self._wire_lock:
                    if writer is not self._writer:
                        raise LinkError("connection changed before ping")
                    if not await self._write_message({"t": "ping", "ts": nonce}):
                        raise LinkError("could not send ping")
                await waiter
        finally:
            if self._pong_waiters.get(nonce) is waiter:
                self._pong_waiters.pop(nonce, None)
            if not waiter.done():
                waiter.cancel()
            elif not waiter.cancelled():
                waiter.exception()  # Consume a teardown failure before return.

    async def _start_pairing(self, replace: bool) -> dict:
        async with self._state_lock:
            if pause_path().exists():
                return {"ok": False, "error": "daemon is paused; resume before pairing"}
            if self.stop.is_set():
                return {"ok": False, "error": "daemon is stopping"}
            if self._pair_task is not None and not self._pair_task.done():
                return {"ok": False, "error": "pairing already in progress", "operation": self._pair_operation}
            if self._paired_identity is not None and not replace:
                return {"ok": True, "done": True, "serial": self._paired_identity.serial}
            if self._pair_load_error and not replace:
                return {"ok": False, "error": self._pair_load_error + "; use pair --replace to repair"}
            self._pair_operation += 1
            operation = self._pair_operation
            self._pair_result = None
            self._pair_cancel = threading.Event()
            session = self._session if not replace else None
            self._pair_scanning = session is None
            self._pair_task = asyncio.create_task(
                self._pair_device(operation, self._link_port_generation, session), name="usb-pairing"
            )
            self._link_wakeup.set()
            return {"ok": True, "done": False, "operation": operation}

    async def _pair_device(self, operation: int, generation: int, session) -> None:
        result = None
        try:
            self._check_pairing(operation, generation, session)
            if session is not None:
                identity = await asyncio.to_thread(discovery.identity_for_path, session.path)
                if identity is None or (session.identity is not None and identity != session.identity):
                    raise LinkError("connected target has no matching stable USB identity")
                await self._request_pong()
                self._check_pairing(operation, generation, session)
                await self._save_pairing(identity, operation, generation, session)
            else:
                # The normal link loop exclusively releases its session/probe
                # before a setup scan can own any candidate.
                await asyncio.wait_for(self._link_idle.wait(), 5.0)
                self._check_pairing(operation, generation)
                if self._pending_probe is not None:
                    pending, self._pending_probe = self._pending_probe, None
                    await pending[0].close()
                path = self.cfg.link.port
                if path is not None:
                    identity = await asyncio.to_thread(discovery.identity_for_path, path)
                    if identity is None:
                        raise LinkError("explicit target has no stable Espressif USB serial")
                    candidates = [(path, identity)]
                else:
                    available = await asyncio.to_thread(discovery.enumerate_candidates)
                    candidates = [(item.path, item.identity) for item in available]
                failures = []
                for path, identity in candidates:
                    self._check_pairing(operation, generation)
                    if identity is None:
                        failures.append(f"{path}: no stable USB serial")
                        continue
                    try:
                        result = await discovery.probe(path, identity=identity)
                    except Exception as exc:
                        failures.append(f"{path}: {exc}")
                        continue
                    break
                if result is None:
                    detail = "; ".join(failures[-4:]) or "no eligible Espressif USB Serial/JTAG devices"
                    raise LinkError("no compatible 349 found: " + detail)
                if result.identity is None:
                    raise LinkError("verified device has no stable USB serial")
                await self._save_pairing(result.identity, operation, generation)
                self._check_pairing(operation, generation)
                self._pending_probe = (result, generation)
                identity = result.identity
                result = None  # Normal link loop adopts this exact handle.
            self._pair_result = {"operation": operation, "ok": True, "serial": identity.serial}
            log.info("paired USB display %s", identity.serial)
        except asyncio.CancelledError:
            self._pair_result = {"operation": operation, "ok": False, "error": "pairing cancelled"}
            raise
        except Exception as exc:
            self._pair_result = {"operation": operation, "ok": False, "error": str(exc)}
            log.warning("pairing failed: %s", exc)
        finally:
            try:
                if result is not None:
                    await result.close()
            finally:
                self._pair_scanning = False
                self._link_wakeup.set()

    def _link_backoff_bounds(self) -> tuple[float, float]:
        minimum = max(0.05, float(self.cfg.link.reconnect_min_s))
        return minimum, max(minimum, float(self.cfg.link.reconnect_max_s))

    async def _wait_link_wakeup(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._link_wakeup.wait(), timeout=timeout)
        except TimeoutError:
            return False
        self._link_wakeup.clear()
        return True

    async def _read_until_link_change(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port_generation: int
    ) -> None:
        read_task = asyncio.create_task(self._read_loop(reader), name="serial-reader")
        wake_task: asyncio.Task | None = None
        try:
            while True:
                wake_task = asyncio.create_task(self._link_wakeup.wait(), name="link-reconfigure")
                done, _ = await asyncio.wait({read_task, wake_task}, return_when=asyncio.FIRST_COMPLETED)
                if read_task in done:
                    wake_task.cancel()
                    await asyncio.gather(wake_task, return_exceptions=True)
                    wake_task = None
                    await read_task
                    return

                wake_task = None
                self._link_wakeup.clear()
                if (
                    self._link_port_generation != port_generation
                    or pause_path().exists() or self._pair_scanning or self._link_failed
                    or self.stop.is_set()
                ):
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                    return
        finally:
            if wake_task is not None:
                if not wake_task.done():
                    wake_task.cancel()
                await asyncio.gather(wake_task, return_exceptions=True)
            if not read_task.done():
                read_task.cancel()
            await asyncio.gather(read_task, return_exceptions=True)

    def _fail_link(self, reason: str) -> None:
        self._link_error = reason
        self._link_failed = True
        self._link_wakeup.set()

    def _target_current(self, generation: int) -> bool:
        return (
            generation == self._link_port_generation
            and not pause_path().exists() and not self.stop.is_set()
            and not self._pair_scanning
        )

    async def _probe_target(self, path: str, generation: int, identity=None) -> discovery.ProbeResult | None:
        probe_task = asyncio.create_task(discovery.probe(path, identity=identity), name="serial-probe")
        stop_task = asyncio.create_task(self.stop.wait(), name="probe-stop")
        wake_task = None
        result = None
        try:
            while True:
                wake_task = asyncio.create_task(self._link_wakeup.wait(), name="probe-reconfigure")
                done, _ = await asyncio.wait({probe_task, stop_task, wake_task}, return_when=asyncio.FIRST_COMPLETED)
                if not self._target_current(generation):
                    return None
                if probe_task in done:
                    result = await probe_task
                    return result
                self._link_wakeup.clear()
                wake_task.cancel()
                await asyncio.gather(wake_task, return_exceptions=True)
                wake_task = None
        finally:
            if wake_task is not None:
                wake_task.cancel()
            stop_task.cancel()
            if not probe_task.done():
                probe_task.cancel()

            async def cleanup():
                await asyncio.gather(
                    probe_task, stop_task, *([wake_task] if wake_task else []), return_exceptions=True
                )
                if result is None and not probe_task.cancelled():
                    # A successful result racing with pause/reload owns a tty.
                    try:
                        late_result = probe_task.result()
                    except Exception:
                        pass
                    else:
                        await late_result.close()

            cleanup_task = asyncio.create_task(cleanup(), name="probe-cleanup")
            cancelled = False
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup_task.result()
            if cancelled:
                if result is not None:
                    close_task = asyncio.create_task(result.close())
                    while not close_task.done():
                        try:
                            await asyncio.shield(close_task)
                        except asyncio.CancelledError:
                            pass
                    close_task.result()
                raise asyncio.CancelledError()

    async def _clear_link(self, session: discovery.Session) -> None:
        async with self._state_lock:
            if self._session is not session:
                return
            self._session = None
            self._writer = None
            self._active_port = None
            self._last_rx_mono = None
            self._last_pong_mono = None
            self._grouped_enabled = False
            self._history_enabled = False
            self._body_style_enabled = False
            self._actions_capable = False
            self._actions_negotiated = False
            self.action_manager.invalidate_for_link_reset()
            if self._action_pending is not None and not self._action_pending.get("started"):
                self._action_pending["cancelled"] = True
            self._pending_present_id = None
            self._pending_present_due = None
            await self._end_presentation_locked(send_end=False)
            self._grouped_group = "home"
            self._manual_notifications = False
            for waiter in self._pong_waiters.values():
                if not waiter.done():
                    waiter.set_exception(LinkError("serial connection ended"))
            self._pong_waiters.clear()

    async def _link_loop(self) -> None:
        min_backoff, max_backoff = self._link_backoff_bounds()
        backoff = min_backoff
        observed_settings_generation = self._link_settings_generation
        while not self.stop.is_set():
            if observed_settings_generation != self._link_settings_generation:
                observed_settings_generation = self._link_settings_generation
                min_backoff, max_backoff = self._link_backoff_bounds()
                backoff = min_backoff
            self._link_wakeup.clear()
            if self._pending_probe is not None and (
                pause_path().exists() or self._pair_scanning
                or self._pending_probe[1] != self._link_port_generation
            ):
                pending, self._pending_probe = self._pending_probe, None
                self._link_idle.clear()
                try:
                    await pending[0].close()
                finally:
                    self._link_idle.set()
            if pause_path().exists() or self._pair_scanning:
                self._link_idle.set()
                await self._wait_link_wakeup(PORT_SCAN_INTERVAL_S)
                continue

            generation = self._link_port_generation
            result = None
            session = None
            installed = False
            path = self.cfg.link.port
            identity = None
            try:
                if self._pending_probe is not None:
                    pending, self._pending_probe = self._pending_probe, None
                    result, result_generation = pending
                    path = result.path
                    if result_generation != generation:
                        continue
                elif path is None:
                    if self._paired_identity is None:
                        self._link_error = self._pair_load_error or "not paired; run 349ctl pair"
                        self._link_idle.set()
                        await self._wait_link_wakeup(PORT_SCAN_INTERVAL_S)
                        continue
                    candidate = await asyncio.to_thread(discovery.resolve_serial, self._paired_identity.serial)
                    if not self._target_current(generation):
                        continue
                    if candidate is None:
                        self._link_error = "paired device is not connected"
                        self._link_idle.set()
                        await self._wait_link_wakeup(PORT_SCAN_INTERVAL_S)
                        continue
                    path = candidate.path
                    identity = self._paired_identity
                self._link_idle.clear()
                if result is None:
                    result = await self._probe_target(path, generation, identity)
                    if result is None:
                        continue
                if not self._target_current(generation):
                    continue
                session = await discovery.adopt(result)
                result = None  # Session now owns the verified handle.
                await self._prime_sample()
                async with self._state_lock:
                    if not self._target_current(generation):
                        continue
                    self._grouped_enabled = False
                    self._history_enabled = False
                    self._body_style_enabled = False
                    self._actions_capable = False
                    self._actions_negotiated = False
                    self._device_expected_generation = 0
                    self._pending_present_id = None
                    self._pending_present_due = None
                    await self._end_presentation_locked(send_end=False)
                    self._grouped_group = "home"
                    self._manual_notifications = False
                    self._card_sync_capacity = None
                    self._dashboard_capable = False
                    self._sync_tx = 0
                    self._device_boot_id = None
                    self._session = session
                    self._writer = session.writer
                    self._active_port = session.path
                    self._link_failed = False
                    self._link_error = None
                    self._last_rx_mono = self._monotonic()
                    self._last_pong_mono = self._monotonic()
                    installed = True
                    await self._apply_hello_locked(session.hello)
                log.info("verified link up on %s", session.path)
                backoff = min_backoff
                await self._read_until_link_change(session.reader, session.writer, generation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._link_error = str(exc)
                log.warning("link %s failed: %s", path, exc)
            finally:
                try:
                    if installed:
                        await self._clear_link(session)
                finally:
                    try:
                        if session is not None:
                            await session.close()
                        elif result is not None:
                            await result.close()
                    finally:
                        self._link_idle.set()
            if self.stop.is_set() or pause_path().exists() or self._pair_scanning:
                continue
            if generation != self._link_port_generation:
                continue
            if observed_settings_generation != self._link_settings_generation:
                continue
            if await self._wait_link_wakeup(backoff):
                continue
            min_backoff, max_backoff = self._link_backoff_bounds()
            backoff = min(backoff * 2, max_backoff)

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

    async def _apply_hello_locked(self, message: dict) -> None:
        boot_id = message.get("boot_id")
        if isinstance(boot_id, bool) or not isinstance(boot_id, int) or not 1 <= boot_id <= 0xFFFFFFFF:
            boot_id = None
        log.info(
            "device hello: fw=%s build=%s sha=%s proto=%s cap=%s",
            message.get("fw"),
            message.get("build"),
            message.get("build_sha"),
            message.get("proto"),
            message.get("cap"),
        )
        new_capacity = proto.card_sync_capacity(message)
        new_dashboard = new_capacity is not None and proto.dashboard_capable(message)
        new_grouped = new_capacity is not None and proto.grouped_ui_capable(message)
        new_history = new_capacity is not None and proto.notification_history_capable(message)
        new_body_style = proto.notification_body_style_capable(message)
        new_actions_capable = proto.notification_actions_capable(message)
        new_actions_negotiated = (
            self.cfg.notifications.device_open == "dms" and new_actions_capable
        )
        first_grouped = new_grouped and not self._grouped_enabled
        first_history = new_history and not self._history_enabled
        changed_boot = boot_id is not None and boot_id != self._device_boot_id
        capabilities_changed = (
            new_capacity != self._card_sync_capacity
            or new_dashboard != self._dashboard_capable
            or new_grouped != self._grouped_enabled
            or new_history != self._history_enabled
            or new_body_style != self._body_style_enabled
            or new_actions_negotiated != self._actions_negotiated
        )
        self._card_sync_capacity = new_capacity
        self._dashboard_capable = new_dashboard
        self._grouped_enabled = new_grouped
        self._grouped_mode = new_grouped
        self._notification_peer_known = True
        self._history_enabled = new_history
        self._history_mode = new_history
        self._body_style_enabled = new_body_style
        self._actions_capable = new_actions_capable
        self._actions_negotiated = new_actions_negotiated
        if boot_id is not None and boot_id != self._action_ledger_boot_id:
            self._action_ledger_boot_id = boot_id
            self._action_highwater = 0
            self._action_results.clear()
            self._action_pending = None
        self.notifications.actions_changed.set()
        if first_grouped or changed_boot:
            self._device_expected_generation = 0
        if changed_boot:
            self._pending_present_id = None
            self._pending_present_due = None
            await self._end_presentation_locked(send_end=False)
            self._grouped_group = "notifications" if new_history else "home"
            self._manual_notifications = False
        elif first_history:
            self._grouped_group = "notifications"
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
        elif not new_history and not first_grouped:
            self._history_mode = False
        if boot_id is not None and boot_id == self._device_boot_id and not capabilities_changed:
            log.debug("ignoring repeated hello for device boot_id=%s", boot_id)
        else:
            # A missing ID keeps the pre-dedup legacy behavior: every
            # hello requests a fresh full sync.
            self._device_boot_id = boot_id
            await self._send_sync_locked()

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
            if not proto.is_device_hello(message):
                self._fail_link("incompatible device hello")
                return
            await self._prime_sample()
            async with self._state_lock:
                await self._apply_hello_locked(message)
        elif kind == "pong":
            nonce = message.get("ts")
            if type(nonce) not in (int, float):
                return
            waiter = self._pong_waiters.get(nonce)
            if waiter is not None and not waiter.done():
                self._last_pong_mono = self._monotonic()
                waiter.set_result(None)
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
        if action == "activate":
            await self._handle_action_input(message)
            return
        if action == "browse" and self._grouped_enabled:
            session = message.get("session")
            generation = message.get("generation")
            group = message.get("group")
            manual = message.get("manual")
            if (
                isinstance(session, bool)
                or not isinstance(session, int)
                or session != self.grouped_session
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or not 0 <= generation <= SESSION_MAX
                or not isinstance(group, str)
                or group not in {"home", "notifications"}
                or ("manual" in message and not isinstance(manual, bool))
                or ("manual" in message and manual is False and not self._history_enabled)
                or ("manual" in message and manual is True and group != "notifications")
                or ("manual" in message and manual is False and group != "notifications")
            ):
                return
            async with self._state_lock:
                if (
                    not self._grouped_enabled
                    or generation != self._device_expected_generation
                    or ("manual" in message and manual is False and not self._history_enabled)
                ):
                    return
                self._pending_present_id = None
                self._pending_present_due = None
                self._grouped_group = group
                self._manual_notifications = (
                    manual if "manual" in message else group == "notifications"
                )
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
                await self._expire_history_locked(self._monotonic())
                if not self._grouped_enabled or local_id not in self.model.retained_notifs:
                    return
                cached = self.model.cached_notification_ids(self._card_sync_capacity, retained=True)
                if local_id not in cached:
                    return
                overflowed = len(self.model.retained_notifs) > len(cached)
                self._cancel_unsent_action(local_id)
                self.notifications.hide_locally(local_id)
                self._injected_expiry.pop(local_id, None)
                if not self.model.close_notification(local_id):
                    return
                if self._pending_present_id == local_id:
                    self._pending_present_id = None
                    self._pending_present_due = None
                if self._presentation is not None and self._presentation["id"] == local_id:
                    self._presentation = None
                if not self.model.retained_notifs:
                    if not self._history_mode:
                        self._grouped_group = "home"
                    self._manual_notifications = False
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

    @staticmethod
    def _valid_action_identity(message: dict) -> tuple[int, int, int, int, int] | None:
        values = tuple(message.get(name) for name in ("session", "boot_id", "id", "open_rev", "request"))
        session, boot_id, local_id, revision, request = values
        if (
            isinstance(session, bool) or not isinstance(session, int) or not 1 <= session <= SESSION_MAX
            or isinstance(boot_id, bool) or not isinstance(boot_id, int) or not 1 <= boot_id <= 0xFFFFFFFF
            or any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= SESSION_MAX
                   for value in (local_id, revision, request))
        ):
            return None
        return session, boot_id, local_id, revision, request

    def _cancel_unsent_action(self, local_id: int) -> None:
        pending = self._action_pending
        if (
            pending is not None and pending["identity"][2] == local_id
            and not pending.get("started")
        ):
            pending["cancelled"] = True

    def _remember_action_result(self, identity: tuple[int, int, int, int, int], status: str) -> None:
        request = identity[4]
        self._action_results[request] = (identity, status)
        self._action_results.move_to_end(request)
        while len(self._action_results) > ACTION_RESULT_LIMIT:
            self._action_results.popitem(last=False)

    async def _send_action_result(self, identity: tuple[int, int, int, int, int], status: str) -> None:
        session, boot_id, local_id, revision, request = identity
        await self.send(proto.action_result(session, boot_id, local_id, revision, request, status))

    async def _handle_action_input(self, message: dict) -> None:
        identity = self._valid_action_identity(message)
        if identity is None or not self._actions_negotiated:
            return
        session, boot_id, local_id, revision, request = identity
        if session != self.grouped_session or boot_id != self._device_boot_id:
            await self._send_action_result(identity, "stale")
            return

        immediate: str | None = None
        duplicate = False
        refresh_binding = False
        dispatch = False
        async with self._state_lock:
            await self._expire_history_locked(self._monotonic())
            if (
                not self._actions_negotiated or not self._grouped_enabled
                or self._action_ledger_boot_id != boot_id or self._device_boot_id != boot_id
            ):
                immediate = "stale"
            else:
                old = self._action_results.get(request)
                if old is not None:
                    immediate = old[1] if old[0] == identity else "stale"
                    duplicate = old[0] == identity
                elif self._action_pending is not None:
                    pending_identity = self._action_pending["identity"]
                    if pending_identity == identity:
                        return
                    if request <= self._action_highwater:
                        immediate = "stale"
                    else:
                        self._action_highwater = request
                        immediate = "unavailable"
                        self._remember_action_result(identity, immediate)
                        refresh_binding = True
                elif request <= self._action_highwater:
                    immediate = "stale"
                else:
                    self._action_highwater = request
                    cached = self.model.cached_notification_ids(self._card_sync_capacity, retained=True)
                    if local_id not in cached or local_id not in self.model.retained_notifs:
                        immediate = "stale"
                    else:
                        entry = self.action_manager.entries.get(local_id)
                        if entry is None:
                            self.action_manager.open_for(local_id)
                            entry = self.action_manager.entries.get(local_id)
                        if entry is None or entry.revision != revision:
                            immediate = "stale"
                            refresh_binding = True
                        elif entry.state != "ready" or not self.action_manager.ready_binding(
                            local_id, revision, session, boot_id
                        ):
                            immediate = "unavailable" if entry.state != "ready" else "stale"
                            refresh_binding = True
                            self.notifications.actions_changed.set()
                        else:
                            self._action_pending = {
                                "identity": identity,
                                "started": False,
                                "cancelled": False,
                            }
                            self._pending_present_id = None
                            self._pending_present_due = None
                            self._grouped_group = "notifications"
                            self._manual_notifications = True
                            if self._presentation is not None:
                                await self._end_presentation_locked(send_end=True)
                            dispatch = True
                    if immediate is not None:
                        self._remember_action_result(identity, immediate)
                        refresh_binding = True

        if immediate is not None:
            await self._send_action_result(identity, immediate)
            if refresh_binding and not duplicate:
                await self.action_manager.refresh_after_terminal(local_id, revision)
            return
        if dispatch:
            task = asyncio.create_task(self._dispatch_action(identity), name=f"notification-action-{request}")
            self._action_tasks.add(task)
            task.add_done_callback(self._action_task_done)

    def _action_task_done(self, task: asyncio.Task) -> None:
        self._action_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            log.warning("notification action task failed (%s)", type(error).__name__)

    async def _dispatch_action(self, identity: tuple[int, int, int, int, int]) -> None:
        session, boot_id, local_id, revision, request = identity
        immediate: str | None = None
        async with self._state_lock:
            await self._expire_history_locked(self._monotonic())
            pending = self._action_pending
            if (
                pending is None or pending["identity"] != identity
                or self._action_ledger_boot_id != boot_id
            ):
                return
            if pending.get("cancelled") or self._writer is None or not self._actions_negotiated:
                immediate = "unavailable"
            elif not self.action_manager.ready_binding(local_id, revision, session, boot_id):
                immediate = "stale"
            else:
                pending["started"] = True

        if immediate is None:
            try:
                status = await self.action_manager.activate(local_id, revision, request, session, boot_id)
            except Exception as exc:  # No retry: the provider outcome may be uncertain.
                log.warning("notification action handler failed (%s)", type(exc).__name__)
                status = "unknown"
        else:
            status = immediate
        if status != "dispatched":
            try:
                await self.action_manager.refresh_after_terminal(local_id, revision)
            except Exception as exc:
                log.warning("notification action refresh failed (%s)", type(exc).__name__)
        await self._finish_action(identity, status)

    async def _finish_action(self, identity: tuple[int, int, int, int, int], status: str) -> None:
        session, boot_id, local_id, revision, request = identity
        async with self._state_lock:
            if session != self.grouped_session or boot_id != self._action_ledger_boot_id:
                return
            self._remember_action_result(identity, status)
            if self._action_pending is not None and self._action_pending["identity"] == identity:
                self._action_pending = None
            if (
                self._writer is not None and self._actions_negotiated
                and self._device_boot_id == boot_id
            ):
                await self.send(proto.action_result(session, boot_id, local_id, revision, request, status))

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
            old_link_settings = (
                self.cfg.link.port,
                self.cfg.link.reconnect_min_s,
                self.cfg.link.reconnect_max_s,
            )
            apply_config(self.cfg, new)
            if self._port_override is not None:
                self.cfg.link.port = self._port_override
            new_link_settings = (
                self.cfg.link.port,
                self.cfg.link.reconnect_min_s,
                self.cfg.link.reconnect_max_s,
            )
            if old_link_settings != new_link_settings:
                self._link_settings_generation += 1
                self._link_wakeup.set()
            if old_link_settings[0] != new_link_settings[0]:
                self._link_port_generation += 1
                self._cancel_pairing()
            self.model.max_visible = self.cfg.notifications.max_visible
            self.model.cache_limit = self.cfg.notifications.cache_limit
            self.model.retention_s = self.cfg.notifications.retention_s
            await self._expire_history_locked(self._monotonic())
            self._actions_negotiated = (
                self.cfg.notifications.device_open == "dms"
                and self._actions_capable and self._action_ledger_boot_id is not None
            )
            self.notifications.actions_changed.set()
            self._needs_sync = True
            self._tick_wakeup.set()
        await self.notifications.reconfigure()
        log.info("config reloaded from %s", self.cfg_path)
        return True

    def pause(self) -> None:
        pause_path().touch()
        self._cancel_pairing()
        self._link_wakeup.set()
        log.info("paused: releasing serial port for flashing")

    def resume(self) -> None:
        try:
            pause_path().unlink()
        except FileNotFoundError:
            pass
        self._link_wakeup.set()
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
            "active_port": self._active_port if self._writer is not None else None,
            "device_boot_id": self._device_boot_id if self._writer is not None else None,
            "pairing": self._pairing_status(),
            "link_error": self._link_error,
            "rev": self.model.rev,
            "notifs": len(self.model.notifs),
            "retained_notifs": len(self.model.retained_notifs),
            "grouped": self._grouped_enabled,
            "notification_history": self._history_enabled,
            "notification_body_style": self._body_style_enabled,
            "notification_actions": {
                "configured": self.cfg.notifications.device_open == "dms",
                "capable": self._actions_capable,
                "negotiated": self._actions_negotiated,
                "provider": {
                    "available": self.action_manager._provider_epoch is not None,
                    "epoch": self.action_manager._provider_epoch,
                    "pid": self.action_manager._provider_pid,
                },
                "ready": sum(entry.state == "ready" for entry in self.action_manager.entries.values()),
                "pending": self._action_pending is not None,
            },
            "presentation": presentation,
            "last_rx_s": age,
            "dashboard": {
                key: self.model.dashboard.get(key)
                for key in ("cpu", "cpu_freq_mhz", "mem", "mem_used_bytes", "network",
                            "rx_bytes_per_s", "tx_bytes_per_s")
            },
            "config": self.cfg_path,
        }

    async def _ipc_handler(self, request: dict) -> dict:
        cmd = request.get("cmd")
        if cmd == "status":
            return self._status()
        if cmd == "pair":
            replace = request.get("replace", False)
            if type(replace) is not bool:
                return {"ok": False, "error": "pair replace must be a boolean"}
            return await self._start_pairing(replace)
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
            await self._drain_pairing()
            if self._pending_probe is not None:
                pending, self._pending_probe = self._pending_probe, None
                await pending[0].close()
            try:
                await asyncio.wait_for(self._link_idle.wait(), 4.0)
            except TimeoutError:
                return {"ok": False, "error": "paused, but serial cleanup is still in progress"}
            if not pause_path().exists():
                return {"ok": False, "error": "pause was superseded by resume"}
            log.info("serial port released for flashing")
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
        interval_settings = (
            float(self.cfg.daemon.tick_s),
            float(self.cfg.daemon.sync_interval_s),
        )
        now = self._monotonic()
        next_sync = now + interval_settings[1]
        if self._next_sample_mono is None:
            self._next_sample_mono = now

        while True:
            now = self._monotonic()
            wait_s = max(0.0, min(self._next_sample_mono, next_sync) - now)
            if self._injected_expiry:
                wait_s = min(wait_s, max(0.0, min(self._injected_expiry.values()) - now))
            if self._history_mode and self.model.retained_received_mono:
                retention_deadline = min(self.model.retained_received_mono.values()) + float(
                    self.cfg.notifications.retention_s
                )
                wait_s = min(wait_s, max(0.0, retention_deadline - now))
            attention_deadlines = []
            if self._pending_present_due is not None:
                attention_deadlines.append(self._pending_present_due)
            if self._presentation is not None and self._presentation["deadline"] is not None:
                attention_deadlines.append(self._presentation["deadline"])
            if attention_deadlines:
                wait_s = min(wait_s, max(0.0, min(attention_deadlines) - now))
            try:
                # Await in this task so a concurrent wakeup cannot swallow an
                # external cancellation (wait_for can do that on Python 3.11).
                async with asyncio.timeout(wait_s):
                    await self._tick_wakeup.wait()
            except asyncio.TimeoutError:
                pass
            else:
                self._tick_wakeup.clear()

            # A reload may have changed either interval while this wait was
            # active. Only a tick interval change moves the sample deadline.
            tick = float(self.cfg.daemon.tick_s)
            sync_interval = float(self.cfg.daemon.sync_interval_s)
            settings = (tick, sync_interval)
            if settings != interval_settings:
                setting_change_time = self._monotonic()
                next_sync = setting_change_time + sync_interval
                if tick != interval_settings[0]:
                    self._next_sample_mono = setting_change_time + tick
                interval_settings = settings

            now = self._monotonic()
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
                await self._expire_history_locked(self._monotonic())
                await self._expire_presentation_locked(attention_now)
                if self._pending_present_due is not None and self._pending_present_due <= attention_now:
                    await self._publish_pending_presentation_locked(attention_now)

            now = self._monotonic()
            if self._next_sample_mono is not None and now >= self._next_sample_mono:
                try:
                    sample = await self._collect_sample()
                except asyncio.CancelledError:
                    await self._drain_sample_worker()
                    raise
                async with self._state_lock:
                    if self._publish_sample_locked(sample):
                        epoch, offset = self.clock.read()
                        if offset != last_offset:
                            self.model.set_clock(epoch, offset)
                            await self.send(proto.clock(epoch, offset))
                            last_offset = offset

                        await self._update_dashboard_locked(
                            sample.values, sampled_mono=sample.completed_mono
                        )
                        if self.model.set_zones(build_zones(self.cfg.bar.preset, sample.values)):
                            await self.send(proto.bar(self.model.zones, self.model.rev))
                        self._finish_sample_locked(sample)

            now = self._monotonic()
            if now >= next_sync:
                await self._send_sync()
                next_sync = self._monotonic() + float(self.cfg.daemon.sync_interval_s)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            if self._writer is not None:
                try:
                    await self._request_pong()
                except (LinkError, TimeoutError) as exc:
                    log.debug("keepalive failed: %s", exc)
                    if (
                        self._writer is not None and self._last_pong_mono is not None
                        and self._monotonic() - self._last_pong_mono >= LINK_LIVENESS_TIMEOUT_S
                    ):
                        self._fail_link("device stopped answering keepalive pings")


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
