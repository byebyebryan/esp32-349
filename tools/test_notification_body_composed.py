"""Replay styled notification bodies through host conversion, C state and LVGL."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

from dbus_next import Message, MessageType

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host" / "src"))
from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.sources.notifications import NOTIFICATIONS_NAME
from test_grouped_composed import Native
from test_notification_actions_composed import Provider


def _rendered(native: Native) -> list[dict]:
    result = native.command("readback")
    return result["readback"]["rendered_cards"]


def _readback(native: Native) -> dict:
    return native.command("readback")["readback"]


def _card(native: Native, nid: int) -> dict:
    return next(
        card for card in _rendered(native) if card["valid"] and card["id"] == nid
    )


def _slot_or_none(native: Native, nid: int) -> dict | None:
    return next(
        (card for card in _rendered(native) if card["valid"] and card["id"] == nid),
        None,
    )


async def scenario(native: Native) -> None:
    now = [1000.0]
    native.clock_hook = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    cfg = default_config()
    cfg.notifications.device_open = "dms"
    daemon = Daemon(cfg, asyncio.Event())
    provider = Provider()
    daemon.action_manager.provider = provider
    daemon._sample = lambda: {"cpu": 0.18, "mem": 0.43, "network": True}
    daemon.clock.read = lambda: (1790411700, -7 * 3600)
    daemon._writer = object()
    source = daemon.notifications
    source._server_owner, source._server_pid = ":1.40", provider.pid
    source_serial = 1

    async def write(message: dict) -> bool:
        native.wire(message)
        return True

    daemon._write_message = write

    async def route_inputs() -> list[dict]:
        messages, native.outbound = native.outbound, []
        inputs = [message for message in messages if message.get("t") == "input"]
        for message in inputs:
            await daemon._handle_input(message)
        for _ in range(8):
            await asyncio.sleep(0)
        return inputs

    async def advance(ms: int) -> list[dict]:
        native.command("advance", ms=ms)
        async with daemon._state_lock:
            await daemon._expire_presentation_locked(now[0])
            if (
                daemon._pending_present_due is not None
                and daemon._pending_present_due <= now[0]
            ):
                await daemon._publish_pending_presentation_locked(now[0])
        return await route_inputs()

    async def settle() -> None:
        await advance(301)
        await advance(200)

    async def direct_notify(nid: int, body: str, runs: list[dict] | None) -> None:
        await daemon._device_notify(
            proto.notify(
                nid,
                "BODY STYLE",
                f"BODY {nid}",
                body,
                1,
                0,
                1790411700,
                body_runs=runs,
            )
        )

    def capture(name: str) -> bytes:
        native.command("capture", name=name)
        path = native.artifacts / f"{name}.ppm"
        assert path.is_file(), path
        frame = path.read_bytes()
        assert frame.startswith(b"P6\n640 172\n255\n"), path
        return frame

    with (
        patch("status349.daemon.time.monotonic", lambda: now[0]),
        patch("status349.sources.notifications.time.monotonic", lambda: now[0]),
    ):
        # Negotiate only from the production C hello and use its full history,
        # cache, dashboard and body-style capability set.
        native.wire({"t": "hello"})
        hello = next(
            message for message in native.outbound if message.get("t") == "hello"
        )
        assert proto.notification_body_style_capable(hello), hello
        assert proto.notification_history_capable(hello), hello
        assert proto.notification_actions_capable(hello), hello
        native.outbound.clear()
        await daemon._on_line(proto.encode(hello).decode("utf-8").rstrip())

        raw_body = (
            "<b>Bold</b> and <i>italic</i> and <b><i>both 東京</i></b><br>東/東京 • ✓"
        )
        call_body = [
            "Desktop App",
            0,
            "",
            "Formatting acceptance",
            raw_body,
            ["default", "Open"],
            {},
            10000,
        ]
        await source._handle_notify(
            Message(
                destination=NOTIFICATIONS_NAME,
                path="/org/freedesktop/Notifications",
                interface=NOTIFICATIONS_NAME,
                member="Notify",
                signature="susssasa{sv}i",
                body=call_body,
                sender=":1.50",
                serial=source_serial,
            )
        )
        local_id = next(iter(source._outbox))
        display_message = source._outbox[local_id]
        original_action_body = {
            "app": "Desktop App",
            "summary": "Formatting acceptance",
            "body": raw_body,
            "default_label": "Open",
        }
        expected_display = "Bold and italic and both 東京\n東/東京 • ✓"
        expected_runs = [
            {"start": 0, "end": 4, "style": 1},
            {"start": 9, "end": 15, "style": 2},
            {"start": 20, "end": 31, "style": 3},
        ]
        assert display_message["body"] == expected_display
        assert display_message["body_runs"] == expected_runs
        await source._handle(
            Message(
                message_type=MessageType.METHOD_RETURN,
                destination=":1.50",
                sender=":1.40",
                reply_serial=source_serial,
                signature="u",
                body=[9],
            )
        )
        source_serial += 1
        candidate = source.action_candidate(local_id)
        assert candidate is not None and candidate["expected"] == original_action_body
        assert candidate["expected"]["body"] == raw_body

        source._outbox.pop(local_id)
        await daemon._device_notify(display_message)
        await daemon.action_manager.reconcile_once()
        await settle()

        status = native.status()
        assert status["ids"] == [local_id]
        assert status["actions"]["open"] == [
            {
                "id": local_id,
                "rev": status["actions"]["open"][0]["rev"],
                "state": "ready",
            }
        ]
        rendered = _card(native, local_id)
        assert rendered["body_text"] == expected_display
        assert rendered["body_runs"] == expected_runs
        assert rendered["span_visible"]
        assert rendered["span_overflow"] == 1  # LV_SPAN_OVERFLOW_ELLIPSIS
        assert (
            rendered["body_rect"]
            == rendered["span_rect"]
            == {
                "x": 12,
                "y": 56,
                "width": 376,
                "height": 66,
            }
        )
        segments = rendered["span_segments"]
        assert [(segment["text"], segment["style"]) for segment in segments] == [
            ("Bold", 1),
            (" and ", 0),
            ("italic", 2),
            (" and ", 0),
            ("both 東京", 3),
            ("\n東/東京 • ✓", 0),
        ]
        cjk_segment = next(segment for segment in segments if "東京" in segment["text"])
        assert cjk_segment["style"] == 3
        assert cjk_segment["cjk_resolved_font"] == "status_text_16"
        assert cjk_segment["cjk_fallback_regular"]
        regular_cjk_segment = next(
            segment
            for segment in segments
            if segment["text"].startswith("\n") and "東京" in segment["text"]
        )
        assert regular_cjk_segment["style"] == 0
        assert regular_cjk_segment["cjk_resolved_font"] == "status_text_16"
        frame_four_fonts = capture("notification-body-four-fonts-cjk-multiline")

        # Open still uses the source's raw body and default action while the
        # card shows the converted body. The actual LVGL button reaches the
        # action provider with the existing bound identity.
        native.command("press", x=600, y=44, ms=1)
        native.command("release", x=600, y=44, ms=1)
        await route_inputs()
        assert len(provider.invocations) == 1
        assert provider.invocations[0]["id"] == local_id
        assert source.action_candidate(local_id)["expected"] == original_action_body

        # Spans stay intact across a held pointer frame and replacement. The
        # old body remains visible while held, then the new body and ranges
        # appear together after release.
        old_segments = [
            segment["id"] for segment in _card(native, local_id)["span_segments"]
        ]
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=355, y=102, ms=50)
        await route_inputs()
        assert _readback(native)["input_state"] in {"pressed", "dragging"}
        during_pointer = _card(native, local_id)
        assert [
            segment["id"] for segment in during_pointer["span_segments"]
        ] == old_segments

        replacement_body = "Replacement text\n東京"
        replacement_runs = [{"start": 12, "end": 16, "style": 1}]
        await direct_notify(local_id, replacement_body, replacement_runs)
        frozen = _card(native, local_id)
        assert frozen["body_text"] == expected_display
        assert frozen["body_runs"] == expected_runs
        held_deadline_inputs = await advance(30001)
        assert not any(
            message.get("action") == "browse" and message.get("manual") is False
            for message in held_deadline_inputs
        )
        assert _readback(native)["manual"]
        assert [
            segment["id"] for segment in _card(native, local_id)["span_segments"]
        ] == old_segments
        native.command("release", x=355, y=102, ms=1)
        await route_inputs()
        await advance(200)
        replaced = _card(native, local_id)
        assert replaced["body_text"] == replacement_body
        assert replaced["body_runs"] == replacement_runs
        assert [segment["text"] for segment in replaced["span_segments"]] == [
            "Replacement ",
            "text",
            "\n東京",
        ]

        # Replacement by plain text clears visible styles without losing the
        # notification or falling back to a truncated legacy body.
        plain_body = "Plain replacement\n東京"
        await direct_notify(local_id, plain_body, None)
        plain = _card(native, local_id)
        assert plain["body_text"] == plain_body
        assert plain["body_runs"] == []
        assert not plain["span_visible"]

        # Stage a second styled card in the production host cache without
        # delivering it incrementally. The native C cache and rendered cards
        # stay unchanged until sync_commit publishes the complete snapshot.
        staged_body = "Committed together\n東京"
        staged_runs = [{"start": 10, "end": 18, "style": 1}]
        native_writer = daemon._write_message

        async def defer_wire(_message: dict) -> bool:
            return True

        daemon._write_message = defer_wire
        atomic_body = "Atomic snapshot\n東京"
        atomic_runs = [{"start": 0, "end": 6, "style": 2}]
        await direct_notify(local_id, atomic_body, atomic_runs)
        await direct_notify(local_id + 1, staged_body, staged_runs)
        daemon._write_message = native_writer
        staged_frames: list[dict] = []

        async def collect_sync(message: dict) -> bool:
            staged_frames.append(message)
            return True

        daemon._write_message = collect_sync
        await daemon._send_sync()
        daemon._write_message = native_writer
        assert staged_frames[-1]["t"] == "sync_commit"
        assert any(
            card["id"] == local_id + 1 and card["body_runs"] == staged_runs
            for message in staged_frames
            if message["t"] == "sync_cards"
            for card in message["notifs"]
        )
        old_ids = native.status()["ids"]
        assert local_id + 1 not in old_ids
        before_commit = _card(native, local_id)
        assert before_commit["body_text"] == plain_body
        assert before_commit["body_runs"] == [] and not before_commit["span_visible"]
        for message in staged_frames[:-1]:
            native.wire(message)
        assert native.status()["ids"] == old_ids
        still_old = _card(native, local_id)
        assert still_old["body_text"] == plain_body
        assert still_old["body_runs"] == [] and not still_old["span_visible"]
        assert _slot_or_none(native, local_id + 1) is None
        native.wire(staged_frames[-1])
        assert native.status()["ids"] == [local_id, local_id + 1]
        atomic = _card(native, local_id)
        assert atomic["body_text"] == atomic_body
        assert atomic["body_runs"] == atomic_runs and atomic["span_visible"]
        committed = _card(native, local_id + 1)
        assert committed["body_text"] == staged_body
        assert committed["body_runs"] == staged_runs

        # A host connected to a history peer without the style capability must
        # send plain bodies in both the cache transaction and later deltas.
        legacy_hello = dict(hello)
        legacy_hello["cap"] = [
            capability
            for capability in hello["cap"]
            if capability != proto.NOTIFICATION_BODY_STYLE_CAPABILITY
        ]
        assert proto.notification_history_capable(legacy_hello)
        assert not proto.notification_body_style_capable(legacy_hello)
        await daemon._on_line(proto.encode(legacy_hello).decode("utf-8").rstrip())
        assert not daemon._body_style_enabled
        latest_begin = next(
            message for message in reversed(native.sent) if message["t"] == "sync_begin"
        )
        legacy_sync = [
            card
            for message in native.sent
            if message.get("t") == "sync_cards"
            and message.get("tx") == latest_begin["tx"]
            for card in message["notifs"]
        ]
        assert legacy_sync and all("body_runs" not in card for card in legacy_sync)
        await direct_notify(
            local_id + 2,
            "Older peer plain fallback",
            [
                {"start": 0, "end": 5, "style": 1},
            ],
        )
        legacy_delta = next(
            message
            for message in reversed(native.sent)
            if message.get("t") == "notify" and message.get("id") == local_id + 2
        )
        assert "body_runs" not in legacy_delta
        await daemon._send_sync()
        legacy_cards = [
            card
            for message in reversed(native.sent)
            if message.get("t") == "sync_cards"
            for card in message["notifs"]
        ]
        legacy_card = next(card for card in legacy_cards if card["id"] == local_id + 2)
        assert "body_runs" not in legacy_card

        await daemon._on_line(proto.encode(hello).decode("utf-8").rstrip())
        assert daemon._body_style_enabled
        # Capture a maximum-length styled body: clipping stays UTF-8 safe,
        # appends a regular ellipsis, and LVGL keeps it inside the same span box.
        long_body = "B" * 520
        long_runs = [{"start": 0, "end": len(long_body), "style": 1}]
        # Replace the currently focused card so the assertion covers its
        # actual visible span rather than an off-screen history entry.
        await direct_notify(local_id, long_body, long_runs)
        await settle()
        ellipsis_card = _card(native, local_id)
        assert len(ellipsis_card["body_text"].encode("utf-8")) == 511
        assert ellipsis_card["body_text"].endswith("…")
        assert ellipsis_card["body_runs"] == [{"start": 0, "end": 508, "style": 1}]
        assert ellipsis_card["span_segments"][-1]["text"] == "…"
        assert ellipsis_card["span_segments"][-1]["style"] == 0
        assert ellipsis_card["body_rect"] == ellipsis_card["span_rect"]
        frame_ellipsis = capture("notification-body-production-font-ellipsis")
        assert frame_ellipsis != frame_four_fonts

        # A cache close can remove the selected notification while a touch is
        # held. The removed styled card must not remain visible or reappear
        # after release, and the other history slots keep their own spans.
        native.command("press", x=350, y=100, ms=1)
        native.command("move", x=355, y=102, ms=50)
        await route_inputs()
        assert _readback(native)["input_state"] in {"pressed", "dragging"}
        native.wire(proto.close(local_id, total=2, session=daemon.grouped_session))
        await advance(50)
        assert local_id not in native.status()["ids"]
        assert _slot_or_none(native, local_id) is None
        assert _readback(native)["input_state"] == "ignored"
        native.command("release", x=355, y=102, ms=1)
        await advance(300)
        assert local_id not in native.status()["ids"]
        assert _slot_or_none(native, local_id) is None
        assert {card["id"] for card in _rendered(native) if card["valid"]} == {
            local_id + 1,
            local_id + 2,
        }

    print(
        "host body conversion -> real C protocol/state -> LVGL spans and actions: passed"
    )


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
