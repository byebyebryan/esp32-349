#!/usr/bin/env python3
"""Replay Open through the real source, daemon, firmware parser and LVGL.

The notification owner and action provider are controlled substitutes. This
checks integration and pointer handling; desktop invocation is a separate gate.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from unittest.mock import patch

from dbus_next import Message, MessageType

from test_grouped_composed import Native, legacy_grouped_hello
from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.sources.notifications import NOTIFICATIONS_NAME


class Provider:
    """The agreed provider envelope, with a controllable dispatch boundary."""

    def __init__(self):
        self.epoch = "composed-epoch"
        self.pid = 40
        self.session = self.boot_id = None
        self.bindings = {}
        self.invocations = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def call(self, method, payload=None):
        if method == "status":
            return {"v": 1, "epoch": self.epoch, "pid": self.pid,
                    "session": self.session, "boot_id": self.boot_id,
                    "bindings": list(self.bindings.values())}
        p = dict(payload)
        if method == "bind":
            self.session, self.boot_id = p["session"], p["boot_id"]
            token = f"token-{p['id']}-{p['rev']}-{p['source_version']}"
            self.bindings[p["id"]] = {"id": p["id"], "rev": p["rev"],
                                        "token": token, "live": True}
            return {"v": 1, "epoch": self.epoch, "pid": self.pid,
                    "session": self.session, "boot_id": self.boot_id,
                    "id": p["id"], "rev": p["rev"], "token": token,
                    "status": "ready"}
        if method == "release":
            self.bindings.pop(p["id"], None)
            return {"v": 1, "epoch": self.epoch, "status": "released"}
        assert method == "activate", method
        assert p["session"] == self.session and p["boot_id"] == self.boot_id
        binding = self.bindings.get(p["id"])
        assert binding and binding["token"] == p["token"] and binding["rev"] == p["rev"]
        self.invocations.append(p)
        await self.gate.wait()
        return {"v": 1, "epoch": self.epoch, "pid": self.pid,
                **{key: p[key] for key in ("session", "boot_id", "id", "rev", "request")},
                "status": "dispatched"}


async def scenario(native: Native):
    now = [1000.0]
    native.clock_hook = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    cfg = default_config()
    cfg.notifications.device_open = "dms"
    daemon = Daemon(cfg, asyncio.Event())
    provider = Provider()
    daemon.action_manager.provider = provider
    daemon._sample = lambda: {"cpu": .18, "mem": .43, "network": True}
    daemon.clock.read = lambda: (1790411700, -7 * 3600)
    daemon._writer = object()
    source = daemon.notifications
    source._server_owner, source._server_pid = ":1.40", provider.pid

    async def write(message):
        native.wire(message)
        return True
    daemon._write_message = write

    async def route():
        messages, native.outbound = native.outbound, []
        inputs = [message for message in messages if message.get("t") == "input"]
        for message in inputs:
            await daemon._handle_input(message)
        # Run asynchronous activation without blocking native pointer replay.
        for _ in range(8):
            await asyncio.sleep(0)
        return inputs

    async def advance(ms):
        native.command("advance", ms=ms)
        async with daemon._state_lock:
            await daemon._expire_presentation_locked(now[0])
            if daemon._pending_present_due is not None and daemon._pending_present_due <= now[0]:
                await daemon._publish_pending_presentation_locked(now[0])
        await route()

    async def tap(x=528, y=44):
        native.command("press", x=x, y=y, ms=1)
        native.command("release", x=x, y=y, ms=1)
        return await route()

    async def notify(serial, *, replaces=0, actions=None, summary="We've / we’ve 東京 → ✓"):
        await source._handle(Message(
            destination=NOTIFICATIONS_NAME, path="/org/freedesktop/Notifications",
            interface=NOTIFICATIONS_NAME, member="Notify", signature="susssasa{sv}i",
            body=["COMPOSED", replaces, "", summary, "Body tap is inert. 東京 が → ✓",
                  ["default", "Open"] if actions is None else actions, {}, 10000],
            sender=":1.50", serial=serial,
        ))
        await source._handle(Message(
            message_type=MessageType.METHOD_RETURN, destination=":1.50", sender=":1.40",
            reply_serial=serial, signature="u", body=[9],
        ))
        for nid, message in list(source._outbox.items()):
            source._outbox.pop(nid)
            await daemon._device_notify(message)
        await daemon.action_manager.reconcile_once()
        await advance(301)
        await advance(200)  # Settle attention published at the coalescing deadline.
        return source._daemon_to_local[9]

    with patch("status349.daemon.time.monotonic", lambda: now[0]):
        native.wire({"t": "hello"})
        hello = next(m for m in native.outbound if m.get("t") == "hello")
        assert "notification-actions-v1" in hello["cap"], hello
        assert proto.notification_history_capable(hello), hello
        legacy_hello = legacy_grouped_hello(hello)
        native.outbound.clear()
        await daemon._on_line(proto.encode(legacy_hello).decode().rstrip())
        await advance(100)
        nid = await notify(1)
        status = native.status()
        assert status["actions"]["enabled"] and status["actions"]["open"] == [
            {"id": nid, "rev": status["actions"]["open"][0]["rev"], "state": "ready"}], status
        native.command("capture", name="actions-composed-ready")

        # Actual LVGL body tap and button-owned drag cannot activate or dismiss.
        await tap(350, 100)
        native.command("press", x=528, y=44, ms=1)
        native.command("move", x=600, y=44, ms=100)
        native.command("release", x=600, y=44, ms=1)
        await route()
        assert not provider.invocations and nid in native.status()["ids"]

        # Pending is global. A rapid repeated tap dispatches exactly once.
        provider.gate.clear()
        inputs = await tap()
        request = next(m for m in inputs if m.get("action") == "activate")
        assert len(provider.invocations) == 1 and daemon._manual_notifications
        assert native.status()["actions"]["pending"]["request"] == request["request"]
        native.command("capture", name="actions-composed-pending")
        await tap()
        assert len(provider.invocations) == 1
        provider.gate.set()
        await route()
        assert native.status()["actions"]["pending"] is None
        await daemon._handle_input(request)
        await route()
        assert len(provider.invocations) == 1, "duplicate activation invoked twice"
        await advance(600)

        # Source replacement invalidates an already captured Open press.
        old_rev = native.status()["actions"]["open"][0]["rev"]
        native.command("press", x=528, y=44, ms=1)
        await notify(2, replaces=9)
        native.command("release", x=528, y=44, ms=1)
        await route()
        assert len(provider.invocations) == 1
        assert native.status()["actions"]["open"][0]["rev"] > old_rev

        # Attention expiry retains the live action, while desktop closure disables it.
        await advance(11000)
        assert nid in native.status()["ids"]
        assert native.status()["actions"]["open"][0]["state"] == "ready"
        await source._handle(Message(
            message_type=MessageType.SIGNAL, sender=":1.40",
            path="/org/freedesktop/Notifications", interface=NOTIFICATIONS_NAME,
            member="NotificationClosed", signature="uu", body=[9, 1],
        ))
        await daemon.action_manager.reconcile_once()
        assert nid in native.status()["ids"]
        assert native.status()["actions"]["open"][0]["state"] == "unavailable"
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=230, y=100, ms=100)
        native.command("release", x=230, y=100, ms=1)
        await advance(200)
        native.command("capture", name="actions-composed-history")
        await tap()
        assert len(provider.invocations) == 1
        await tap(600, 44)
        assert nid not in daemon.model.retained_notifs
        assert native.status()["grouped"]["group"] == "home"

        # A real close can precede its action response. Keep global busy until
        # the exact old request settles, without labeling the surviving card.
        closing_id = await notify(3)
        provider.gate.clear()
        pending_inputs = await tap()
        closing_request = next(m for m in pending_inputs if m.get("action") == "activate")
        assert len(provider.invocations) == 2
        await source._handle(Message(
            message_type=MessageType.SIGNAL, sender=":1.40",
            path="/org/freedesktop/Notifications", interface=NOTIFICATIONS_NAME,
            member="NotificationClosed", signature="uu", body=[9, 2],
        ))
        assert closing_id not in native.status()["ids"]
        survivor_id = await notify(4, summary="SURVIVOR 東京 → ✓")
        assert native.status()["actions"]["pending"]["id"] == closing_id
        # A result with another card's ID must not release this request.
        native.wire(proto.action_result(closing_request["session"], closing_request["boot_id"],
            survivor_id, closing_request["open_rev"], closing_request["request"], "dispatched"))
        assert native.status()["actions"]["pending"] is not None
        provider.gate.set()
        await route()
        assert native.status()["actions"]["pending"] is None
        assert native.status()["ids"] == [survivor_id]
        assert closing_id not in daemon.model.retained_notifs
        native.command("capture", name="actions-composed-close-before-result")

    print("source -> host actions -> production parser/state -> LVGL -> activation: passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    native = Native(args.native.resolve(), args.artifacts.resolve())
    try:
        asyncio.run(scenario(native))
    finally:
        native.close()
