"""Notification mirroring via D-Bus monitoring.

The desktop notification daemon keeps ownership of notifications; we only
eavesdrop. A monitor connection cannot send messages, so a second connection is
kept for `CloseNotification` propagation.

Two bus quirks drive the implementation:

- The assigned notification ID only exists in the unicast method reply to
  `Notify`, which is why the monitor rules include a `method_return` rule and we
  correlate `reply_serial` with the original call serial. The rule is narrowed
  to the notification daemon's sender to keep unrelated traffic out.
- dbus-broker disconnects monitors that do not support unix file descriptors
  when an FD-carrying message matches, so the monitor connection negotiates FD
  support and we close the descriptors we never use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from .. import proto
from ..config import NotificationsConfig
from ..notification_text import convert_body

log = logging.getLogger(__name__)

NOTIFICATIONS_NAME = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"

MONITOR_RULES = [
    "interface='org.freedesktop.Notifications'",
    f"type='method_return',sender='{NOTIFICATIONS_NAME}'",
    f"type='error',sender='{NOTIFICATIONS_NAME}'",
]

# One message per interval keeps a notification burst from overflowing the
# device's 4 KB RX ring.
NOTIFY_RATE_PER_S = 20.0
NOTIFY_OUTBOX_LIMIT = 32
ASSOCIATION_LIMIT = 32
PENDING_REPLY_LIMIT = 64
MONITOR_QUEUE_LIMIT = 256
OPEN_ACTION_PAIR_LIMIT = 64
IDENTITY_QUERY_TIMEOUT_S = 0.5
IDENTITY_RETRY_INITIAL_S = 0.1
IDENTITY_RETRY_MAX_S = 5.0
DISMISS_TIMEOUT_S = 0.5
DISMISS_QUEUE_LIMIT = 32
MAX_NOTIFICATION_SOURCE_IDENTITY_CODEPOINTS = 256
DBUS_OWNER_MATCH = (
    "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
    "member='NameOwnerChanged',arg0='org.freedesktop.Notifications'"
)


@dataclass(frozen=True, slots=True)
class _PendingDismiss:
    local_id: int
    daemon_id: int
    version: int
    owner: str | None
    owner_generation: int
    control: object


@dataclass(frozen=True, slots=True)
class _QueuedMessage:
    message: Message
    owner_generation: int
    owner_change_generation: int | None = None


def parse_open_metadata(body: list) -> dict | None:
    """Keep a bounded, unnormalized default-action validation payload."""
    if len(body) < 8:
        return None
    app, _, _, summary, text, actions, _, _ = body[:8]
    if (
        not all(isinstance(value, str) for value in (app, summary, text))
        or not isinstance(actions, list)
        or len(actions) % 2
        or len(actions) > 2 * OPEN_ACTION_PAIR_LIMIT
        or not all(isinstance(value, str) for value in actions)
    ):
        return None
    defaults = [actions[i + 1] for i in range(0, len(actions), 2) if actions[i] == "default"]
    if len(defaults) != 1:
        return None
    expected = {"app": app, "summary": summary, "body": text, "default_label": defaults[0]}
    # Leave room for the bridge's bounded identity/envelope fields. Do not keep
    # image hints or a second unbounded copy of the notification's body.
    if any(len(value) > proto.LINE_MAX - 1024 for value in expected.values()):
        return None
    try:
        size = len(json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode())
    except UnicodeError:
        return None
    if size > proto.LINE_MAX - 1024:
        return None
    return expected


def is_ignored(app: str, ignore_apps: list[str]) -> bool:
    # When configured privacy filters are active, conservatively hide an
    # oversized app identity. Fully stripping/lowercasing it would make each
    # incoming notification unbounded, and clipping first could miss a padded
    # ignored name such as "KeePassXC" followed by megabytes of whitespace.
    if len(app) > MAX_NOTIFICATION_SOURCE_IDENTITY_CODEPOINTS:
        return bool(ignore_apps)
    app = app.strip().lower()
    return any(app == ignored.strip().lower() for ignored in ignore_apps)


def _source_identity_hint(value: str) -> str:
    """Bound source classification while keeping raw metadata separate."""
    if len(value) <= MAX_NOTIFICATION_SOURCE_IDENTITY_CODEPOINTS:
        return value
    # Never let a clipped suffix manufacture a trusted sender such as "kitty".
    return "oversized-notification-source"


def _source_conversion_hints(hints: object) -> object:
    """Bound the one desktop-entry hint used during source classification."""
    if not isinstance(hints, dict):
        return hints
    desktop_entry = hints.get("desktop-entry")
    value = getattr(desktop_entry, "value", desktop_entry)
    if (
        isinstance(value, str)
        and len(value) > MAX_NOTIFICATION_SOURCE_IDENTITY_CODEPOINTS
    ):
        return {"desktop-entry": _source_identity_hint(value)}
    return hints


def parse_notify_body(body: list) -> dict | None:
    """Map a `Notify` call body to the fields we forward."""
    if len(body) < 8:
        return None
    app_name, replaces_id, _app_icon, summary, body_text, _actions, hints, expire_timeout = body[:8]

    app_text = str(app_name)
    summary_text = str(summary)
    # Source conversion only needs a known short sender name and an exact
    # "Codex" title match. Keep the original values below for wire projection
    # and Open validation, where identity must never come from display text.
    source_summary = (
        summary_text
        if len(summary_text) == len("Codex") and summary_text == "Codex"
        else ""
    )
    display_body, body_runs = convert_body(
        _source_identity_hint(app_text),
        source_summary,
        str(body_text),
        _source_conversion_hints(hints),
    )

    urgency = 1
    if isinstance(hints, dict):
        variant = hints.get("urgency")
        if isinstance(variant, Variant) and isinstance(variant.value, int):
            urgency = int(variant.value)

    parsed = {
        "app": app_text,
        "replaces": int(replaces_id),
        "summary": summary_text,
        "body": display_body,
        "urgency": urgency,
        "expire": int(expire_timeout),
    }
    if body_runs:
        parsed["body_runs"] = body_runs
    return parsed


def parse_closed_body(body: list) -> tuple[int, int] | None:
    if len(body) < 2:
        return None
    return int(body[0]), int(body[1])


def effective_popup_timeout_ms(expire: int, urgency: int, cfg: NotificationsConfig) -> int:
    """A desktop popup may time out without closing its history entry."""
    if expire >= 0:
        return expire
    return cfg.critical_popup_timeout_ms if urgency >= 2 else cfg.popup_timeout_ms


class NotificationSource:
    def __init__(
        self,
        cfg: NotificationsConfig,
        on_notify: Callable[[dict], Awaitable[None]],
        on_close: Callable[[int], Awaitable[None]],
        *,
        on_active_expire: Callable[[int], Awaitable[None]] | None = None,
        on_expire: Callable[[int], Awaitable[None]] | None = None,
        on_monitor_reset: Callable[[list[int]], Awaitable[None]] | None = None,
        grouped_mode: Callable[[], bool] | None = None,
        bounded_mode: Callable[[], bool] | None = None,
        allocate_local_id: Callable[[], int] | None = None,
    ):
        self.cfg = cfg
        self._on_notify = on_notify
        self._on_close = on_close
        self._on_active_expire = on_active_expire or self._noop_expire
        self._on_expire = on_expire or self._noop_expire
        self._on_monitor_reset = on_monitor_reset or self._noop_monitor_reset
        self._grouped_mode = grouped_mode or (lambda: False)
        self._bounded_mode = bounded_mode or self._grouped_mode
        self._allocate_local_id = allocate_local_id

        self._monitor: MessageBus | None = None
        self._control: MessageBus | None = None
        self._messages: asyncio.Queue[Message | _QueuedMessage] = asyncio.Queue(maxsize=MONITOR_QUEUE_LIMIT)
        self._outbox: dict[int, dict] = {}
        self._outbox_ready = asyncio.Event()
        self._monitor_task: asyncio.Task | None = None
        self._identity_task: asyncio.Task | None = None
        self._process_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._dismiss_task: asyncio.Task | None = None
        self._dismiss_queue: asyncio.Queue[_PendingDismiss] = asyncio.Queue(maxsize=DISMISS_QUEUE_LIMIT)

        self._next_id = 1
        self._by_serial: dict[tuple[str, int], tuple[int, int]] = {}
        self._daemon_to_local: dict[int, int] = {}
        self._local_to_daemon: dict[int, int | None] = {}
        self._action_daemon_id: dict[int, int] = {}
        self.actions_changed = asyncio.Event()
        self._open_info: dict[int, dict] = {}
        self._open_reply_versions: dict[tuple[str, int], int] = {}
        self._next_open_version = 1
        self._server_owner: str | None = None
        self._server_pid: int | None = None
        self._control_generation = 0
        self._owner_change_generation = 0
        self._owner_change_applied_generation = 0
        self._observed_owner: str | None = None
        self._control_handler: Callable[[Message], bool] | None = None
        self._identity_wakeup = asyncio.Event()
        self._identity_state = "stopped"
        self._identity_last_error: str | None = None
        self._notification_owner_generations: dict[int, int] = {}
        self._mirrored_local_ids: set[int] = set()
        self._expiry_deadlines: dict[int, float] = {}
        self._locally_removed: dict[int, None] = {}
        self._attention_expired: dict[int, None] = {}
        self._forgotten_local_ids: dict[int, None] = {}
        self.failed = asyncio.Event()
        self.failure: BaseException | None = None

    @staticmethod
    async def _noop_expire(_local_id: int) -> None:
        return None

    @staticmethod
    async def _noop_monitor_reset(_local_ids: list[int]) -> None:
        return None

    def _watch_task(self, task: asyncio.Task) -> asyncio.Task:
        def on_done(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            self.failure = done.exception() or RuntimeError(f"{done.get_name()} stopped unexpectedly")
            self.failed.set()

        task.add_done_callback(on_done)
        return task

    async def start(self) -> None:
        await self.reconfigure()

    async def reconfigure(self) -> None:
        if self.cfg.mode == "off":
            await self.stop()
            await self._close_mirrored_notifications()
            self._discard_messages()
            log.info("notifications disabled")
            return
        if self.cfg.mode != "mirror":
            log.warning("notification mode %r not implemented, staying off", self.cfg.mode)
            await self.stop()
            return
        if self._process_task is not None:
            return
        self._process_task = self._watch_task(asyncio.create_task(self._process_loop(), name="notifications"))
        self._send_task = self._watch_task(asyncio.create_task(self._send_loop(), name="notify-send"))
        self._monitor_task = self._watch_task(asyncio.create_task(self._monitor_loop(), name="notify-monitor"))
        self._ensure_dismiss_worker()

    async def stop(self) -> None:
        tasks = tuple(
            task for task in (self._monitor_task, self._process_task, self._send_task, self._dismiss_task)
            if task is not None
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._monitor_task = self._process_task = self._send_task = self._dismiss_task = None
        self._discard_dismissals()
        await self._teardown()

    async def _close_mirrored_notifications(self) -> None:
        await self._archive_local_notifications(tuple(self._mirrored_local_ids))

    async def _archive_local_notifications(self, local_ids: tuple[int, ...] | list[int]) -> None:
        for local_id in local_ids:
            self._forget_local(local_id)
        if self._grouped_mode():
            await self._on_monitor_reset(list(local_ids))
        else:
            for local_id in local_ids:
                await self._on_close(local_id)

    def _forget_local(self, local_id: int) -> None:
        self._open_info.pop(local_id, None)
        self._notification_owner_generations.pop(local_id, None)
        self.actions_changed.set()
        self._mirrored_local_ids.discard(local_id)
        self._locally_removed.pop(local_id, None)
        self._attention_expired.pop(local_id, None)
        self._forgotten_local_ids[local_id] = None
        while len(self._forgotten_local_ids) > ASSOCIATION_LIMIT:
            self._forgotten_local_ids.pop(next(iter(self._forgotten_local_ids)))
        self._expiry_deadlines.pop(local_id, None)
        self._outbox.pop(local_id, None)
        self._local_to_daemon.pop(local_id, None)
        self._action_daemon_id.pop(local_id, None)
        for daemon_id, mapped_id in tuple(self._daemon_to_local.items()):
            if mapped_id == local_id:
                self._daemon_to_local.pop(daemon_id, None)
        for key, (pending_id, _requested_id) in tuple(self._by_serial.items()):
            if pending_id == local_id:
                self._by_serial.pop(key, None)
                self._open_reply_versions.pop(key, None)

    def forget(self, local_id: int) -> None:
        """Release all bounded source metadata for an evicted/removed record."""
        self._forget_local(int(local_id))

    def hide_locally(self, local_id: int) -> None:
        """Suppress queued delivery while preserving a live desktop association."""
        local_id = int(local_id)
        self._outbox.pop(local_id, None)
        self.actions_changed.set()
        if local_id in self._mirrored_local_ids:
            self._locally_removed[local_id] = None
            while len(self._locally_removed) > ASSOCIATION_LIMIT:
                self._locally_removed.pop(next(iter(self._locally_removed)))

    def is_locally_removed(self, local_id: int) -> bool:
        return int(local_id) in self._locally_removed

    def attention_expired(self, local_id: int) -> bool:
        return int(local_id) in self._attention_expired

    def is_forgotten(self, local_id: int) -> bool:
        return int(local_id) in self._forgotten_local_ids

    def _mark_attention_expired(self, local_id: int) -> None:
        self._attention_expired[local_id] = None
        while len(self._attention_expired) > ASSOCIATION_LIMIT:
            self._attention_expired.pop(next(iter(self._attention_expired)))

    def _trim_associations(self) -> list[int]:
        trimmed: list[int] = []
        if not self._bounded_mode():
            return trimmed
        while len(self._local_to_daemon) > ASSOCIATION_LIMIT:
            oldest = next(iter(self._local_to_daemon))
            self._forget_local(oldest)
            trimmed.append(oldest)
        return trimmed

    def enforce_grouped_bounds(self) -> list[int]:
        """Drop pre-negotiation desktop associations beyond the grouped bound."""
        return self._trim_associations()

    def _trim_pending_replies(self) -> None:
        while len(self._by_serial) > PENDING_REPLY_LIMIT:
            key = next(iter(self._by_serial))
            self._by_serial.pop(key)
            self._open_reply_versions.pop(key, None)

    def action_candidate(self, local_id: int) -> dict | None:
        """Return only a confirmed current server association, never history."""
        info = self._open_info.get(local_id)
        desktop_id = self._action_daemon_id.get(local_id)
        if (
            info is None or info["expected"] is None or not info["confirmed"]
            or self._server_owner is None or self._server_pid is None
            or info.get("owner") != self._server_owner
            or info.get("owner_generation") != self._owner_change_generation
            or info.get("reply_generation") != self._owner_change_generation
            or self._notification_owner_generations.get(local_id) != self._owner_change_generation
            or desktop_id is None or self._daemon_to_local.get(desktop_id) != local_id
            or not 0 < desktop_id <= 0xFFFFFFFF or isinstance(desktop_id, bool)
            or not 0 < info["version"] <= proto.IDENTITY_MAX
            or local_id in self._locally_removed or local_id in self._forgotten_local_ids
        ):
            return None
        return {
            "id": local_id, "version": info["version"], "desktop_id": desktop_id,
            "owner": self._server_owner, "pid": self._server_pid,
            "expected": dict(info["expected"]),
        }

    def action_candidates(self) -> dict[int, dict]:
        return {nid: candidate for nid in self._open_info if (candidate := self.action_candidate(nid)) is not None}

    def identity_status(self) -> dict:
        """Return bounded provider identity state for read-only diagnostics."""
        return {
            "state": self._identity_state,
            "owner": self._server_owner,
            "pid": self._server_pid,
            "last_error": self._identity_last_error,
        }

    def _set_identity_status(
        self, state: str, error: str | None, owner: str | None = None, pid: int | None = None
    ) -> None:
        updated = (state, error, owner, pid)
        current = (self._identity_state, self._identity_last_error, self._server_owner, self._server_pid)
        if updated == current:
            return
        self._identity_state, self._identity_last_error, self._server_owner, self._server_pid = updated
        if state == "ready":
            log.info("notification server identity ready: owner=%s pid=%s", owner, pid)
        self.actions_changed.set()

    async def _apply_identity_evidence(
        self, owner: str, owner_generation: int,
        control: object | None = None, control_generation: int | None = None,
    ) -> None:
        stale_pending: list[int] = []
        control = self._control if control is None else control
        control_generation = self._control_generation if control_generation is None else control_generation
        if (
            self._identity_state != "ready" or self._server_owner != owner
            or self._owner_change_generation != owner_generation
            or self._control is not control
            or self._control_generation != control_generation
        ):
            return
        # Finish association mutations before invoking callbacks. A callback
        # may yield to another owner change, replacement or full teardown.
        archived: list[int] = []
        for local_id, info in tuple(self._open_info.items()):
            if self._open_info.get(local_id) is not info or local_id not in self._mirrored_local_ids:
                continue
            reply_owner = info.get("reply_owner")
            owner_evidence_matches = bool(
                reply_owner == owner
                and info.get("reply_generation") == owner_generation
                and info.get("owner_generation") == owner_generation
                and local_id in self._mirrored_local_ids
            )
            staged_id = info.get("staged_reply") is True
            if staged_id and info.get("owner_generation") == owner_generation:
                if reply_owner != owner or info.get("reply_generation") != owner_generation:
                    if reply_owner is not None:
                        stale_pending.append(local_id)
                    continue
                desktop_id = info.get("reply_desktop_id")
                if (
                    not isinstance(desktop_id, int) or isinstance(desktop_id, bool)
                    or not 0 < desktop_id <= 0xFFFFFFFF
                ):
                    stale_pending.append(local_id)
                    continue
                previous_local = self._daemon_to_local.get(desktop_id)
                if previous_local is not None and previous_local != local_id:
                    self._forget_local(previous_local)
                    archived.append(previous_local)
                previous_id = self._local_to_daemon.get(local_id)
                if previous_id is not None and previous_id != desktop_id:
                    if self._daemon_to_local.get(previous_id) == local_id:
                        self._daemon_to_local.pop(previous_id, None)
                    self._action_daemon_id.pop(local_id, None)
                self._daemon_to_local[desktop_id] = local_id
                self._local_to_daemon[local_id] = desktop_id
                self._action_daemon_id[local_id] = desktop_id
                info["staged_reply"] = False
                owner_evidence_matches = bool(
                    reply_owner == owner
                    and info.get("reply_generation") == owner_generation
                    and info.get("owner_generation") == owner_generation
                    and local_id in self._mirrored_local_ids
                )
            desktop_id = self._local_to_daemon.get(local_id)
            current_association = bool(
                desktop_id is not None
                and self._action_daemon_id.get(local_id) == desktop_id
                and self._daemon_to_local.get(desktop_id) == local_id
                and local_id in self._mirrored_local_ids
            )
            if owner_evidence_matches and current_association:
                info["owner"] = owner
                info["identity_pending"] = False
                if info.get("expected") is not None:
                    info["confirmed"] = True
            elif (
                info.get("identity_pending") is True
                and reply_owner is not None and reply_owner != owner
            ):
                stale_pending.append(local_id)
        archived.extend(stale_pending)
        if archived:
            await self._archive_local_notifications(archived)

    @staticmethod
    async def _identity_call(control: object, message: Message) -> Message:
        try:
            async with asyncio.timeout(IDENTITY_QUERY_TIMEOUT_S):
                return await control.call(message)
        except (asyncio.CancelledError, TimeoutError):
            NotificationSource._discard_cancelled_call(control, message)
            raise

    async def _refresh_server_identity(
        self,
        control: object | None = None,
        control_generation: int | None = None,
        owner_generation: int | None = None,
    ) -> bool:
        """Take one fenced identity snapshot; return false until fully verified."""
        control = self._control if control is None else control
        control_generation = self._control_generation if control_generation is None else control_generation
        owner_generation = self._owner_change_generation if owner_generation is None else owner_generation
        if control is None or self._control is not control:
            return False
        self._set_identity_status("resolving", None)

        def still_current() -> bool:
            return (
                self._control is control
                and self._control_generation == control_generation
                and self._owner_change_generation == owner_generation
            )

        error_code: str | None = None
        owner: str | None = None
        pid: int | None = None
        try:
            owner_reply = await self._identity_call(control, Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="GetNameOwner",
                signature="s", body=[NOTIFICATIONS_NAME],
            ))
            if not still_current():
                return False
            if owner_reply.message_type == MessageType.ERROR:
                error_code = (
                    "name_not_owned"
                    if owner_reply.error_name == "org.freedesktop.DBus.Error.NameHasNoOwner"
                    else "owner_lookup_failed"
                )
            elif (
                not owner_reply.body or not isinstance(owner_reply.body[0], str)
                or not owner_reply.body[0].startswith(":")
            ):
                error_code = "invalid_owner_reply"
            else:
                owner = owner_reply.body[0]
                pid_reply = await self._identity_call(control, Message(
                    destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus", member="GetConnectionUnixProcessID",
                    signature="s", body=[owner],
                ))
                if not still_current():
                    return False
                if pid_reply.message_type == MessageType.ERROR:
                    error_code = "pid_lookup_failed"
                elif (
                    not pid_reply.body or not isinstance(pid_reply.body[0], int)
                    or isinstance(pid_reply.body[0], bool) or pid_reply.body[0] <= 0
                ):
                    error_code = "invalid_pid_reply"
                else:
                    pid = pid_reply.body[0]
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            error_code = "lookup_timeout"
        except Exception:
            error_code = "lookup_failed"

        if not still_current():
            return False
        if error_code is not None or owner is None or pid is None:
            self._set_identity_status("retrying", error_code or "lookup_failed")
            return False

        self._set_identity_status("ready", None, owner, pid)
        # Replies received while a lookup was unavailable remain inert until
        # this snapshot proves their actual service owner and epoch.
        await self._apply_identity_evidence(owner, owner_generation)
        self.actions_changed.set()
        return True

    async def _identity_loop(self, control: object, control_generation: int) -> None:
        backoff = IDENTITY_RETRY_INITIAL_S
        while self._control is control and self._control_generation == control_generation:
            self._identity_wakeup.clear()
            owner_generation = self._owner_change_generation
            succeeded = await self._refresh_server_identity(
                control, control_generation, owner_generation
            )
            if self._identity_wakeup.is_set():
                continue
            if succeeded:
                backoff = IDENTITY_RETRY_INITIAL_S
                await self._identity_wakeup.wait()
                continue
            else:
                delay = backoff
                backoff = min(backoff * 2, IDENTITY_RETRY_MAX_S)
            try:
                await asyncio.wait_for(self._identity_wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _expire_due(self) -> None:
        now = time.monotonic()
        for local_id, deadline in tuple(self._expiry_deadlines.items()):
            if deadline <= now and self._expiry_deadlines.get(local_id) == deadline:
                self._expiry_deadlines.pop(local_id, None)
                if self._grouped_mode():
                    self._mark_attention_expired(local_id)
                    await self._on_active_expire(local_id)
                else:
                    self._outbox.pop(local_id, None)
                    self._forget_local(local_id)
                    await self._on_close(local_id)

    async def dismiss(self, local_id: int) -> None:
        daemon_id = self._action_daemon_id.get(local_id)
        if self.cfg.device_dismiss != "propagate":
            log.debug("dismiss %d is local-only", local_id)
            return
        control = self._control
        info = self._open_info.get(local_id)
        version = info.get("version") if info is not None else None
        if (
            daemon_id is None or control is None or self._identity_state != "ready"
            or self._server_owner is None
            or self._daemon_to_local.get(daemon_id) != local_id
            or self._local_to_daemon.get(local_id) != daemon_id
            or not isinstance(version, int)
            or info is None or info.get("owner") != self._server_owner
            or info.get("owner_generation") != self._owner_change_generation
            or info.get("reply_generation") != self._owner_change_generation
            or info.get("reply_owner") != self._server_owner
            or info.get("identity_pending") is True
            or self._notification_owner_generations.get(local_id) != self._owner_change_generation
        ):
            log.debug("dismiss %d has no daemon id to propagate", local_id)
            return
        pending = _PendingDismiss(
            local_id, daemon_id, version, self._server_owner,
            self._owner_change_generation, control,
        )
        self._ensure_dismiss_worker()
        try:
            self._dismiss_queue.put_nowait(pending)
        except asyncio.QueueFull:
            log.warning("CloseNotification(%d) queue full; dropping dismissal", daemon_id)

    def _ensure_dismiss_worker(self) -> None:
        if self._dismiss_task is None:
            self._dismiss_task = self._watch_task(
                asyncio.create_task(self._dismiss_loop(), name="notify-dismiss")
            )

    def _discard_dismissals(self) -> None:
        while True:
            try:
                self._dismiss_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            else:
                self._dismiss_queue.task_done()

    def _dismiss_is_current(self, pending: _PendingDismiss) -> bool:
        info = self._open_info.get(pending.local_id)
        return bool(
            self.cfg.device_dismiss == "propagate"
            and self._control is pending.control
            and self._identity_state == "ready"
            and self._server_owner is not None
            and self._server_owner == pending.owner
            and self._owner_change_generation == pending.owner_generation
            and self._action_daemon_id.get(pending.local_id) == pending.daemon_id
            and self._local_to_daemon.get(pending.local_id) == pending.daemon_id
            and self._daemon_to_local.get(pending.daemon_id) == pending.local_id
            and pending.local_id in self._mirrored_local_ids
            and info is not None and info.get("version") == pending.version
            and info.get("owner") == pending.owner
            and info.get("owner_generation") == pending.owner_generation
            and info.get("reply_generation") == pending.owner_generation
            and info.get("reply_owner") == pending.owner
            and info.get("identity_pending") is not True
            and self._notification_owner_generations.get(pending.local_id) == pending.owner_generation
        )

    async def _dispatch_dismiss(self, pending: _PendingDismiss, message: Message) -> bool:
        # Check immediately before MessageBus.call sends, even if a caller
        # has yielded since dequeueing this request.
        if not self._dismiss_is_current(pending):
            return False
        await pending.control.call(message)
        return True

    @staticmethod
    def _discard_cancelled_call(control: object, message: Message) -> None:
        """Release dbus-next's waiter entry when cancellation cannot do so."""
        serial = message.serial
        handlers = getattr(control, "_method_return_handlers", None)
        if isinstance(serial, int) and serial > 0 and isinstance(handlers, dict):
            handlers.pop(serial, None)

    async def _dismiss_loop(self) -> None:
        while True:
            pending = await self._dismiss_queue.get()
            try:
                if not self._dismiss_is_current(pending):
                    continue
                message = Message(
                    # Pin every request to the identity captured at enqueue.
                    destination=pending.owner,
                    path=NOTIFICATIONS_PATH,
                    interface=NOTIFICATIONS_NAME,
                    member="CloseNotification",
                    signature="u",
                    body=[pending.daemon_id],
                )
                try:
                    # Keep cancellation in the worker task; Python 3.11's
                    # wait_for can lose it when a reply completes concurrently.
                    async with asyncio.timeout(DISMISS_TIMEOUT_S):
                        await self._dispatch_dismiss(pending, message)
                except asyncio.CancelledError:
                    self._discard_cancelled_call(pending.control, message)
                    raise
                except TimeoutError:
                    self._discard_cancelled_call(pending.control, message)
                    log.warning(
                        "CloseNotification(%d) timed out after %.1fs",
                        pending.daemon_id,
                        DISMISS_TIMEOUT_S,
                    )
                except Exception as exc:
                    log.warning("CloseNotification(%d) failed: %s", pending.daemon_id, exc)
            finally:
                self._dismiss_queue.task_done()

    @staticmethod
    def _is_notification_owner_change(message: Message) -> bool:
        return bool(
            message.message_type == MessageType.SIGNAL
            and message.sender == "org.freedesktop.DBus"
            and message.path == "/org/freedesktop/DBus"
            and message.interface == "org.freedesktop.DBus"
            and message.member == "NameOwnerChanged"
            and message.signature == "sss"
            and len(message.body) == 3
            and message.body[0] == NOTIFICATIONS_NAME
            and isinstance(message.body[1], str)
            and isinstance(message.body[2], str)
        )

    async def _monitor_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._setup()
                backoff = 1.0
                disconnected = await self._wait_for_connection_disconnect()
                log.warning("notification %s connection disconnected", disconnected)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("notifications unavailable: %s", exc)
            await self._teardown()
            # Once monitoring was interrupted, desktop IDs can no longer be
            # trusted. Remove the cards before accepting a new monitor session.
            await self._close_mirrored_notifications()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _wait_for_connection_disconnect(self) -> str:
        control = self._control
        monitor = self._monitor
        if control is None or monitor is None:
            raise RuntimeError("notification connections are not ready")
        waiters = {
            asyncio.create_task(control.wait_for_disconnect(), name="notify-control-disconnect"): "control",
            asyncio.create_task(monitor.wait_for_disconnect(), name="notify-monitor-disconnect"): "monitor",
        }
        identity_task = self._identity_task
        if identity_task is not None:
            waiters[identity_task] = "identity"
        try:
            done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            first_name = waiters[next(iter(done))]
            for task in done:
                await task
                if task is identity_task:
                    raise RuntimeError("notification identity worker stopped")
            return first_name
        finally:
            # The parent monitor loop can be cancelled while asyncio.wait is
            # suspended (notably during stop); collect both bus waiters then.
            for task in waiters:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _setup(self) -> None:
        control = await MessageBus(bus_type=BusType.SESSION).connect()
        self._control = control
        self._control_generation += 1
        control_generation = self._control_generation

        def control_handler(message: Message) -> bool:
            return self._handle_control_message(control, control_generation, message)

        self._control_handler = control_handler
        control.add_message_handler(control_handler)
        match_reply = await self._identity_call(control, Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch",
            signature="s", body=[DBUS_OWNER_MATCH],
        ))
        if match_reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"NameOwnerChanged AddMatch failed: {match_reply.error_name}")

        self._monitor = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()
        reply = await self._identity_call(self._monitor,
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus.Monitoring",
                member="BecomeMonitor",
                signature="asu",
                body=[MONITOR_RULES, 0],
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"BecomeMonitor failed: {reply.error_name} {reply.body}")

        def monitor_handler(message: Message) -> bool:
            return self._enqueue(message)

        self._monitor.add_message_handler(monitor_handler)
        self._set_identity_status("resolving", None)
        self._identity_task = self._watch_task(asyncio.create_task(
            self._identity_loop(control, control_generation), name="notify-identity"
        ))
        log.info("notification mirror active")

    def _handle_control_message(
        self, control: object, control_generation: int, message: Message
    ) -> bool:
        # Do not consume unrelated method replies: dbus-next's MessageBus.call
        # needs them to complete AddMatch and identity RPCs.
        if (
            self._control is not control
            or self._control_generation != control_generation
            or not self._is_notification_owner_change(message)
        ):
            return False
        old_owner, new_owner = message.body[1], message.body[2]
        if old_owner != new_owner:
            log.info("notification server owner changed: %s -> %s", old_owner or "none", new_owner or "none")
            self._owner_change_generation += 1
            self._observed_owner = new_owner
            self._set_identity_status(
                "resolving" if new_owner else "unavailable",
                None if new_owner else "name_not_owned",
            )
            self._identity_wakeup.set()
            self._enqueue(message, owner_change_generation=self._owner_change_generation)
        return True

    def _enqueue(self, message: Message, *, owner_change_generation: int | None = None) -> bool:
        # Returning True marks eavesdropped method calls handled and prevents
        # dbus-next from auto-replying UNKNOWN_METHOD. Received FDs are unused.
        for fd in message.unix_fds or []:
            try:
                os.close(fd)
            except OSError:
                pass
        queued = _QueuedMessage(
            message,
            owner_generation=(
                owner_change_generation
                if owner_change_generation is not None
                else self._owner_change_generation
            ),
            owner_change_generation=owner_change_generation,
        )
        try:
            self._messages.put_nowait(queued)
        except asyncio.QueueFull:
            log.error("notification monitor queue overflow; resetting monitor correlation")
            self._discard_messages()
            if self._monitor is not None:
                self._monitor.disconnect()
        return True

    async def _teardown(self) -> None:
        self._discard_dismissals()
        # Revoke identity before cancellation/cleanup yields to other workers.
        self._control_generation += 1
        self._owner_change_generation += 1
        self._observed_owner = None
        self._set_identity_status("stopped", None)
        self._identity_wakeup.set()
        identity_task = self._identity_task
        self._identity_task = None
        if identity_task is not None and not identity_task.done():
            identity_task.cancel()
            await asyncio.gather(identity_task, return_exceptions=True)
        for bus in (self._monitor, self._control):
            if bus is not None:
                handler = self._control_handler if bus is self._control else None
                if handler is not None:
                    try:
                        bus.remove_message_handler(handler)
                    except Exception:
                        pass
                try:
                    bus.disconnect()
                except Exception:
                    pass
        self._monitor = self._control = None
        self._control_handler = None
        # Ids from a previous daemon session are meaningless now.
        self._by_serial.clear()
        self._open_info.clear()
        self._open_reply_versions.clear()
        self._set_identity_status("stopped", None)
        self.actions_changed.set()
        self._daemon_to_local.clear()
        self._local_to_daemon.clear()
        self._action_daemon_id.clear()
        self._notification_owner_generations.clear()
        self._outbox.clear()
        self._outbox_ready.clear()
        self._expiry_deadlines.clear()
        self._discard_messages()

    def _discard_messages(self) -> None:
        while not self._messages.empty():
            try:
                self._messages.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _process_loop(self) -> None:
        while True:
            await self._expire_due()
            timeout = None
            if self._expiry_deadlines:
                timeout = max(0.0, min(self._expiry_deadlines.values()) - time.monotonic())
            try:
                message = await self._messages.get() if timeout is None else await asyncio.wait_for(
                    self._messages.get(), timeout=timeout
                )
            except asyncio.TimeoutError:
                continue
            try:
                if isinstance(message, _QueuedMessage):
                    if message.owner_change_generation is not None:
                        await self._handle_owner_change(
                            message.message, message.owner_change_generation
                        )
                    else:
                        await self._handle(
                            message.message, owner_generation=message.owner_generation
                        )
                else:
                    await self._handle(message)
            except Exception:
                log.exception("notification handling failed")

    def _has_pending_notify_reply(self, local_id: int, version: int) -> bool:
        return any(
            pending_local == local_id and self._open_reply_versions.get(key) == version
            for key, (pending_local, _requested_id) in self._by_serial.items()
        )

    async def _handle_owner_change(self, message: Message, generation: int | None = None) -> None:
        if not self._is_notification_owner_change(message):
            return
        old_owner, new_owner = message.body[1], message.body[2]
        if old_owner == new_owner:
            return
        if generation is None:
            self._owner_change_generation += 1
            generation = self._owner_change_generation
            self._observed_owner = new_owner
            self._set_identity_status(
                "resolving" if new_owner else "unavailable",
                None if new_owner else "name_not_owned",
            )
            self._identity_wakeup.set()
        elif generation > self._owner_change_generation:
            self._owner_change_generation = generation
            self._observed_owner = new_owner
            self._set_identity_status(
                "resolving" if new_owner else "unavailable",
                None if new_owner else "name_not_owned",
            )
            self._identity_wakeup.set()
        if generation <= self._owner_change_applied_generation:
            return

        # The control socket can observe loss/gain before the monitor socket's
        # queued Notify/reply pair. Use the latest owner event as the fence so
        # fresh final-owner evidence survives intermediate loss notifications.
        latest_generation = self._owner_change_generation
        latest_owner = self._observed_owner
        archive_ids: list[int] = []
        for local_id in tuple(self._mirrored_local_ids):
            info = self._open_info.get(local_id)
            source_generation = self._notification_owner_generations.get(local_id, -1)
            fresh_epoch = source_generation >= latest_generation
            current_owner_reply = bool(
                latest_owner and info is not None
                and info.get("reply_owner") == latest_owner
                and info.get("owner") is None
            )
            pending_reply = bool(
                info is not None and self._has_pending_notify_reply(local_id, info["version"])
            )
            if fresh_epoch or current_owner_reply or pending_reply:
                self._notification_owner_generations[local_id] = latest_generation
                if info is not None:
                    info["owner_generation"] = latest_generation
                    info["confirmed"] = False
                    info["owner"] = None
                    if current_owner_reply:
                        info["reply_generation"] = latest_generation
                    info["identity_pending"] = bool(
                        info.get("expected") is not None
                        and (pending_reply or current_owner_reply or fresh_epoch)
                    )
                # An unreturned Notify cannot keep an earlier owner's desktop
                # ID. Its eventual reply will establish a fresh association.
                if pending_reply and not current_owner_reply:
                    for daemon_id, mapped_id in tuple(self._daemon_to_local.items()):
                        if mapped_id == local_id:
                            self._daemon_to_local.pop(daemon_id, None)
                    self._local_to_daemon[local_id] = None
                    self._action_daemon_id.pop(local_id, None)
            else:
                archive_ids.append(local_id)

        self._owner_change_applied_generation = generation
        if archive_ids:
            await self._archive_local_notifications(archive_ids)
        if latest_owner and self._identity_state == "ready" and self._server_owner == latest_owner:
            await self._apply_identity_evidence(latest_owner, latest_generation)
        self.actions_changed.set()

    async def _handle(self, message: Message, *, owner_generation: int | None = None) -> None:
        if self._is_notification_owner_change(message):
            await self._handle_owner_change(message)
        elif (
            message.message_type == MessageType.METHOD_CALL
            and message.interface == NOTIFICATIONS_NAME
            and message.member == "Notify"
        ):
            await self._handle_notify(message, owner_generation=owner_generation)
        elif message.message_type in {MessageType.METHOD_RETURN, MessageType.ERROR}:
            key = (message.destination, message.reply_serial) if message.destination and message.reply_serial else None
            pending = self._by_serial.pop(key, None) if key is not None else None
            open_version = self._open_reply_versions.pop(key, None) if key is not None else None
            if pending is None:
                return
            local_id, requested_id = pending
            if local_id not in self._local_to_daemon:
                return
            info = self._open_info.get(local_id)
            if info is not None and info.get("version") != open_version:
                return
            if message.message_type == MessageType.ERROR:
                # A failed replacement must not invalidate the still-live
                # desktop notification it tried to update. Remove only a
                # provisional association for a record without a confirmed ID.
                if (
                    requested_id
                    and self._local_to_daemon.get(local_id) is None
                    and self._daemon_to_local.get(requested_id) == local_id
                ):
                    self._daemon_to_local.pop(requested_id, None)
                    self._local_to_daemon[local_id] = None
                return
            daemon_id = None
            if (
                message.body and isinstance(message.body[0], int)
                and not isinstance(message.body[0], bool)
                and 0 < message.body[0] <= 0xFFFFFFFF
            ):
                daemon_id = message.body[0]
            info = self._open_info.get(local_id)
            reply_owner = (
                message.sender
                if isinstance(message.sender, str) and message.sender.startswith(":")
                else None
            )
            if info is not None and info.get("version") == open_version:
                evidence_generation = (
                    self._owner_change_generation
                    if reply_owner is not None and reply_owner == self._observed_owner
                    else self._owner_change_generation if owner_generation is None else owner_generation
                )
                info["reply_owner"] = reply_owner
                info["reply_generation"] = evidence_generation
                info["reply_desktop_id"] = daemon_id

                if reply_owner is not None:
                    known_owner = (
                        self._observed_owner
                        if self._observed_owner is not None
                        else self._server_owner if self._identity_state == "ready" else None
                    )
                    if (
                        known_owner is not None
                        and reply_owner != known_owner
                    ):
                        # A fast owner replacement can produce the new
                        # service's reply before the control socket reports
                        # its owner event. Hold its ID until that owner is
                        # observed and independently validated.
                        info["identity_pending"] = True
                        info["staged_reply"] = daemon_id is not None
                        self.actions_changed.set()
                        return
                    if (
                        self._identity_state != "ready"
                        and self._observed_owner is not None
                        and daemon_id is not None
                    ):
                        mapped_local = self._daemon_to_local.get(daemon_id)
                        mapped_generation = self._notification_owner_generations.get(mapped_local)
                        if mapped_local not in {None, local_id} and mapped_generation != self._owner_change_generation:
                            info["identity_pending"] = True
                            info["staged_reply"] = True
                            self.actions_changed.set()
                            return

            archive_ids: list[int] = []
            if daemon_id is not None:
                if requested_id and self._daemon_to_local.get(requested_id) not in {None, local_id}:
                    requested_local = self._daemon_to_local.get(requested_id)
                    requested_generation = self._notification_owner_generations.get(requested_local)
                    if not (self._observed_owner is not None and requested_generation != self._owner_change_generation):
                        return
                if requested_id and requested_id != daemon_id and self._daemon_to_local.get(requested_id) == local_id:
                    self._daemon_to_local.pop(requested_id, None)
                previous_local = self._daemon_to_local.get(daemon_id)
                if previous_local is not None and previous_local != local_id:
                    previous_generation = self._notification_owner_generations.get(previous_local)
                    if (
                        self._observed_owner is not None
                        and previous_generation != self._owner_change_generation
                    ):
                        self._forget_local(previous_local)
                        archive_ids.append(previous_local)
                    else:
                        # A daemon ID can be reused after a desktop close. Its
                        # current Notify reply transfers correlation ownership.
                        self._forget_local(previous_local)
                self._daemon_to_local[daemon_id] = local_id
                previous_id = self._local_to_daemon.get(local_id)
                if previous_id is not None and previous_id != daemon_id:
                    self._action_daemon_id.pop(local_id, None)
                self._local_to_daemon[local_id] = daemon_id
                self._action_daemon_id[local_id] = daemon_id
                info = self._open_info.get(local_id)
                if info is not None and info.get("version") == open_version:
                    info["staged_reply"] = False
                    if (
                        reply_owner is not None and self._identity_state == "ready"
                        and reply_owner == self._server_owner
                        and evidence_generation == self._owner_change_generation
                        and info.get("owner_generation") == self._owner_change_generation
                    ):
                        info["owner"] = reply_owner
                        info["identity_pending"] = False
                        if info.get("expected") is not None:
                            info["confirmed"] = True
                    self.actions_changed.set()
            elif requested_id and self._daemon_to_local.get(requested_id) == local_id:
                self._daemon_to_local.pop(requested_id, None)
                self._local_to_daemon[local_id] = None
            if archive_ids:
                await self._archive_local_notifications(archive_ids)
        elif (
            message.message_type == MessageType.SIGNAL
            and message.interface == NOTIFICATIONS_NAME
            and message.member == "NotificationClosed"
        ):
            parsed = parse_closed_body(message.body)
            if parsed is not None:
                daemon_id, reason = parsed
                local_id = self._daemon_to_local.get(daemon_id)
                if local_id is not None:
                    self._expiry_deadlines.pop(local_id, None)
                    if self._grouped_mode() and reason == 1:
                        self._mark_attention_expired(local_id)
                        # Reason 1 reports that this desktop notification is
                        # closed; keep replacement correlation but no longer
                        # allow CloseNotification against its ID.
                        self._action_daemon_id.pop(local_id, None)
                        self._open_info.pop(local_id, None)
                        self.actions_changed.set()
                        await self._on_expire(local_id)
                    else:
                        self._forget_local(local_id)
                        await self._on_close(local_id)

    async def _handle_notify(
        self, message: Message, *, owner_generation: int | None = None
    ) -> None:
        if self.cfg.mode != "mirror":
            return
        if owner_generation is None:
            owner_generation = self._owner_change_generation
        if len(message.body) >= 8:
            app_name = str(message.body[0])
            if len(app_name) > MAX_NOTIFICATION_SOURCE_IDENTITY_CODEPOINTS:
                if self.cfg.ignore_apps:
                    # Do not pass a potentially secret oversized identity to
                    # logging or later display paths while privacy filters are
                    # configured.
                    log.debug("ignoring notification with oversized app identifier")
                    return
            elif is_ignored(app_name, self.cfg.ignore_apps):
                log.debug("ignoring notification from %r", message.body[0])
                return
        parsed = parse_notify_body(message.body)
        if parsed is None:
            return
        received_mono = time.monotonic()

        replaces = parsed.pop("replaces")
        prior_local = self._daemon_to_local.get(replaces) if replaces else None
        local_id = (
            prior_local
            if prior_local is not None
            and self._notification_owner_generations.get(prior_local) == owner_generation
            else None
        )
        if local_id is None:
            if self._allocate_local_id is None:
                local_id = self._next_id
                self._next_id += 1
            else:
                local_id = self._allocate_local_id()
            self._local_to_daemon[local_id] = None
            if replaces and prior_local is None:
                # Keep a provisional association until Notify returns. Some
                # notification daemons allocate a fresh ID for an unknown
                # replaces_id, and that returned ID is authoritative.
                self._daemon_to_local[replaces] = local_id
        self._mirrored_local_ids.add(local_id)
        # Match retained-card ordering: a genuine replacement is a new arrival.
        association = self._local_to_daemon.pop(local_id)
        self._local_to_daemon[local_id] = association
        self._locally_removed.pop(local_id, None)
        self._attention_expired.pop(local_id, None)
        self._forgotten_local_ids.pop(local_id, None)
        self._notification_owner_generations.pop(local_id, None)
        self._notification_owner_generations[local_id] = owner_generation
        while len(self._notification_owner_generations) > ASSOCIATION_LIMIT:
            self._notification_owner_generations.pop(next(iter(self._notification_owner_generations)))

        # Every Notify changes action identity, including identical replacements.
        version = self._next_open_version
        self._next_open_version += 1
        self._open_info.pop(local_id, None)
        self._open_info[local_id] = {
            "version": version, "expected": parse_open_metadata(message.body),
            "confirmed": False, "owner": None,
            "owner_generation": owner_generation,
            "reply_owner": None, "reply_generation": None,
            "identity_pending": self._identity_state != "ready" or owner_generation != self._owner_change_generation,
        }
        while len(self._open_info) > ASSOCIATION_LIMIT:
            self._open_info.pop(next(iter(self._open_info)))
        self.actions_changed.set()

        if message.serial and message.sender:
            self._by_serial[(message.sender, message.serial)] = (local_id, replaces)
            self._open_reply_versions[(message.sender, message.serial)] = version
            self._trim_pending_replies()

        # This deadline expires the legacy active projection. Grouped attention
        # has a separate coalesced presentation lease in the daemon.
        timeout_ms = effective_popup_timeout_ms(parsed["expire"], parsed["urgency"], self.cfg)
        if timeout_ms > 0:
            self._expiry_deadlines[local_id] = time.monotonic() + timeout_ms / 1000.0
        else:
            self._expiry_deadlines.pop(local_id, None)

        # Coalesce queued replacements and let NotificationClosed cancel a
        # card that has not reached the serial link yet.
        self._outbox.pop(local_id, None)
        self._outbox[local_id] = proto.notify(
            local_id,
            parsed["app"],
            parsed["summary"],
            parsed["body"],
            parsed["urgency"],
            parsed["expire"],
            int(time.time()),
            body_runs=parsed.get("body_runs"),
        )
        self._outbox[local_id]["_received_mono"] = received_mono
        while len(self._outbox) > NOTIFY_OUTBOX_LIMIT:
            self._outbox.pop(next(iter(self._outbox)))
        for trimmed_id in self._trim_associations():
            await self._on_active_expire(trimmed_id)
        self._outbox_ready.set()

    async def _send_loop(self) -> None:
        interval = 1.0 / NOTIFY_RATE_PER_S
        while True:
            await self._outbox_ready.wait()
            self._outbox_ready.clear()
            while self._outbox:
                local_id = next(iter(self._outbox))
                message = self._outbox.pop(local_id)
                await self._on_notify(message)
                await asyncio.sleep(interval)
