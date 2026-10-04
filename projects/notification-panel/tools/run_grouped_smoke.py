#!/usr/bin/env python3
"""Opt-in physical smoke recorder. Do not start until the observer is ready.

Uses an isolated instance of the production daemon's policy, without starting
its sources or IPC server. The normal daemon stays paused and retains its RAM
history. No desktop notifications or configuration files are changed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host" / "src"))
from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon
from status349.ipc import pause_path, socket_path
from status349.link import Link, open_port


async def ipc(command: str) -> dict:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(socket_path()), 3)
    try:
        writer.write((json.dumps({"cmd": command}) + "\n").encode())
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), 3))
        if result.get("ok") is False:
            raise RuntimeError(f"349d {command}: {result}")
        return result
    finally:
        writer.close()
        await writer.wait_closed()


class SmokeHost:
    """Production host policy with caller-provided serial/native transport."""
    def __init__(self, send, history: bool = False):
        self.send = send
        self.history = history
        self.hello: dict | None = None
        self.daemon = self.make_daemon()
        for name in ("A", "B", "C"):
            nid = self.daemon._allocate_local_notification_id()
            self.daemon.model.retain_notification(proto.notify(nid, "SMOKE", f"CARD {name}",
                "We've / we’ve 中文 与 → ✓. " + (
                    "The smaller body shows more content. Swipe vertically between recent notifications; "
                    "horizontal movement and body taps do nothing. Close with the large ×; "
                    "popup timeout keeps this card, with a ten-minute retention limit."
                    if history else "Swipe horizontally for groups; vertically for cards."),
                1, 0, int(time.time())))

    def make_daemon(self) -> Daemon:
        cfg = default_config()
        cfg.notifications.mode = "off"
        cfg.daemon.tick_s = .1
        daemon = Daemon(cfg, asyncio.Event())
        daemon._sample = lambda: {"cpu": .18, "mem": .43, "network": True}
        async def write(message):
            if daemon._writer is None:
                return False
            return await self.send(message)
        daemon._write_message = write
        return daemon

    async def connected(self, hello: dict) -> None:
        self.hello = hello
        if not self.history:
            # Default mode exercises the previous Home/grouped compatibility UI.
            hello = {**hello, "cap": [cap for cap in hello.get("cap", [])
                                     if cap != proto.NOTIFICATION_HISTORY_CAPABILITY]}
        elif not proto.notification_history_capable(hello):
            raise RuntimeError("Recorder requires notification-history-v1")
        self.daemon._writer = object()
        await self.daemon._on_line(proto.encode(hello).decode().rstrip())

    async def disconnected(self) -> None:
        daemon = self.daemon
        async with daemon._state_lock:
            daemon._writer = None
            daemon._grouped_enabled = False
            daemon._history_enabled = False
            daemon._card_sync_capacity = None
            daemon._dashboard_capable = False
            daemon._device_boot_id = None
            daemon._device_expected_generation = 0
            daemon._pending_present_id = None
            daemon._pending_present_due = None
            await daemon._end_presentation_locked(send_end=False)
            daemon._grouped_group = "notifications" if self.history else "home"
            daemon._manual_notifications = False

    async def reset_session(self) -> None:
        old = self.daemon
        self.daemon = self.make_daemon()
        self.daemon.model = old.model
        self.daemon.clock = old.clock
        self.daemon._next_local_notification_id = old._next_local_notification_id
        self.daemon.grouped_session = old.grouped_session % proto.IDENTITY_MAX + 1
        assert self.hello is not None
        await self.connected(self.hello)

    async def present_cards(self) -> None:
        if not self.daemon.model.retained_notifs:
            raise RuntimeError("No cards remain; finish and start a new session")
        message = next(reversed(self.daemon.model.retained_notifs.values()))
        async with self.daemon._state_lock:
            await self.daemon._present_notification_locked({**message, "expire": 0}, time.monotonic())

    async def timeout_card(self) -> int:
        await self.reset_session()
        result = await self.daemon._ipc_handler({"cmd": "notify", "app": "SMOKE",
            "summary": "10 SECOND PRESENTATION", "body": "Watch it return Home, then browse back. This card stays retained.",
            "expire": 10000, "urgency": 1})
        return result["id"]

    def ids(self) -> list[int]:
        return [n["id"] for n in self.daemon.model.card_snapshot(32, retained=True)["notifs"]]


class Recorder:
    def __init__(self, port: str, expected: str, artifacts: Path, history: bool = False):
        artifacts.mkdir(parents=True, exist_ok=True)
        self.artifacts = artifacts
        self.trace = (artifacts / "trace.jsonl").open("w")
        self.console = (artifacts / "console.log").open("w")
        self.port, self.expected = port, expected
        self.link: Link | None = None
        self.host = SmokeHost(self.send, history)
        self.jobs: list[asyncio.Task] = []
        self.latest: dict | None = None
        self.replug_armed = False
        self.lost_at: float | None = None
        self.previous_boot: int | None = None
        self.returned_at: float | None = None
        self.replug_results: list[dict] = []
        self.write_failed = False

    def event(self, kind: str, **fields) -> None:
        self.trace.write(json.dumps({"mono": time.monotonic(), "event": kind, **fields}) + "\n")
        self.trace.flush()

    async def send(self, message: dict) -> bool:
        if self.link is None:
            return False
        self.event("tx", message=message)
        try:
            self.link.send(message)
        except (OSError, serial.SerialException) as exc:
            self.event("write_error", error=str(exc))
            self.write_failed = True
            return False
        if message.get("t") == "present" and message.get("remaining_ms", 0) != 0:
            print(f"PRESENT id={message['id']} remaining_ms={message['remaining_ms']}", flush=True)
        return True

    async def accept(self, line: str) -> None:
        data, message = proto.classify(line)
        if message is not None:
            self.event("rx", message=message)
            if message.get("t") == "hello":
                if not proto.grouped_ui_capable(message) or message.get("build_sha") != self.expected:
                    raise RuntimeError(f"Unexpected firmware: {message}")
                if self.host.daemon._writer is None:
                    await self.host.connected(message)
                    return
            if message.get("t") == "input":
                print(f"INPUT {message.get('action')} {message.get('group', message.get('id'))}", flush=True)
            await self.host.daemon._on_line(line)
        elif not data:
            self.console.write(line + "\n"); self.console.flush()

    async def attach(self) -> None:
        self.link = Link(open_port(self.port))
        self.write_failed = False
        deadline = time.monotonic() + 6
        request_at = 0.0
        while self.host.daemon._writer is None:
            if time.monotonic() > deadline:
                raise RuntimeError("Board did not announce the expected firmware")
            if time.monotonic() >= request_at:
                await self.send(proto.hello())
                request_at = time.monotonic() + .5
            for line in self.link.lines(.05):
                await self.accept(line)
            await asyncio.sleep(.01)
        self.event("connected", hello=self.host.hello)

    async def start_jobs(self) -> None:
        self.jobs = [asyncio.create_task(self.host.daemon._tick_loop()),
                     asyncio.create_task(self.host.daemon._ping_loop()),
                     asyncio.create_task(self.poll())]

    async def stop_jobs(self) -> None:
        for job in self.jobs:
            job.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)
        self.jobs = []

    async def receive(self) -> None:
        while True:
            if self.link is None:
                assert self.lost_at is not None
                if time.monotonic() - self.lost_at > 45:
                    raise RuntimeError("USB did not return within 45 seconds of disconnect")
                if Path(self.port).exists():
                    await self.attach()
                    self.returned_at = time.monotonic()
                else:
                    await asyncio.sleep(.25)
            else:
                try:
                    if self.write_failed:
                        raise serial.SerialException("serial write failed")
                    for line in self.link.lines(.05):
                        await self.accept(line)
                except (OSError, serial.SerialException):
                    self.link.port.close(); self.link = None
                    if not self.replug_armed:
                        raise RuntimeError("Unexpected USB loss outside the armed replug check")
                    self.lost_at = time.monotonic()
                    self.latest = None
                    await self.host.disconnected()
                    self.event("disconnected", boot_id=self.previous_boot)
                    print("DISCONNECTED; waiting for USB return", flush=True)
            await asyncio.sleep(.01)

    async def poll(self) -> None:
        while True:
            if self.host.daemon._writer is not None:
                response = await self.host.daemon._query_device_cards()
                status = response.get("device_cards")
                if response.get("ok") and status and not status.get("view_pending"):
                    self.latest = status
                    if self.returned_at is not None:
                        group = status.get("grouped", {})
                        target_group = "notifications" if self.host.history else "home"
                        valid = (status["ids"] == self.host.ids() and group.get("group") == target_group
                                 and group.get("generation") == 0 and group.get("present_id") is None
                                 and status.get("deck", {}).get("enabled")
                                 and not status["deck"].get("stale"))
                        if valid:
                            assert self.host.hello is not None and self.lost_at is not None
                            assert self.host.hello["boot_id"] != self.previous_boot
                            result = {"elapsed_s": round(time.monotonic() - self.lost_at, 3),
                                      "hello": self.host.hello, "status": status}
                            self.replug_results.append(result); self.event("replug_passed", **result)
                            print(f"REPLUG PASS: {target_group}, {status['count']} retained cards, {result['elapsed_s']}s", flush=True)
                            self.replug_armed = False; self.returned_at = self.lost_at = None
                        elif time.monotonic() - self.returned_at > 5:
                            raise RuntimeError(f"Replug state did not reconcile: {status}")
            await asyncio.sleep(.4)

    async def command(self, command: str) -> bool:
        if command == "cards":
            await self.host.present_cards()
        elif command == "timeout":
            if self.host.history:
                raise RuntimeError("The Home timeout scenario is for legacy grouped mode")
            await self.stop_jobs()
            try:
                nid = await self.host.timeout_card()
            finally:
                await self.start_jobs()
            self.event("timeout_armed", id=nid)
            print(f"TIMEOUT CARD {nid}: 10 seconds after PRESENT; do not navigate until Home returns", flush=True)
        elif command == "replug":
            assert self.host.hello is not None
            self.previous_boot = self.host.hello["boot_id"]
            self.replug_armed = True
            self.event("replug_armed", ids=self.host.ids(), boot_id=self.previous_boot)
            print("REPLUG READY: unplug for two seconds, reconnect; sync will restore surviving cards", flush=True)
        elif command == "status":
            print(json.dumps(self.latest), flush=True)
        elif command == "finish":
            return False
        else:
            print("Commands: cards | timeout | replug | status | finish", flush=True)
        return True

    async def close(self) -> None:
        await self.stop_jobs()
        try:
            if self.link is not None and self.host.daemon._writer is not None:
                for nid in tuple(self.host.daemon.model.retained_notifs):
                    self.host.daemon.model.close_notification(nid)
                await self.host.daemon._send_sync()
        finally:
            if self.link is not None:
                self.link.port.close(); self.link = None
            self.trace.close(); self.console.close()


async def run(args) -> None:
    initial = await ipc("status")
    if initial.get("paused") or pause_path().exists():
        raise RuntimeError("349d is already paused; recorder will not take ownership of that pause")
    if not initial.get("link") or not initial.get("grouped"):
        raise RuntimeError("Expected a live grouped 349d before the physical session")
    baseline = await ipc("device_cards")
    normal_session = baseline.get("device_cards", {}).get("grouped", {}).get("session")
    if not baseline.get("ok") or normal_session is None:
        raise RuntimeError("Wait for a stable normal grouped readback before starting")
    recorder = Recorder(args.port, args.expected_build_sha, args.artifacts, getattr(args, "history", False))
    recorder.host.daemon.grouped_session = normal_session % proto.IDENTITY_MAX + 1
    commands: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    buffer = bytearray()
    def stdin_ready():
        chunk = os.read(sys.stdin.fileno(), 4096)
        if not chunk:
            loop.remove_reader(sys.stdin.fileno()); commands.put_nowait("finish")
        else:
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, tail = buffer.partition(b"\n"); buffer[:] = tail
                commands.put_nowait(line.decode().strip())
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, commands.put_nowait, "finish")
    receiver = None
    error = None
    paused = False
    try:
        # Ownership begins before the RPC: the server can apply pause even if
        # its response is lost. Initial preflight proved no preexisting pause.
        paused = True
        await ipc("pause")
        await asyncio.sleep(.25)
        await recorder.attach()
        receiver = asyncio.create_task(recorder.receive())
        await recorder.start_jobs()
        loop.add_reader(sys.stdin.fileno(), stdin_ready)
        deadline = time.monotonic() + 1200
        print("READY: three long-lived cards in Notifications. Commands: cards | status | finish" if recorder.host.history else
              "READY: three persistent cards cached at Home. Look, then use 'cards' for touch. Commands: cards | timeout | replug | status | finish", flush=True)
        while time.monotonic() < deadline:
            for job in [receiver, *recorder.jobs]:
                if job.done():
                    job.result(); raise RuntimeError("Recorder job ended unexpectedly")
            try:
                command = await asyncio.wait_for(commands.get(), .25)
            except asyncio.TimeoutError:
                continue
            if not await recorder.command(command):
                break
        else:
            raise RuntimeError("Physical session exceeded its 20-minute limit")
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        loop.remove_reader(sys.stdin.fileno())
        if receiver is not None:
            receiver.cancel(); await asyncio.gather(receiver, return_exceptions=True)
        try:
            await recorder.close()
        finally:
            restoration = None
            if paused:
                try:
                    await ipc("resume")
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        host_status = await ipc("status")
                        if host_status.get("link") and host_status.get("grouped"):
                            reply = await ipc("device_cards")
                            view = reply.get("device_cards", {})
                            if (reply.get("ok") and view.get("deck", {}).get("enabled")
                                    and not view["deck"].get("stale")
                                    and view.get("grouped", {}).get("session") == normal_session
                                    and view.get("count", -1) + view.get("overflow", -1) == host_status.get("retained_notifs")):
                                restoration = {"host": host_status, "device": view}; break
                        await asyncio.sleep(.25)
                    if restoration is None:
                        error = error or "normal restoration did not settle within eight seconds"
                        print("Normal daemon resumed; readback did not settle within eight seconds", flush=True)
                except Exception as exc:
                    pause_path().unlink(missing_ok=True)
                    error = error or f"normal restoration: {exc!r}"
            result = {"controller_error": error, "replug": recorder.replug_results,
                      "normal_restoration": restoration,
                      "scope": "Recorder state/serial evidence only; human visual/touch answers are recorded separately"}
            (args.artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print("FINISHED: normal mirroring restored" if restoration else
                  "FINISHED: normal restoration unverified", flush=True)
            if paused and restoration is None:
                raise RuntimeError(error or "Could not verify normal restoration")


async def cleanup_self_test(native_path: Path, artifacts: Path) -> None:
    """Exercise ownership/error cleanup with fake IPC and native transport."""
    from types import SimpleNamespace
    from unittest.mock import patch
    from test_grouped_composed import Native
    for case in ("finish", "attach-error", "pause-response-error", "already-paused"):
        folder = artifacts / case
        folder.mkdir(parents=True, exist_ok=True)
        flag = folder / "fake-paused"
        flag.unlink(missing_ok=True)
        calls = []
        state = {"paused": case == "already-paused"}
        if state["paused"]: flag.touch()
        native = Native(native_path, folder / "native")
        native.wire(proto.hello())
        hello = next(m for m in native.outbound if m.get("t") == "hello")
        class FakeLink:
            def __init__(self): self.port = self
            def send(self, message): native.wire(message)
            def lines(self, _timeout):
                native.command("advance", ms=60)
                messages, native.outbound = native.outbound, []
                return iter(proto.encode(m).decode().rstrip() for m in messages)
            def close(self):
                native.command("advance", ms=100)
                assert native.status()["count"] == 0, "Synthetic cache was not cleared"
        class FakeRecorder(Recorder):
            async def attach(self):
                if case == "attach-error": raise RuntimeError("simulated attach error")
                self.link = FakeLink()
                await self.host.connected(hello)
        async def fake_ipc(command):
            calls.append(command)
            if command == "pause":
                state["paused"] = True; flag.touch()
                if case == "pause-response-error": raise RuntimeError("simulated lost pause reply")
            if command == "resume": state["paused"] = False; flag.unlink(missing_ok=True)
            if command == "device_cards":
                return {"ok": True, "device_cards": {"count": 0, "overflow": 0, "ids": [],
                    "deck": {"enabled": True, "stale": False},
                    "grouped": {"enabled": True, "session": 999, "group": "home"}}}
            return {"paused": state["paused"], "link": True, "grouped": True, "retained_notifs": 0}
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"finish\n"); os.close(write_fd)
        try:
            with os.fdopen(read_fd) as input_pipe, \
                 patch.object(sys.modules[__name__], "Recorder", FakeRecorder), \
                 patch.object(sys.modules[__name__], "ipc", fake_ipc), \
                 patch.object(sys.modules[__name__], "pause_path", lambda: flag), \
                 patch.object(sys, "stdin", input_pipe):
                args = SimpleNamespace(port="native-only", expected_build_sha=hello["build_sha"], artifacts=folder)
                try:
                    await run(args)
                    assert case == "finish"
                except RuntimeError as exc:
                    assert case != "finish", exc
                    assert "simulated" in str(exc) or "already paused" in str(exc), exc
            if case == "already-paused":
                assert calls == ["status"] and flag.exists()
            else:
                assert "resume" in calls and not state["paused"] and not flag.exists()
                result = json.loads((folder / "result.json").read_text())
                assert result["normal_restoration"] is not None
        finally:
            flag.unlink(missing_ok=True)
            native.close()
    print("smoke recorder cleanup: finish, setup error, lost pause reply and existing-pause ownership passed (fake IPC)")


async def self_test(native_path: Path, artifacts: Path) -> None:
    from unittest.mock import patch
    from test_grouped_composed import Native
    now = [1000.0]
    native = Native(native_path, artifacts / "first-boot")
    async def send(message):
        native.wire(message); return True
    host = SmokeHost(send)
    host.daemon.clock.read = lambda: (1790411700, -7 * 3600)
    async def advance(ms):
        native.command("advance", ms=ms); now[0] += ms / 1000
        async with host.daemon._state_lock:
            await host.daemon._expire_presentation_locked(now[0])
            if host.daemon._pending_present_due is not None and host.daemon._pending_present_due <= now[0]:
                await host.daemon._publish_pending_presentation_locked(now[0])
    async def route():
        messages, native.outbound = native.outbound, []
        for message in messages:
            if message.get("t") == "input": await host.daemon._handle_input(message)
    try:
        with patch("status349.daemon.time.monotonic", lambda: now[0]):
            native.wire(proto.hello())
            hello = next(m for m in native.outbound if m.get("t") == "hello")
            await host.connected(hello); await advance(100)
            assert native.status()["grouped"]["group"] == "home" and native.status()["count"] == 3
            await host.present_cards(); await advance(200)
            nid = native.status()["deck"]["focus_id"]
            native.command("press", x=600, y=35, ms=1)
            native.command("release", x=600, y=35, ms=1)
            await route(); await advance(100)
            assert nid not in host.ids() and native.status()["count"] == 2
            timer = await host.timeout_card(); await advance(301); await advance(10100)
            assert native.status()["grouped"]["group"] == "home" and timer in host.ids()
            retained = host.ids(); generation = host.daemon._grouped_generation
            await host.disconnected(); native.close()
            native = Native(native_path, artifacts / "second-boot")
            native.wire(proto.hello())
            hello = next(m for m in native.outbound if m.get("t") == "hello")
            await host.connected(hello); await advance(100)
            status = native.status()
            assert status["ids"] == retained and nid not in status["ids"]
            assert status["grouped"]["group"] == "home" and status["grouped"]["generation"] == 0
            assert host.daemon._grouped_generation == generation
            assert not any(m.get("t") == "present" for m in native.sent)
            print("smoke recorder: production host/parser/LVGL seed, ×, timer/retention and fresh-boot recovery passed")
    finally:
        native.close()
    await cleanup_self_test(native_path, artifacts / "cleanup")
    await history_self_test(native_path, artifacts / "history")


async def history_self_test(native_path: Path, artifacts: Path) -> None:
    """Check the physical history fixture through real parser/UI round trips."""
    from unittest.mock import patch
    from test_grouped_composed import Native
    now = [1000.0]
    native = Native(native_path, artifacts)
    native.clock_hook = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    async def send(message):
        native.wire(message); return True
    try:
        with patch("status349.daemon.time.monotonic", lambda: now[0]):
            host = SmokeHost(send, history=True)
            native.wire(proto.hello())
            hello = next(m for m in native.outbound if m.get("t") == "hello")
            await host.connected(hello)
            native.command("advance", ms=200)
            status = native.status()
            assert status["count"] == 3 and status["grouped"]["history"]
            assert status["grouped"]["group"] == "notifications"
            body = next(m for m in native.sent if m.get("t") == "sync_cards")["notifs"][0]["body"]
            assert 159 < len(body.encode()) <= 511
            removed = status["deck"]["focus_id"]
            native.command("press", x=600, y=22, ms=1)
            native.command("release", x=600, y=22, ms=1)
            messages, native.outbound = native.outbound, []
            for message in messages:
                if message.get("t") == "input":
                    await host.daemon._handle_input(message)
            native.command("advance", ms=200)
            assert removed not in host.ids() and native.status()["count"] == 2
            for nid in tuple(host.ids()):
                await host.daemon._device_close(nid)
            native.command("advance", ms=200)
            status = native.status()
            assert status["count"] == 0 and status["grouped"]["group"] == "notifications"
            print("history smoke recorder: long-lived seed, × round trip and empty pane passed")
    finally:
        native.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--expected-build-sha")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--native", type=Path)
    parser.add_argument("--history", action="store_true",
                        help="physical check of the notification-only pane with long-lived cards")
    args = parser.parse_args()
    if args.self_test:
        if args.native is None: parser.error("--self-test requires --native")
        asyncio.run(self_test(args.native, args.artifacts))
    else:
        if not args.port or not args.expected_build_sha:
            parser.error("Physical mode requires --port and --expected-build-sha")
        asyncio.run(run(args))
