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

from . import discovery, pairing, proto
from .actions import BridgeClient, NotificationActionManager
from .composition import build_zones
from .config import Config, apply_config, load_config, validate_config
from .daemon_actions import ActionDispatchMixin
from .daemon_cards import CardPresentationMixin
from .daemon_link import LinkLifecycleMixin
from .daemon_telemetry import TelemetryMixin, _TelemetrySample
from .ipc import IpcServer, pause_path
from .sources.bluetooth import BluetoothSource
from .sources.clock import ClockSource
from .sources.network import NetworkSource
from .sources.notifications import NotificationSource
from .sources.power import PowerSource
from .sources.screen_power import ScreenPowerSource
from .sources.sysinfo import SysinfoSource
from .sources.volume import VolumeSource
from .state import StateModel

log = logging.getLogger("349d")

SESSION_MAX = proto.IDENTITY_MAX


class Daemon(CardPresentationMixin, LinkLifecycleMixin, ActionDispatchMixin, TelemetryMixin):
    """Own shared state/locks and coordinate the daemon's worker responsibilities."""

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
        self.screen_power = ScreenPowerSource()
        self._screen_power_sample = {"on": None, "outputs": [], "error": None}
        self._backlight_capable = False
        self._backlight_boost_capable = False
        self._last_display_sent: dict | None = None
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
                asyncio.create_task(self._display_loop(), name="display"),
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
            if self._backlight_capable:
                display = self._display_payload()
                if not await self._write_message(display):
                    return
                self._last_display_sent = display
            for message in messages:
                if not await self._write_message(message):
                    break

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
        new_backlight = "backlight-v1" in message.get("cap", [])
        new_backlight_boost = new_backlight and "backlight-boost-v1" in message.get("cap", [])
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
            or new_backlight != self._backlight_capable
            or new_backlight_boost != self._backlight_boost_capable
        )
        self._card_sync_capacity = new_capacity
        self._dashboard_capable = new_dashboard
        self._backlight_capable = new_backlight
        self._backlight_boost_capable = new_backlight_boost
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
                "source": self.notifications.identity_status(),
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
            "display": {
                "capable": self._backlight_capable,
                "boost_capable": self._backlight_boost_capable,
                "brightness_percent": self.cfg.display.brightness_percent,
                "disconnect_timeout_s": self.cfg.display.disconnect_timeout_s,
                "follow_host_screen": self.cfg.display.follow_host_screen,
                "notification_boost_s": self.cfg.display.notification_boost_s,
                "host_screen": self._screen_power_sample,
                "last_sent": self._last_display_sent if self._writer is not None else None,
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
    parser.add_argument("--port", help="serial port override (default: configured port or saved USB pairing)")
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
