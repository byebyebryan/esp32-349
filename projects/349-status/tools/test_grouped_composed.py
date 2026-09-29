#!/usr/bin/env python3
"""Host envelopes -> production firmware parser/state -> LVGL -> host input.

Uses virtual clocks and the native platform substitutions, never the USB board.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import select
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host" / "src"))
from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon


class Native:
    def __init__(self, executable: Path, artifacts: Path):
        artifacts.mkdir(parents=True, exist_ok=True)
        self.artifacts = artifacts
        self.trace = (artifacts / "composed-trace.jsonl").open("w")
        self.errors = (artifacts / "native-stderr.log").open("w")
        self.process = subprocess.Popen([str(executable), "--jsonl", str(artifacts)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors, text=True, bufsize=1)
        self.outbound: list[dict] = []
        self.sent: list[dict] = []
        self.clock_hook = lambda _ms: None

    def command(self, kind: str, **fields) -> dict:
        command = {"type": kind, **fields}
        if kind in {"press", "move", "release"}:
            command = {"type": "pointer", "action": kind, **fields}
        self.trace.write(json.dumps({"command": command}) + "\n"); self.trace.flush()
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(command) + "\n"); self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 5)[0]:
            raise AssertionError(f"Native command timed out: {command}")
        line = self.process.stdout.readline()
        assert line, f"Native exited ({self.process.poll()}): {command}"
        result = json.loads(line)
        self.trace.write(json.dumps({"result": result}) + "\n"); self.trace.flush()
        assert result.get("ok"), result
        if kind in {"press", "move", "release", "advance"}:
            self.clock_hook(fields.get("ms", 1))
        self.outbound.extend(result.get("outbound", []))
        return result

    def wire(self, message: dict) -> None:
        self.sent.append(message)
        # Exercise the real framing serializer before the native parser strips prefix.
        data, decoded = proto.classify(proto.encode(message).decode().rstrip("\n"))
        assert data and decoded is not None
        self.command("wire", message=decoded)

    def status(self) -> dict:
        result = self.command("readback")
        message = next(m for m in reversed(result["outbound"]) if m.get("t") == "cards_status")
        assert isinstance(message, dict), result
        status = proto.card_status(message)
        assert status is not None, message
        return status

    def close(self) -> None:
        if self.process.stdin: self.process.stdin.close()
        try: self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait()
        self.errors.close(); self.trace.close()


def legacy_grouped_hello(hello: dict) -> dict:
    """Keep this scenario on the Home/presentation contract with real peers."""
    assert proto.grouped_ui_capable(hello), hello
    capabilities = hello.get("cap")
    assert isinstance(capabilities, list), hello
    assert proto.notification_history_capable(hello), hello
    legacy = dict(hello)
    legacy["cap"] = [cap for cap in capabilities if cap != proto.NOTIFICATION_HISTORY_CAPABILITY]
    assert len(legacy["cap"]) == len(capabilities) - 1
    assert not proto.notification_history_capable(legacy), legacy
    return legacy


async def scenario(native: Native) -> None:
    now = [1000.0]
    def advance_clock(ms):
        now[0] += ms / 1000
    native.clock_hook = advance_clock
    daemon = Daemon(default_config(), asyncio.Event())
    daemon._sample = lambda: {"cpu": .18, "mem": .43, "network": True}
    daemon.clock.read = lambda: (1790411700, -7 * 3600)
    # Presence is required for actual daemon write/presentation paths.
    daemon._writer = object()

    async def write(message: dict) -> bool:
        native.wire(message)
        return True
    daemon._write_message = write

    async def route_inputs() -> None:
        messages, native.outbound = native.outbound, []
        for message in messages:
            if message.get("t") == "input": await daemon._handle_input(message)

    async def advance(ms: int) -> None:
        native.command("advance", ms=ms)
        async with daemon._state_lock:
            await daemon._expire_presentation_locked(now[0])
            if daemon._pending_present_due is not None and daemon._pending_present_due <= now[0]:
                await daemon._publish_pending_presentation_locked(now[0])
        await route_inputs()

    async def swipe(dx: int = 0, dy: int = 0) -> None:
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=350 + dx // 2, y=100 + dy // 2, ms=100)
        await route_inputs()
        native.command("move", x=350 + dx, y=100 + dy, ms=100)
        native.command("release", x=350 + dx, y=100 + dy, ms=1)
        await route_inputs()
        await advance(200)

    async def notify(nid: int, urgency: int = 1, expire: int = 1000, summary: str | None = None):
        await daemon._device_notify(proto.notify(nid, "COMPOSED", summary or f"CARD {nid}",
            "We've / we’ve 東京 が → ✓", urgency, expire, 1790411700))

    def selected_text_pixels(name: str) -> bytes:
        # Selected card title/body during the +50 px captured drag, excluding cues.
        magic, size, scale, pixels = (native.artifacts / f"{name}.ppm").read_bytes().split(b"\n", 3)
        assert (magic, size, scale) == (b"P6", b"640 172", b"255")
        return b"".join(pixels[(y * 640 + 180) * 3:(y * 640 + 604) * 3]
                        for y in range(96, 148))

    with patch("status349.daemon.time.monotonic", lambda: now[0]):
        # Use the real firmware capability announcement, not an invented peer.
        native.wire({"t": "hello"})
        hello = next(m for m in native.outbound if m.get("t") == "hello")
        assert proto.notification_history_capable(hello), hello
        legacy_hello = legacy_grouped_hello(hello)
        native.outbound.clear()
        await daemon._on_line(proto.encode(legacy_hello).decode().rstrip())
        await advance(100)
        assert native.status()["grouped"]["group"] == "home"
        await swipe(dx=-120)
        empty = native.status()
        assert empty["count"] == 0 and empty["grouped"]["group"] == "notifications"
        assert empty["grouped"]["manual"] and daemon._manual_notifications
        native.command("capture", name="composed-empty-notifications")
        await swipe(dy=-70)
        assert native.status()["grouped"]["group"] == "notifications"
        await swipe(dx=120)
        assert native.status()["grouped"]["group"] == "home" and not daemon._manual_notifications

        await notify(1)
        await advance(301)
        await advance(200)  # Automatic Home-to-Notifications transition.
        status = native.status()
        assert status["ids"] == [1] and status["grouped"]["present_id"] == 1
        native.command("capture", name="composed-automatic")
        # A newer normal lease sent while pressed must not leave the host
        # unaware of manual takeover when the acquired drag snaps back.
        native.command("press", x=350, y=100, ms=1)
        await notify(1)
        await advance(301)
        native.command("move", x=370, y=100, ms=100)
        await route_inputs()
        native.command("release", x=370, y=100, ms=1)
        await advance(200)
        assert native.status()["grouped"]["manual"]
        assert daemon._manual_notifications, "Canceled navigation lost newer-generation manual takeover"
        await swipe(dx=120)
        # The same newer-generation takeover must survive a held × tap.
        await notify(2)
        await advance(301)
        await advance(200)
        native.command("press", x=600, y=35, ms=1)
        await notify(1)
        await advance(301)
        native.command("release", x=600, y=35, ms=1)
        await route_inputs()
        await advance(200)  # Dismissal hands the viewport to its successor.
        assert daemon._manual_notifications, "Held dismiss lost newer-generation manual takeover"
        assert 2 not in daemon.model.retained_notifs
        assert native.status()["grouped"]["manual"]
        await swipe(dx=120)
        await advance(1001)
        assert native.status()["grouped"]["group"] == "home"
        assert 1 in daemon.model.retained_notifs
        await daemon._send_sync(); await advance(100)
        assert native.status()["grouped"]["group"] == "home"
        await swipe(dx=-120)
        assert native.status()["grouped"]["manual"]
        assert daemon._manual_notifications
        await notify(2, expire=0)
        await advance(301)
        assert native.status()["deck"]["focus_id"] == 1

        # A critical visit freezes during a real LVGL held gesture, then preempts.
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=350, y=150, ms=100)
        await route_inputs()
        native.command("capture", name="composed-held-before")
        await notify(1, expire=0, summary="REPLACEMENT WHILE HELD")
        await notify(3, urgency=2, expire=0)
        await daemon._send_sync(); await advance(100)
        assert native.status()["deck"]["focus_id"] == 1
        native.command("capture", name="composed-held-critical")
        assert selected_text_pixels("composed-held-before") == selected_text_pixels("composed-held-critical")
        native.command("release", x=350, y=150, ms=1)
        await advance(200)
        assert native.status()["deck"]["focus_id"] == 3
        await advance(200)  # Deferred critical arrival follows the user's settle.

        # UI × produces the session-scoped input consumed by actual host handling.
        native.command("press", x=608, y=34, ms=1)
        native.command("release", x=608, y=34, ms=1)
        await route_inputs(); await advance(100)
        assert 3 not in daemon.model.retained_notifs
        await daemon._send_sync(); await advance(100)
        assert 3 not in native.status()["ids"]
        await notify(3, urgency=2, expire=0, summary="REPLACED AFTER LOCAL DISMISS")
        await advance(100)
        assert native.status()["deck"]["focus_id"] == 3

        # Expired attention and stale sessions cannot cancel a fresh critical visit.
        native.wire(proto.present(daemon.grouped_session, 1, 1, 0, 1))
        assert native.status()["grouped"]["present_id"] == 3
        native.wire(proto.close(3, total=0, session=daemon.grouped_session + 1))
        assert 3 in native.status()["ids"]

        # Retention eviction is bounded and refilled by the authoritative snapshot.
        await swipe(dx=120)
        for nid in range(4, 37): await notify(nid, expire=0)
        await daemon._send_sync(); await advance(100)
        status = native.status()
        assert status["ids"] == list(range(5, 37)), status
        assert status["count"] == 32 and status["overflow"] == 0

        # Atomic malformed staged metadata must preserve the committed cache.
        before = status["ids"]
        malformed = proto.card_sync_messages(daemon.model.card_snapshot(32, retained=True), 999,
            include_dashboard=True, grouped_session=daemon.grouped_session)[0]
        malformed["grouped"]["session"] = -1
        native.wire(malformed)
        assert native.status()["ids"] == before
        assert any(m.get("t") == "resync" for m in native.outbound)
        await daemon._send_sync(); await advance(100)
        native.command("capture", name="composed-capacity")

    print("host -> production parser/state -> LVGL -> host input: passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    native = Native(args.native.resolve(), args.artifacts.resolve())
    try: asyncio.run(scenario(native))
    finally: native.close()
