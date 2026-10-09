"""Verified USB sessions, pairing, wire writes, readback and keepalive.

Internal methods assembled by Daemon; its shared model and locks remain the
single authority for cross-worker ordering and lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading

from . import discovery, proto
from .ipc import pause_path
from .link import LinkError

log = logging.getLogger("349d")
PING_INTERVAL_S = 4.0
PONG_TIMEOUT_S = 2.0
LINK_LIVENESS_TIMEOUT_S = 12.0
WRITE_TIMEOUT_S = 2.0
PORT_SCAN_INTERVAL_S = 0.5
CARD_STATUS_TIMEOUT_S = 2.0


class LinkLifecycleMixin:
    """Verified USB sessions, pairing, wire writes, readback and keepalive."""

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
            self._backlight_capable = False
            self._backlight_boost_capable = False
            self._last_display_sent = None
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
                await self._refresh_display()
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
                    self._backlight_capable = False
                    self._backlight_boost_capable = False
                    self._last_display_sent = None
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
