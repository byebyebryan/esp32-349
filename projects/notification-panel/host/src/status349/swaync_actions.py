"""Checked selected-card actions through the SwayNC fork's native D-Bus API."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import OrderedDict

from dbus_next import MessageType, Variant

from .actions import (
    BRIDGE_BINDING_LIMIT, BRIDGE_MESSAGE_MAX, BRIDGE_TIMEOUT_S,
    BridgeClient, BridgeError, action_fingerprint, validate_bind_reply, validate_status,
)
from .activation_token import session_activation_token


class SwayncBridgeClient:
    """Adapt native revision-checked actions to the existing host action manager.

    Bindings are local and bounded. The daemon checks its opaque Notify revision
    again at dispatch, so even an unseen identical replacement cannot inherit a
    bound card's action. No callback, URL, or command is recorded or replayed.
    """

    def __init__(self, source, *, token_provider=None):
        self.source = source
        self.token_provider = token_provider
        self._identity = None
        self._scope = None
        self._bindings = OrderedDict()
        self._results = OrderedDict()
        self._highwater = 0
        self._lock = asyncio.Lock()

    def _current_identity(self):
        identity = self.source.identity_status()
        if identity.get("state") != "ready" or not identity.get("owner") or not identity.get("pid"):
            raise BridgeError("unavailable")
        return identity["owner"], identity["pid"]

    def _reset(self, identity):
        self._identity = identity
        self._scope = None
        self._bindings.clear()
        self._results.clear()
        self._highwater = 0

    async def _rpc(self, member, signature, body, identity):
        reply = await self.source.action_provider_call(identity[0], member, signature, body)
        if self._current_identity() != identity:
            raise BridgeError("unavailable", uncertain=member.startswith("InvokeAction"))
        if reply is None or reply.message_type != MessageType.METHOD_RETURN or reply.sender != identity[0]:
            raise BridgeError("unavailable", uncertain=member.startswith("InvokeAction"))
        return reply

    async def _snapshot(self, desktop_id, identity):
        reply = await self._rpc("GetNotification", "u", [desktop_id], identity)
        if reply.signature != "a{sv}" or len(reply.body) != 1 or not isinstance(reply.body[0], dict):
            raise BridgeError("malformed-output")
        raw = reply.body[0]
        if not raw:
            return None
        signatures = {"id": "u", "revision": "s", "app": "s", "summary": "s", "body": "s", "actions": "as"}
        if set(raw) != set(signatures) or any(
            not isinstance(raw[key], Variant) or raw[key].signature != signature
            for key, signature in signatures.items()
        ):
            raise BridgeError("malformed-output")
        snapshot = {key: raw[key].value for key in signatures}
        if (
            snapshot["id"] != desktop_id or not snapshot["revision"]
            or len(snapshot["revision"]) > 128 or len(snapshot["actions"]) > 128
            or len(snapshot["actions"]) % 2
            or len(json.dumps(snapshot, ensure_ascii=False).encode()) > BRIDGE_MESSAGE_MAX
        ):
            raise BridgeError("malformed-output")
        return snapshot

    def _candidate(self, local_id):
        candidate = self.source.action_candidate(local_id)
        if candidate is None or (candidate["owner"], candidate["pid"]) != self._identity:
            return None
        return candidate

    def _live(self, binding):
        return action_fingerprint(self._candidate(binding["id"])) == binding["fingerprint"]

    def _envelope(self, payload):
        return {"v": 1, "epoch": self._identity[0], "pid": self._identity[1],
                **{key: payload[key] for key in ("session", "boot_id", "id", "rev")}}

    async def call(self, method, payload=None):
        if method not in {"status", "bind", "activate", "release"}:
            raise ValueError("unsupported bridge method")
        try:
            async with asyncio.timeout(BRIDGE_TIMEOUT_S):
                async with self._lock:
                    identity = self._current_identity()
                    if identity != self._identity:
                        # Probe capability on the exact notification owner, without
                        # activating a service or falling back to latest-popup IPC.
                        await self._snapshot(0, identity)
                        self._reset(identity)
                    if method == "status":
                        return {"v": 1, "epoch": identity[0], "pid": identity[1],
                                "session": self._scope[0] if self._scope else None,
                                "boot_id": self._scope[1] if self._scope else None,
                                "bindings": [{"id": b["id"], "rev": b["rev"], "token": b["token"],
                                              "live": self._live(b)} for b in self._bindings.values()]}
                    if not isinstance(payload, dict):
                        raise ValueError("bridge method requires an object payload")
                    if method == "bind":
                        return await self._bind(payload, identity)
                    if method == "release":
                        binding = self._bindings.get(payload["id"])
                        if self._matches(binding, payload):
                            self._bindings.pop(payload["id"])
                        return {"v": 1}
                    return await self._activate(payload, identity)
        except TimeoutError as exc:
            raise BridgeError("timeout", uncertain=method == "activate") from exc
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError("unavailable", uncertain=method == "activate") from exc

    async def _bind(self, payload, identity):
        scope = payload["session"], payload["boot_id"]
        if scope != self._scope:
            self._reset(identity)
            self._scope = scope
        envelope = self._envelope(payload)
        candidate = self._candidate(payload["id"])
        if candidate is None or any(
            candidate[key] != payload[field]
            for key, field in (("version", "source_version"), ("desktop_id", "desktop_id"), ("expected", "expected"))
        ):
            return {**envelope, "status": "stale"}
        fingerprint = action_fingerprint(candidate)
        previous = self._bindings.get(payload["id"])
        if previous and previous["rev"] >= payload["rev"]:
            if previous["rev"] == payload["rev"] and previous["fingerprint"] == fingerprint:
                return {**envelope, "status": "ready", "token": previous["token"]}
            return {**envelope, "status": "stale"}
        snapshot = await self._snapshot(candidate["desktop_id"], identity)
        if snapshot is None:
            return {**envelope, "status": "unavailable"}
        actions = snapshot["actions"]
        labels = [actions[i + 1] for i in range(0, len(actions), 2) if actions[i] == "default"]
        expected = {key: snapshot[key] for key in ("app", "summary", "body")}
        expected["default_label"] = labels[0] if len(labels) == 1 else None
        if expected != candidate["expected"] or action_fingerprint(self._candidate(payload["id"])) != fingerprint:
            return {**envelope, "status": "stale"}
        if previous is None and len(self._bindings) >= BRIDGE_BINDING_LIMIT:
            return {**envelope, "status": "unavailable"}
        binding = {"id": payload["id"], "rev": payload["rev"], "token": secrets.token_hex(24),
                   "fingerprint": fingerprint, "desktop_id": candidate["desktop_id"],
                   "revision": snapshot["revision"]}
        self._bindings[payload["id"]] = binding
        return {**envelope, "status": "ready", "token": binding["token"]}

    def _matches(self, binding, payload):
        return (binding is not None and self._scope == (payload["session"], payload["boot_id"])
                and binding["rev"] == payload["rev"] and binding["token"] == payload["token"])

    async def _activate(self, payload, identity):
        envelope = {**self._envelope(payload), "request": payload["request"]}
        request = payload["request"]
        key = tuple(payload.get(k) for k in ("epoch", "session", "boot_id", "id", "rev", "token"))
        if request <= self._highwater:
            recorded = self._results.get(request)
            return {**envelope, "status": recorded[1] if recorded and recorded[0] == key else "stale"}
        binding = self._bindings.get(payload["id"])
        if payload["epoch"] != identity[0] or not self._matches(binding, payload) or not self._live(binding):
            return {**envelope, "status": "stale"}
        # Reserve before crossing D-Bus; cancellation or lost replies remain
        # unknown and must never cause a repeated activation.
        self._highwater = request
        self._results[request] = (key, "unknown")
        while len(self._results) > 64:
            self._results.popitem(last=False)
        token = await self.token_provider() if self.token_provider is not None else None
        if self._current_identity() != identity or not self._live(binding):
            self._results[request] = (key, "stale")
            return {**envelope, "status": "stale"}
        arguments = [binding["desktop_id"], binding["revision"], "default"]
        if token is not None:
            reply = await self._rpc("InvokeActionWithToken", "usss", [*arguments, token], identity)
        else:
            reply = await self._rpc("InvokeAction", "uss", arguments, identity)
        if reply.signature != "s" or reply.body not in (["dispatched"], ["stale"], ["unavailable"]):
            raise BridgeError("malformed-output", uncertain=True)
        result = reply.body[0]
        self._results[request] = (key, result)
        return {**envelope, "status": result}


class ConfiguredBridgeClient:
    """Select one provider; route releases back to the provider that bound it."""

    def __init__(self, source, selection):
        self.selection = selection

        async def token_for_owner():
            identity = source.identity_status()
            if identity.get("state") != "ready" or not identity.get("pid"):
                raise BridgeError("unavailable")
            return await session_activation_token(notifier_pid=identity["pid"])

        self.providers = {"dms": BridgeClient(), "swaync": SwayncBridgeClient(source, token_provider=token_for_owner)}
        self._issued = OrderedDict()

    @staticmethod
    def _key(payload):
        return tuple(payload.get(k) for k in ("session", "boot_id", "id", "rev", "token"))

    async def call(self, method, payload=None):
        name = self.selection()
        if method == "release":
            name = self._issued.pop(self._key(payload), None)
            if name is None:
                return {"v": 1}
        if name not in self.providers:
            raise BridgeError("unavailable")
        reply = await self.providers[name].call(method, payload)
        if method == "bind" and validate_bind_reply(reply, payload) is not None and reply.get("status") == "ready":
            self._issued[self._key(reply)] = name
        elif method == "status":
            parsed = validate_status(reply)
            if parsed is not None:
                for binding in parsed["bindings"].values():
                    self._issued[self._key({**parsed, **binding})] = name
        while len(self._issued) > 128:
            self._issued.popitem(last=False)
        return reply
