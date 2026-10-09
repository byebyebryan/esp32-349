"""Retained card lifetime, peer projection and presentation attention.

Internal methods assembled by Daemon; its shared model and locks remain the
single authority for cross-worker ordering and lifecycle.
"""

from __future__ import annotations

import logging
import math
import time

from . import proto

log = logging.getLogger("349d")
NORMAL_PRESENT_COALESCE_S = 0.300
SESSION_MAX = proto.IDENTITY_MAX


class CardPresentationMixin:
    """Retained card lifetime, peer projection and presentation attention."""

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
            clean_message.pop("boost", None)
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
            if (self._backlight_boost_capable and self.cfg.display.notification_boost_s > 0
                    and not self.notifications.attention_expired(local_id)):
                # This is an arrival event, never retained or replayed in sync.
                update["boost"] = True
            sent = await self.send(update)
            if was_grouped and sent and not self.notifications.attention_expired(local_id):
                await self._queue_presentation_locked(clean_message, now)

    def _notification_projection(self, message: dict, *, history: bool, now_mono: float) -> dict | None:
        """Build one peer-specific notification projection without retained internals."""
        projected = {key: value for key, value in message.items() if not key.startswith("_") and key != "history"}
        projected.pop("boost", None)
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
            archived = set(local_ids)
            for local_id in local_ids:
                self._cancel_unsent_action(local_id)
                self.model.close_active_notification(local_id)
            if self._pending_present_id in archived:
                self._pending_present_id = None
                self._pending_present_due = None
            if self._presentation is not None and self._presentation["id"] in archived:
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
