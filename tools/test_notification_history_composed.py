#!/usr/bin/env python3
"""Exercise negotiated notification history through host, production C and LVGL."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from unittest.mock import patch

from dbus_next import Message, MessageType

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host" / "src"))
from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.sources.notifications import NOTIFICATIONS_NAME
from test_grouped_composed import Native
from test_notification_actions_composed import Provider


async def scenario(native: Native) -> None:
    now = [1000.0]
    native.clock_hook = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    cfg = default_config()
    cfg.notifications.device_open = "dms"
    assert cfg.notifications.retention_s == 1800
    daemon = Daemon(cfg, asyncio.Event())
    provider = Provider()
    daemon.action_manager.provider = provider
    daemon._sample = lambda: {"cpu": .18, "mem": .43, "network": True}
    daemon.clock.read = lambda: (1790411700, -7 * 3600)
    daemon._writer = object()
    source = daemon.notifications
    source._server_owner, source._server_pid = ":1.40", provider.pid
    serial = 1
    input_trace: list[dict] = []

    async def write(message: dict) -> bool:
        native.wire(message)
        return True
    daemon._write_message = write

    async def route_inputs() -> None:
        messages, native.outbound = native.outbound, []
        for message in messages:
            if message.get("t") == "input":
                input_trace.append(message)
                await daemon._handle_input(message)

    async def advance(ms: int) -> None:
        remaining = ms
        while remaining:
            elapsed = min(remaining, 600000)
            native.command("advance", ms=elapsed)
            async with daemon._state_lock:
                await daemon._expire_presentation_locked(now[0])
                if daemon._pending_present_due is not None and daemon._pending_present_due <= now[0]:
                    await daemon._publish_pending_presentation_locked(now[0])
            await route_inputs()
            remaining -= elapsed

    async def settle_arrival() -> None:
        # Let the host publish at the 300 ms coalescing deadline, then give
        # the native lifecycle its full 200 ms settle window.
        await advance(301)
        await advance(200)

    async def swipe(dx: int = 0, dy: int = 0) -> None:
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=350 + dx // 2, y=100 + dy // 2, ms=100)
        await route_inputs()
        native.command("move", x=350 + dx, y=100 + dy, ms=100)
        native.command("release", x=350 + dx, y=100 + dy, ms=1)
        await route_inputs()
        await advance(200)

    async def direct_notify(
        nid: int, summary: str, body: str, *, expire: int = 0, urgency: int = 1
    ) -> None:
        await daemon._device_notify(proto.notify(
            nid, "HISTORY COMPOSED", summary, body, urgency, expire, 1790411700,
            body_max_bytes=4096,
        ))

    async def source_notify(summary: str) -> int:
        nonlocal serial
        current_serial = serial
        serial += 1
        await source._handle(Message(
            destination=NOTIFICATIONS_NAME,
            path="/org/freedesktop/Notifications",
            interface=NOTIFICATIONS_NAME,
            member="Notify",
            signature="susssasa{sv}i",
            body=["COMPOSED", 0, "", summary, "Archived body 東京 / ✓",
                  ["default", "Open"], {}, 10000],
            sender=":1.50",
            serial=current_serial,
        ))
        await source._handle(Message(
            message_type=MessageType.METHOD_RETURN,
            destination=":1.50",
            sender=":1.40",
            reply_serial=current_serial,
            signature="u",
            body=[9],
        ))
        for nid, message in list(source._outbox.items()):
            source._outbox.pop(nid)
            await daemon._device_notify(message)
        await daemon.action_manager.reconcile_once()
        await settle_arrival()
        return source._daemon_to_local[9]

    def latest_sync() -> tuple[dict, list[dict]]:
        begin = next(message for message in reversed(native.sent) if message.get("t") == "sync_begin")
        cards = [
            card
            for message in native.sent
            if message.get("t") == "sync_cards" and message.get("tx") == begin["tx"]
            for card in message["notifs"]
        ]
        return begin, cards

    def captured(name: str) -> bytes:
        return (native.artifacts / f"{name}.ppm").read_bytes()

    with patch("status349.daemon.time.monotonic", lambda: now[0]):
        # Negotiate from the actual native firmware hello with history intact.
        native.wire({"t": "hello"})
        hello = next(message for message in native.outbound if message.get("t") == "hello")
        assert proto.notification_history_capable(hello), hello
        assert proto.notification_actions_capable(hello), hello
        native.outbound.clear()
        await daemon._on_line(proto.encode(hello).decode().rstrip())
        begin, cards = latest_sync()
        assert begin["grouped"]["history"] is True and cards == []
        status = native.status()
        assert status["grouped"]["group"] == "notifications" and status["count"] == 0
        native.command("capture", name="history-empty")

        # A long UTF-8 body is extended for history while its legacy projection
        # remains bounded to 159 bytes.
        long_body = ("We've reviewed 東京 が → ✓. " * 90)
        await direct_notify(10, "LONG CJK BODY", long_body, expire=10000)
        sent = next(message for message in reversed(native.sent)
                    if message.get("t") == "notify" and message.get("id") == 10)
        assert len(sent["body"].encode("utf-8")) <= proto.NOTIFICATION_BODY_HISTORY_BYTES
        assert sent["body"].endswith("…") and "東京" in sent["body"]
        assert sent["history"]["age_ms"] == 0
        assert sent["history"]["remaining_ms"] == 1800000
        legacy = daemon._notification_projection(
            daemon.model.retained_notifs[10], history=False, now_mono=now[0]
        )
        assert len(legacy["body"].encode("utf-8")) <= proto.NOTIFICATION_BODY_LEGACY_BYTES
        assert legacy["body"].endswith("…")
        assert native.status()["ids"] == [10]
        await settle_arrival()
        native.command("capture", name="history-single-long-cjk")

        # The 10 s attention lease ends, while history stays in the new pane.
        await advance(10000)
        status = native.status()
        assert status["grouped"]["group"] == "notifications"
        assert 10 in status["ids"] and 10 in daemon.model.retained_notifs
        native.command("capture", name="history-after-popup-timeout")

        # Browse older/newer cards vertically. Horizontal movement is inert.
        await direct_notify(11, "SECOND CARD", "A second retained card.")
        await settle_arrival()
        before_focus = native.status()["deck"]["focus_id"]
        await swipe(dy=-90)
        browsed = native.status()
        assert browsed["deck"]["focus_id"] != before_focus
        assert daemon._manual_notifications
        before_horizontal = (browsed["deck"]["focus_id"], browsed["deck"]["position"])
        await swipe(dx=-120)
        after_horizontal = native.status()
        assert (after_horizontal["deck"]["focus_id"], after_horizontal["deck"]["position"]) == before_horizontal
        assert after_horizontal["grouped"]["group"] == "notifications"
        native.command("capture", name="history-vertical-browse")

        # After 30 s idle, a new arrival can take focus again through the host input.
        await advance(30001)
        assert not daemon._manual_notifications
        assert any(message.get("action") == "browse" and message.get("manual") is False
                   for message in input_trace)
        await direct_notify(12, "FOCUS AFTER IDLE", "New arrival after browsing.")
        await settle_arrival()
        assert native.status()["deck"]["focus_id"] == 12

        # Closing the desktop notification archives its text and disables Open.
        archived_id = await source_notify("ARCHIVED ACTION")
        assert native.status()["actions"]["open"]
        await source._handle(Message(
            message_type=MessageType.SIGNAL,
            sender=":1.40",
            path="/org/freedesktop/Notifications",
            interface=NOTIFICATIONS_NAME,
            member="NotificationClosed",
            signature="uu",
            body=[9, 1],
        ))
        await daemon.action_manager.reconcile_once()
        status = native.status()
        archived_open = next(item for item in status["actions"]["open"] if item["id"] == archived_id)
        assert archived_open["state"] == "unavailable"
        assert archived_id in status["ids"] and archived_id in daemon.model.retained_notifs
        native.command("capture", name="history-open-unavailable")
        native.command("press", x=596, y=30, ms=1)
        native.command("release", x=596, y=30, ms=1)
        await route_inputs()
        assert provider.invocations == []

        # Explicit × removes the archived record through host input, with a
        # production dismissal transition before the empty result settles.
        frame_before_remove = captured("history-open-unavailable")
        native.command("press", x=600, y=110, ms=1)
        native.command("release", x=600, y=110, ms=1)
        await route_inputs()
        native.command("capture", name="history-remove-start")
        await advance(75)
        native.command("capture", name="history-remove-mid")
        await advance(125)
        native.command("capture", name="history-remove-settled")
        assert frame_before_remove != captured("history-remove-mid")
        assert captured("history-remove-mid") != captured("history-remove-settled")
        status = native.status()
        assert archived_id not in status["ids"]
        assert archived_id not in daemon.model.retained_notifs
        assert status["grouped"]["group"] == "notifications"

        # Two records share a 30-minute deadline. Full sync and a reconnect
        # handshake keep their original revisions and remaining lifetime.
        await direct_notify(50, "EXPIRY WITHOUT REPLACEMENT", "Original record.")
        await direct_notify(51, "EXPIRY WITH REPLACEMENT", "Will be renewed.")
        await settle_arrival()
        born = daemon.model.retained_received_mono[50]
        first_revisions = {
            nid: daemon.model.retained_history_rev[nid] for nid in (50, 51)
        }
        assert daemon.model.retained_received_mono[51] == born
        await advance(1790000)
        await daemon._send_sync()
        begin, cards = latest_sync()
        assert begin["grouped"]["history"] is True
        near_expiry = {card["id"]: card["history"] for card in cards}
        assert {50, 51} <= set(near_expiry)
        for nid in (50, 51):
            assert near_expiry[nid]["rev"] == first_revisions[nid]
            assert near_expiry[nid]["age_ms"] >= 1790000
            assert 0 < near_expiry[nid]["remaining_ms"] <= 10000
            assert daemon.model.retained_received_mono[nid] == born

        # Model the host-side link reset, then let the real hello trigger the
        # same full cache transfer used after reconnect/replug.
        async with daemon._state_lock:
            daemon._grouped_enabled = False
            daemon._history_enabled = False
            daemon._dashboard_capable = False
            daemon._card_sync_capacity = None
            daemon._device_boot_id = None
        await daemon._on_line(proto.encode(hello).decode().rstrip())
        begin, cards = latest_sync()
        assert begin["grouped"]["history"] is True
        reconnected = {card["id"]: card["history"] for card in cards}
        assert {50, 51} <= set(reconnected)
        assert all(reconnected[nid]["rev"] == first_revisions[nid] for nid in (50, 51))
        assert all(0 < reconnected[nid]["remaining_ms"] <= 10000 for nid in (50, 51))
        assert {50, 51} <= set(native.status()["ids"])

        # Replace one record just before the original deadline. Its new
        # revision renews retention; the other expires locally with no host
        # traffic, then the host purge and full sync agree with the device.
        await advance(9000)
        await direct_notify(51, "REPLACED BEFORE EXPIRY", "Fresh text and lifetime.")
        renewed = next(message for message in reversed(native.sent)
                       if message.get("t") == "notify" and message.get("id") == 51)
        assert renewed["history"]["rev"] > first_revisions[51]
        assert renewed["history"]["age_ms"] == 0
        assert renewed["history"]["remaining_ms"] == 1800000
        await settle_arrival()
        # Expiry of a captured destination during a real vertical gesture
        # must return to its surviving source without resurrecting the target.
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=350, y=50, ms=100)
        await route_inputs()
        await advance(1000)
        status = native.status()
        assert 50 not in status["ids"] and 51 in status["ids"]
        assert 50 in daemon.model.retained_notifs  # Host has not run expiry yet.
        native.command("release", x=350, y=50, ms=1)
        await route_inputs()
        await advance(200)
        assert native.status()["deck"]["focus_id"] == 51
        async with daemon._state_lock:
            expired = await daemon._expire_history_locked(now[0])
        assert 50 in expired and 51 not in expired
        assert 50 not in daemon.model.retained_notifs and 51 in daemon.model.retained_notifs
        await daemon._send_sync()
        status = native.status()
        assert status["ids"] == [51]
        assert status["grouped"]["group"] == "notifications"

        # Last-card expiry while pressed cancels controls/gesture ownership.
        # A late release cannot reopen or dismiss the already removed record.
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=350, y=50, ms=100)
        await route_inputs()
        await advance(1800000)
        assert native.status()["ids"] == []
        native.command("release", x=350, y=50, ms=1)
        await route_inputs()
        await advance(200)
        async with daemon._state_lock:
            await daemon._expire_history_locked(now[0])
        await daemon._send_sync()
        status = native.status()
        assert status["ids"] == [] and status["grouped"]["group"] == "notifications"

    print("host notification history -> production parser/state -> LVGL -> host input: passed")


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
