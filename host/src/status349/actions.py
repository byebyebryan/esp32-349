"""Bounded asynchronous client and response validation for the DMS action bridge."""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable

BRIDGE_TARGET = "349-notification-actions"
BRIDGE_TIMEOUT_S = 2.0
BRIDGE_MESSAGE_MAX = 8192
BRIDGE_BINDING_LIMIT = 32
_EPOCH_MAX = 128
_TOKEN_MAX = 512


class BridgeError(RuntimeError):
    """A bounded bridge call failed without exposing action payloads in text."""

    def __init__(self, kind: str, *, uncertain: bool = False):
        super().__init__(kind)
        self.kind = kind
        self.uncertain = uncertain


def _bounded_text(value: object, maximum: int, *, nonempty: bool = True) -> bool:
    if not isinstance(value, str) or (nonempty and not value):
        return False
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeError:
        return False


def _integer(value: object, maximum: int, *, nonzero: bool = True) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and (value > 0 if nonzero else value >= 0) and value <= maximum
    )


def _version_one(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def validate_status(reply: object) -> dict | None:
    if not isinstance(reply, dict):
        return None
    if not _version_one(reply.get("v")):
        return None
    if not _bounded_text(reply.get("epoch"), _EPOCH_MAX):
        return None
    if not _integer(reply.get("pid"), 0x7FFFFFFF):
        return None
    session = reply.get("session")
    boot_id = reply.get("boot_id")
    if session is not None and not _integer(session, 0x7FFFFFFF):
        return None
    if boot_id is not None and not _integer(boot_id, 0xFFFFFFFF):
        return None
    if (session is None) != (boot_id is None):
        return None
    bindings = reply.get("bindings")
    if not isinstance(bindings, list) or len(bindings) > BRIDGE_BINDING_LIMIT:
        return None
    if session is None and bindings:
        return None
    parsed: dict[int, dict] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            return None
        nid, revision, token, live = (
            binding.get("id"), binding.get("rev"), binding.get("token"), binding.get("live")
        )
        if (
            not _integer(nid, 0x7FFFFFFF) or not _integer(revision, 0x7FFFFFFF)
            or not _bounded_text(token, _TOKEN_MAX) or not isinstance(live, bool)
            or nid in parsed
        ):
            return None
        parsed[nid] = {"id": nid, "rev": revision, "token": token, "live": live}
    return {"v": 1, "epoch": reply["epoch"], "pid": reply["pid"], "session": session,
            "boot_id": boot_id, "bindings": parsed}


def validate_bind_reply(reply: object, expected: dict) -> dict | None:
    if not isinstance(reply, dict) or not _version_one(reply.get("v")):
        return None
    limits = {"session": 0x7FFFFFFF, "boot_id": 0xFFFFFFFF, "id": 0x7FFFFFFF, "rev": 0x7FFFFFFF}
    if (
        not _bounded_text(reply.get("epoch"), _EPOCH_MAX)
        or not _integer(reply.get("pid"), 0x7FFFFFFF)
        or any(not _integer(reply.get(field), limits[field]) for field in limits)
        or any(reply[field] != expected[field] for field in limits)
        or not isinstance(reply.get("status"), str)
        or reply.get("status") not in {"ready", "unavailable", "stale"}
    ):
        return None
    token = reply.get("token")
    if reply["status"] == "ready":
        if not _bounded_text(token, _TOKEN_MAX):
            return None
    elif token is not None and not _bounded_text(token, _TOKEN_MAX):
        return None
    return dict(reply)


def validate_activate_reply(reply: object, expected: dict) -> dict | None:
    statuses = {"dispatched", "unavailable", "stale", "failed", "unknown"}
    if not isinstance(reply, dict) or not _version_one(reply.get("v")):
        return None
    limits = {
        "pid": 0x7FFFFFFF, "session": 0x7FFFFFFF, "boot_id": 0xFFFFFFFF, "id": 0x7FFFFFFF,
        "rev": 0x7FFFFFFF, "request": 0x7FFFFFFF,
    }
    if (
        not _bounded_text(reply.get("epoch"), _EPOCH_MAX)
        or any(not _integer(reply.get(field), limit) for field, limit in limits.items())
        or any(reply[field] != expected[field] for field in limits)
        or not isinstance(reply.get("status"), str)
        or reply.get("status") not in statuses
    ):
        return None
    if reply.get("epoch") != expected["epoch"]:
        return None
    return dict(reply)


class BridgeClient:
    """Invoke the dedicated DMS IPC target without a shell or unbounded pipes."""

    def __init__(self, executable: str = "dms", *, timeout_s: float = BRIDGE_TIMEOUT_S):
        self.executable = executable
        self.timeout_s = timeout_s

    async def _read_bounded(self, stream: asyncio.StreamReader) -> bytes:
        data = bytearray()
        while True:
            chunk = await stream.read(min(4096, BRIDGE_MESSAGE_MAX + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > BRIDGE_MESSAGE_MAX:
                raise BridgeError("oversized-output", uncertain=True)

    async def call(self, method: str, payload: dict | None = None) -> dict:
        if method not in {"status", "bind", "activate", "release"}:
            raise ValueError("unsupported bridge method")
        args = [self.executable, "ipc", "call", BRIDGE_TARGET, method]
        if method == "status":
            if payload is not None:
                raise ValueError("status does not take a payload")
        else:
            if not isinstance(payload, dict):
                raise ValueError("bridge method requires an object payload")
            try:
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (TypeError, UnicodeError) as exc:
                raise ValueError("bridge payload is not bounded JSON") from exc
            if len(encoded) > BRIDGE_MESSAGE_MAX:
                raise ValueError("bridge payload exceeds 8192 bytes")
            args.append(encoded.decode("utf-8"))

        try:
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except (OSError, ValueError) as exc:
            raise BridgeError("unavailable") from exc

        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
        wait_task = asyncio.create_task(process.wait())
        try:
            try:
                stdout, _stderr, returncode = await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, wait_task), timeout=self.timeout_s
                )
            except asyncio.TimeoutError as exc:
                raise BridgeError("timeout", uncertain=method == "activate") from exc
            except BridgeError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Stream, process, or decoding failures carry no action details.
                raise BridgeError("process-error", uncertain=method == "activate") from exc
            if returncode != 0:
                raise BridgeError("unavailable", uncertain=method == "activate")
            try:
                result = json.loads(stdout.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise BridgeError("malformed-output", uncertain=True) from exc
            if not isinstance(result, dict):
                raise BridgeError("malformed-output", uncertain=True)
            return result
        except BaseException:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await process.wait()
            except ProcessLookupError:
                pass
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise


@dataclass
class ActionEntry:
    revision: int = 1
    state: str = "unavailable"
    candidate: dict | None = None
    fingerprint: tuple | None = None
    epoch: str | None = None
    provider_pid: int | None = None
    token: str | None = None
    binding_revision: int | None = None
    pending_bind_revision: int | None = None
    blocked_epoch: str | None = None
    blocked_pid: int | None = None
    scope: tuple[int, int] | None = None
    generation: int = 0
    bind_pending: bool = False
    exhausted: bool = False


def action_fingerprint(candidate: dict | None) -> tuple | None:
    if candidate is None:
        return None
    expected = candidate.get("expected")
    if not isinstance(expected, dict):
        return None
    return (
        candidate.get("version"), candidate.get("desktop_id"), candidate.get("owner"),
        candidate.get("pid"), *(expected.get(key) for key in ("app", "summary", "body", "default_label")),
    )


def valid_action_candidate(candidate: object, local_id: int) -> bool:
    if not isinstance(candidate, dict):
        return False
    version, desktop_id, pid = candidate.get("version"), candidate.get("desktop_id"), candidate.get("pid")
    if (
        candidate.get("id") != local_id or isinstance(candidate.get("id"), bool)
        or not _integer(local_id, 0x7FFFFFFF) or not _integer(version, 0x7FFFFFFF)
        or not _integer(desktop_id, 0xFFFFFFFF) or not _integer(pid, 0x7FFFFFFF)
        or not _bounded_text(candidate.get("owner"), 255)
    ):
        return False
    expected = candidate.get("expected")
    fields = ("app", "summary", "body", "default_label")
    if not isinstance(expected, dict) or set(expected) != set(fields):
        return False
    if any(not _bounded_text(expected.get(field), BRIDGE_MESSAGE_MAX, nonempty=False) for field in fields):
        return False
    try:
        return len(json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= BRIDGE_MESSAGE_MAX - 1024
    except (TypeError, UnicodeError):
        return False


class NotificationActionManager:
    """Reconcile at most 32 retained source associations with one bridge epoch.

    `reconcile_once()` is deliberately public and deterministic for host-side
    fake-provider acceptance. `run()` adds event-driven invalidation and a
    one-second maximum status polling rate.
    """

    def __init__(
        self,
        source: object,
        provider: object,
        *,
        enabled: Callable[[], bool],
        context: Callable[[], tuple[int, int | None]],
        retained_ids: Callable[[], list[int] | tuple[int, ...]],
        on_change: Callable[[int, dict], Awaitable[None]],
    ) -> None:
        self.source = source
        self.provider = provider
        self.enabled = enabled
        self.context = context
        self.retained_ids = retained_ids
        self.on_change = on_change
        self.entries: OrderedDict[int, ActionEntry] = OrderedDict()
        self._reconcile_lock = asyncio.Lock()
        self._provider_epoch: str | None = None
        self._provider_pid: int | None = None
        self._last_status_mono: float | None = None
        self._release_queue: OrderedDict[tuple, tuple[int, ActionEntry, bool]] = OrderedDict()

    def open_for(self, local_id: int) -> dict:
        """Return stable per-card wire state, creating unavailable state first."""
        try:
            retained = tuple(int(value) for value in self.retained_ids())[-BRIDGE_BINDING_LIMIT:]
            self._touch_ids(retained)
        except Exception:
            retained = tuple(self.entries)
        if local_id not in retained:
            return {"rev": 1, "state": "unavailable"}
        entry = self.entries.get(local_id)
        if entry is None:
            if len(self.entries) >= BRIDGE_BINDING_LIMIT:
                return {"rev": 1, "state": "unavailable"}
            entry = ActionEntry()
            self.entries[local_id] = entry
        candidate = self._current_source_candidate(local_id)
        if not valid_action_candidate(candidate, local_id):
            candidate = None
        self._update_candidate(entry, local_id, candidate)
        return {"rev": entry.revision, "state": entry.state}

    def _update_candidate(self, entry: ActionEntry, local_id: int, candidate: dict | None) -> tuple[int, str]:
        before = (entry.revision, entry.state)
        fingerprint = action_fingerprint(candidate)
        if fingerprint == entry.fingerprint:
            return before
        old = ActionEntry(**vars(entry))
        if entry.fingerprint is not None or fingerprint is not None:
            bumped = self._bump(entry)
        else:
            bumped = True
        if old.token is not None:
            self._queue_release(local_id, old, final=False)
        entry.candidate = dict(candidate) if candidate is not None else None
        entry.fingerprint = fingerprint
        if bumped:
            entry.state = "unavailable"
        entry.blocked_epoch = None
        entry.blocked_pid = None
        entry.pending_bind_revision = None
        entry.generation += 1
        entry.bind_pending = False
        return before

    def invalidate_for_link_reset(self) -> None:
        """Bump ready metadata so a same-boot reconnect clears blocked Open UI."""
        for local_id, entry in self.entries.items():
            if entry.state != "ready" and entry.pending_bind_revision is None:
                continue
            old = ActionEntry(**vars(entry))
            if self._bump(entry):
                entry.state = "unavailable"
            entry.pending_bind_revision = None
            entry.generation += 1
            entry.blocked_epoch = None
            entry.blocked_pid = None
            if old.token is not None and old.binding_revision == old.revision:
                self._queue_release(local_id, old, final=False)
        event = getattr(self.source, "actions_changed", None)
        if event is not None:
            event.set()

    async def refresh_after_terminal(self, local_id: int, revision: int) -> None:
        """Require a newly proven binding after any non-dispatched outcome."""
        entry = self.entries.get(local_id)
        if entry is None or entry.revision != revision or entry.state != "ready":
            return
        before = (entry.revision, entry.state)
        old = ActionEntry(**vars(entry))
        if not self._bump(entry):
            return
        entry.state = "unavailable"
        entry.pending_bind_revision = None
        entry.generation += 1
        entry.blocked_epoch = None
        entry.blocked_pid = None
        if old.token is not None and old.binding_revision == old.revision:
            self._queue_release(local_id, old, final=False)
        await self._publish(local_id, entry, before)
        event = getattr(self.source, "actions_changed", None)
        if event is not None:
            event.set()

    async def _call(self, method: str, payload: dict | None = None) -> dict:
        result = await self.provider.call(method, payload)
        if not isinstance(result, dict):
            raise BridgeError("malformed-output", uncertain=method == "activate")
        return result

    async def _publish(self, local_id: int, entry: ActionEntry, before: tuple[int, str]) -> None:
        current = (entry.revision, entry.state)
        if current != before:
            await self.on_change(local_id, {"rev": entry.revision, "state": entry.state})

    @staticmethod
    def _bump(entry: ActionEntry) -> bool:
        if entry.revision >= 0x7FFFFFFF:
            entry.exhausted = True
            return False
        entry.revision += 1
        return True

    def _current_source_candidate(self, local_id: int) -> dict | None:
        try:
            return self.source.action_candidate(local_id)
        except Exception:
            return None

    async def _release(self, local_id: int, entry: ActionEntry, *, final: bool) -> None:
        if entry.token is None or entry.scope is None or entry.binding_revision is None:
            return
        session, boot_id = entry.scope
        payload = {
            "v": 1, "session": session, "boot_id": boot_id, "id": local_id,
            "rev": entry.binding_revision, "token": entry.token, "final": final,
        }
        try:
            await self._call("release", payload)
        except Exception:
            pass

    def _queue_release(self, local_id: int, entry: ActionEntry, *, final: bool) -> None:
        if entry.token is None or entry.scope is None or entry.binding_revision is None:
            return
        queued = ActionEntry(**vars(entry))
        key = (local_id, entry.scope, entry.binding_revision, entry.token)
        previous = self._release_queue.get(key)
        if previous is not None:
            final = final or previous[2]
        elif len(self._release_queue) >= BRIDGE_BINDING_LIMIT * 2:
            # Status reconciliation rediscovers any still-live token, so
            # bounded queue pressure can defer revocation without losing it.
            return
        self._release_queue[key] = (local_id, queued, final)

    async def _drain_releases(self) -> None:
        while self._release_queue:
            _key, (local_id, entry, final) = self._release_queue.popitem(last=False)
            await self._release(local_id, entry, final=final)

    def _touch_ids(self, retained: tuple[int, ...]) -> None:
        keep = set(retained)
        for local_id in tuple(self.entries):
            if local_id not in keep:
                entry = self.entries.pop(local_id)
                self._queue_release(local_id, entry, final=True)
        for local_id in retained:
            self.entries.setdefault(local_id, ActionEntry())
        while len(self.entries) > BRIDGE_BINDING_LIMIT:
            local_id, entry = self.entries.popitem(last=False)
            self._queue_release(local_id, entry, final=True)

    async def _refresh_candidates(self) -> tuple[tuple[int, ...], list[int]]:
        try:
            retained = tuple(int(value) for value in self.retained_ids())[-BRIDGE_BINDING_LIMIT:]
        except Exception:
            retained = tuple(self.entries)
        self._touch_ids(retained)
        try:
            candidates = self.source.action_candidates()
        except Exception:
            candidates = {}
        if not isinstance(candidates, dict):
            candidates = {}

        changed_ids: list[int] = []
        for local_id in retained:
            entry = self.entries[local_id]
            candidate = candidates.get(local_id)
            if not valid_action_candidate(candidate, local_id):
                candidate = None
            before = self._update_candidate(entry, local_id, candidate)
            if before == (entry.revision, entry.state):
                continue
            await self._publish(local_id, entry, before)
            changed_ids.append(local_id)
        return retained, changed_ids

    async def _provider_unavailable(self) -> None:
        for local_id, entry in tuple(self.entries.items()):
            before = (entry.revision, entry.state)
            entry.blocked_epoch = None
            entry.blocked_pid = None
            if entry.state == "ready" or (entry.token is not None and entry.binding_revision == entry.revision):
                old = ActionEntry(**vars(entry))
                if self._bump(entry):
                    entry.state = "unavailable"
                entry.pending_bind_revision = None
                self._queue_release(local_id, old, final=False)
            await self._publish(local_id, entry, before)
        self._provider_epoch = None
        self._provider_pid = None

    async def _invalidate_binding(self, local_id: int, entry: ActionEntry, *, epoch: str | None, pid: int | None,
                                  scope: tuple[int, int] | None, blocked: bool) -> None:
        before = (entry.revision, entry.state)
        old = ActionEntry(**vars(entry))
        can_publish = entry.state != "ready" or self._bump(entry)
        if can_publish:
            entry.state = "unavailable"
        entry.pending_bind_revision = None
        entry.blocked_epoch = epoch if blocked else None
        entry.blocked_pid = pid if blocked else None
        if old.token is not None and old.binding_revision == old.revision:
            self._queue_release(local_id, old, final=False)
        if can_publish:
            await self._publish(local_id, entry, before)

    async def _accept_bind(self, local_id: int, entry: ActionEntry, candidate: dict,
                           status: dict, session: int, boot_id: int) -> None:
        if entry.exhausted or entry.candidate is None or entry.fingerprint != action_fingerprint(candidate):
            return
        current = self._current_source_candidate(local_id)
        if action_fingerprint(current) != entry.fingerprint or local_id not in self.entries:
            return
        bind_generation = entry.generation
        if entry.pending_bind_revision is None:
            if entry.revision >= 0x7FFFFFFF:
                entry.exhausted = True
                return
            entry.pending_bind_revision = entry.revision + 1
        expected = {
            "session": session, "boot_id": boot_id, "id": local_id,
            "rev": entry.pending_bind_revision, "source_version": candidate["version"],
            "desktop_id": candidate["desktop_id"], "expected": candidate["expected"],
        }
        payload = {"v": 1, **expected}

        def still_current() -> bool:
            current = self._current_source_candidate(local_id)
            context_now = self.context()
            return (
                action_fingerprint(current) == entry.fingerprint
                and self.entries.get(local_id) is entry
                and entry.pending_bind_revision == expected["rev"]
                and entry.generation == bind_generation
                and self.enabled()
                and context_now == (session, boot_id)
            )

        try:
            reply = await self._call("bind", payload)
        except (BridgeError, OSError, asyncio.TimeoutError):
            if still_current():
                entry.blocked_epoch = status["epoch"]
                entry.blocked_pid = status["pid"]
            return

        parsed = validate_bind_reply(reply, expected)
        if parsed is None or parsed["epoch"] != status["epoch"] or parsed["pid"] != candidate["pid"]:
            if still_current():
                entry.blocked_epoch = status["epoch"]
                entry.blocked_pid = status["pid"]
            return

        if not still_current():
            if parsed["status"] == "ready" and parsed.get("token"):
                stale = ActionEntry(
                    revision=expected["rev"], binding_revision=expected["rev"], state="ready",
                    epoch=parsed["epoch"], provider_pid=parsed["pid"], token=parsed["token"],
                    scope=(session, boot_id),
                )
                still_retained = local_id in self.retained_ids()
                self._queue_release(local_id, stale, final=not still_retained)
            return

        entry.epoch = parsed["epoch"]
        entry.provider_pid = parsed["pid"]
        entry.scope = (session, boot_id)
        if parsed["status"] == "ready":
            before = (entry.revision, entry.state)
            entry.revision = expected["rev"]
            entry.binding_revision = expected["rev"]
            entry.token = parsed["token"]
            entry.pending_bind_revision = None
            entry.blocked_epoch = None
            entry.blocked_pid = None
            entry.state = "ready"
            await self._publish(local_id, entry, before)
        else:
            entry.blocked_epoch = parsed["epoch"]
            entry.blocked_pid = parsed["pid"]

    async def _provider_refresh(self, retained: tuple[int, ...]) -> None:
        try:
            raw_status = await self._call("status")
        except (BridgeError, OSError, asyncio.TimeoutError):
            await self._provider_unavailable()
            return
        status = validate_status(raw_status)
        if status is None:
            await self._provider_unavailable()
            return

        session, boot_id = self.context()
        if (
            not self.enabled() or isinstance(session, bool) or not isinstance(session, int)
            or not 1 <= session <= 0x7FFFFFFF or not _integer(boot_id, 0xFFFFFFFF)
        ):
            await self._provider_unavailable()
            return
        scope = (session, boot_id)

        epoch_changed = self._provider_epoch is not None and self._provider_epoch != status["epoch"]
        pid_changed = self._provider_pid is not None and self._provider_pid != status["pid"]
        status_scope = (status["session"], status["boot_id"])
        scope_mismatch = status_scope != scope

        # The provider can outlive a host-side record that was evicted while a
        # bind response was uncertain. Retire only IDs no longer retained.
        if status["session"] is not None and status["boot_id"] is not None:
            retained_set = set(retained)
            for local_id, binding in status["bindings"].items():
                if local_id not in retained_set:
                    orphan = ActionEntry(
                        revision=binding["rev"], binding_revision=binding["rev"],
                        epoch=status["epoch"], provider_pid=status["pid"], token=binding["token"],
                        scope=(status["session"], status["boot_id"]),
                    )
                    self._queue_release(local_id, orphan, final=True)

        retained_set = set(retained)
        for local_id, binding in status["bindings"].items():
            if local_id not in retained_set or status["session"] is None or status["boot_id"] is None:
                continue
            entry = self.entries.get(local_id)
            scope_is_current = status_scope == scope
            adopted_pending = (
                entry is not None and entry.pending_bind_revision is not None
                and entry.candidate is not None and entry.candidate.get("pid") == status["pid"]
                and binding["live"] and binding["rev"] == entry.pending_bind_revision
                and scope_is_current
            )
            active_binding = (
                entry is not None and entry.state == "ready" and entry.token is not None
                and binding["live"] and binding["rev"] == entry.binding_revision
                and binding["token"] == entry.token and entry.epoch == status["epoch"]
                and entry.provider_pid == status["pid"] and entry.scope == scope
                and entry.binding_revision == entry.revision and scope_is_current
            )
            if not adopted_pending and not active_binding:
                stale_binding = ActionEntry(
                    revision=binding["rev"], binding_revision=binding["rev"],
                    epoch=status["epoch"], provider_pid=status["pid"], token=binding["token"],
                    scope=(status["session"], status["boot_id"]),
                )
                self._queue_release(local_id, stale_binding, final=False)

        for local_id, entry in tuple(self.entries.items()):
            if entry.state != "ready":
                continue
            binding = status["bindings"].get(local_id)
            matching = (
                binding is not None and binding["live"] and entry.token is not None
                and binding["rev"] == entry.binding_revision and binding["token"] == entry.token
                and entry.epoch == status["epoch"] and entry.provider_pid == status["pid"]
                and entry.scope == scope and not scope_mismatch
                and entry.binding_revision == entry.revision
            )
            if epoch_changed or pid_changed or scope_mismatch or not matching:
                blocked = (
                    not epoch_changed and not pid_changed and not scope_mismatch
                    and (binding is None or not binding["live"] or not matching)
                )
                await self._invalidate_binding(
                    local_id, entry, epoch=status["epoch"], pid=status["pid"], scope=scope,
                    blocked=blocked,
                )

        await self._drain_releases()

        self._provider_epoch = status["epoch"]
        self._provider_pid = status["pid"]

        # A previous bind may have completed remotely while its stdout timed
        # out. Adopt only the exact pending revision in the active scope.
        for local_id in retained:
            entry = self.entries.get(local_id)
            candidate = entry.candidate if entry is not None else None
            binding = status["bindings"].get(local_id)
            if (
                entry is None or candidate is None or entry.pending_bind_revision is None
                or binding is None or not binding["live"]
                or binding["rev"] != entry.pending_bind_revision
                or candidate.get("pid") != status["pid"] or scope_mismatch
            ):
                continue
            current = self._current_source_candidate(local_id)
            if action_fingerprint(current) != entry.fingerprint:
                continue
            before = (entry.revision, entry.state)
            entry.revision = entry.pending_bind_revision
            entry.binding_revision = binding["rev"]
            entry.epoch = status["epoch"]
            entry.provider_pid = status["pid"]
            entry.scope = scope
            entry.token = binding["token"]
            entry.pending_bind_revision = None
            entry.state = "ready"
            entry.blocked_epoch = None
            entry.blocked_pid = None
            await self._publish(local_id, entry, before)

        candidates = [
            (entry.candidate["version"], local_id, entry, entry.candidate)
            for local_id in retained
            if (entry := self.entries.get(local_id)) is not None
            and entry.candidate is not None and entry.state != "ready" and not entry.exhausted
            and (entry.blocked_epoch, entry.blocked_pid) != (status["epoch"], status["pid"])
            and entry.candidate.get("pid") == status["pid"]
        ]
        # Stable order makes runs reproducible; provider lineage is per card.
        for _version, local_id, entry, candidate in sorted(candidates):
            if self.entries.get(local_id) is not entry or entry.state == "ready":
                continue
            await self._accept_bind(local_id, entry, candidate, status, session, boot_id)
        await self._drain_releases()

    async def reconcile_once(self) -> None:
        """Refresh source candidates and provider state once; intended for tests too."""
        async with self._reconcile_lock:
            retained, _changed = await self._refresh_candidates()
            await self._drain_releases()
            if not self.enabled():
                await self._provider_unavailable()
                await self._drain_releases()
                return
            await self._provider_refresh(retained)
            await self._drain_releases()
            self._last_status_mono = time.monotonic()

    def ready_binding(self, local_id: int, revision: int, session: int, boot_id: int) -> bool:
        entry = self.entries.get(local_id)
        if (
            entry is None or entry.exhausted or entry.state != "ready" or entry.revision != revision
            or entry.token is None or entry.epoch is None or entry.scope != (session, boot_id)
            or entry.binding_revision != entry.revision
            or entry.candidate is None or entry.candidate.get("pid") != entry.provider_pid
            or not self.enabled() or self.context() != (session, boot_id)
        ):
            return False
        return action_fingerprint(self._current_source_candidate(local_id)) == entry.fingerprint

    async def activate(self, local_id: int, revision: int, request: int, session: int, boot_id: int) -> str:
        """Dispatch only the current retained source binding; never retry."""
        if not _integer(request, 0x7FFFFFFF) or not self.ready_binding(local_id, revision, session, boot_id):
            return "unavailable"
        entry = self.entries.get(local_id)
        if entry is None or entry.token is None or entry.epoch is None:
            return "unavailable"
        candidate = entry.candidate
        token = entry.token
        epoch = entry.epoch
        expected = {
            "epoch": epoch, "session": session, "boot_id": boot_id,
            "id": local_id, "rev": revision, "request": request,
        }
        expected_reply = {**expected, "pid": entry.provider_pid}
        # Recheck after taking the immutable request snapshot and directly
        # before crossing the process boundary.
        if not self.ready_binding(local_id, revision, session, boot_id):
            return "stale"
        payload = {"v": 1, **expected, "token": token}
        try:
            reply = await self._call("activate", payload)
        except BridgeError as exc:
            return "unknown" if exc.uncertain else "unavailable"
        except (OSError, asyncio.TimeoutError):
            return "unknown"
        parsed = validate_activate_reply(reply, expected_reply)
        if parsed is None:
            return "unknown"
        # A close/replacement can race the response. The correlated terminal
        # result remains truthful; signal reconciliation, but never restore the
        # old card's ready state or label a newer revision with this result.
        if action_fingerprint(self._current_source_candidate(local_id)) != action_fingerprint(candidate):
            changed = getattr(self.source, "actions_changed", None)
            if changed is not None:
                changed.set()
        return parsed["status"]

    async def run(self, stop: asyncio.Event) -> None:
        event = getattr(self.source, "actions_changed", None)
        next_poll = 0.0
        while not stop.is_set():
            if event is not None and event.is_set():
                event.clear()
            async with self._reconcile_lock:
                retained, _changed = await self._refresh_candidates()
                await self._drain_releases()
                active = self.enabled()
                if not active:
                    await self._provider_unavailable()
                    await self._drain_releases()
                elif time.monotonic() >= next_poll:
                    await self._provider_refresh(retained)
                    await self._drain_releases()
                    next_poll = time.monotonic() + 1.0

            if stop.is_set():
                break
            if event is None:
                await asyncio.sleep(1.0 if self.enabled() else 0.5)
                continue
            timeout = max(0.0, next_poll - time.monotonic()) if self.enabled() else None
            try:
                if timeout is None:
                    await event.wait()
                else:
                    await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
