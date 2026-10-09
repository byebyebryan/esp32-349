"""Correlated desktop activation requests and their single-dispatch ledger.

Internal methods assembled by Daemon; its shared model and locks remain the
single authority for cross-worker ordering and lifecycle.
"""

from __future__ import annotations

import asyncio
import logging

from . import proto

log = logging.getLogger("349d")
SESSION_MAX = proto.IDENTITY_MAX
ACTION_RESULT_LIMIT = 64


class ActionDispatchMixin:
    """Correlated desktop activation requests and their single-dispatch ledger."""

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
