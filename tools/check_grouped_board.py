#!/usr/bin/env python3
"""Isolated serial acceptance. Run only while 349d is paused; resets the board.

Tests production firmware readback, not pixels, touch, or animation performance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host" / "src"))
from status349 import proto
from status349.ipc import pause_path
from status349.link import Link, open_port
from status349.state import StateModel


def run(port: str | None, artifacts: Path) -> None:
    if not pause_path().exists():
        raise RuntimeError("Pause 349d before opening the serial acceptance connection")
    artifacts.mkdir(parents=True, exist_ok=True)
    trace: list[dict] = []
    consoles: list[str] = []
    def persist_trace() -> None:
        (artifacts / "serial-trace.json").write_text(json.dumps(trace, indent=2) + "\n")
        (artifacts / "console.log").write_text("\n".join(consoles) + "\n")
    session = 1234567
    model = StateModel()
    model.set_zones([])
    offset = time.localtime().tm_gmtoff
    model.set_clock(int(time.time()), offset)
    model.set_dashboard({"cpu": .18, "mem": .43, "network": True})
    tx = 0
    with Link(open_port(port)) as link:
        def collect(duration: float) -> list[dict]:
            result = []
            for line in link.lines(duration):
                data, message = proto.classify(line)
                if message is not None:
                    result.append(message)
                    trace.append({"rx": message})
                elif not data:
                    consoles.append(line)
            persist_trace()
            return result

        def send(message: dict) -> None:
            trace.append({"tx": message})
            link.send(message)

        def snapshot() -> None:
            nonlocal tx
            tx += 1
            for message in proto.card_sync_messages(model.card_snapshot(32, retained=True), tx,
                    include_dashboard=True, grouped_session=session):
                send(message)

        def readback() -> dict:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                send({"t": "cards_query"})
                for message in collect(.16):
                    if message.get("t") == "cards_status":
                        status = proto.card_status(message)
                        assert status is not None, message
                        if message.get("view_pending"):
                            continue
                        return status
            raise AssertionError("No firmware cards_status response")

        def until(predicate, timeout: float = 2.0) -> dict:
            deadline = time.monotonic() + timeout
            latest = None
            while time.monotonic() < deadline:
                latest = readback()
                if predicate(latest):
                    return latest
            raise AssertionError(f"Readback condition failed: {latest}")

        boot_messages = collect(1.0)
        hellos = [m for m in boot_messages if m.get("t") == "hello"]
        deadline = time.monotonic() + 4.0
        while not hellos and time.monotonic() < deadline:
            send(proto.hello())
            hellos = [m for m in collect(.5) if m.get("t") == "hello"]
        assert hellos and proto.grouped_ui_capable(hellos[-1]), hellos
        hello = hellos[-1]

        for nid in range(1, 34):
            model.retain_notification(proto.notify(nid, "BOARD TEST", f"CARD {nid}",
                "We've / we’ve 中文 → ✓", 1, 0, int(time.time())))
        snapshot()
        initial = until(lambda s: s["count"] == 32 and s["grouped"]["group"] == "home")
        assert initial["ids"] == list(range(2, 34)) and initial["overflow"] == 0
        replacement = proto.notify(31, "BOARD TEST", "REPLACED 31", "Updated text", 1, 0, int(time.time()))
        model.retain_notification(replacement)
        send({**replacement, "session": session, "total": 32, "cached": True})
        replaced = until(lambda s: s["ids"][-1] == 31)
        assert replaced["count"] == 32 and replaced["ids"] == list(range(2, 31)) + [32, 33, 31]

        send(proto.present(session, 1, 33, 350, 1))
        until(lambda s: s["grouped"]["group"] == "notifications")
        send(proto.present(session, 1, 33, 9000, 1))
        expired = until(lambda s: s["grouped"]["group"] == "home", 1.2)
        assert expired["count"] == 32
        snapshot()
        assert until(lambda s: s["grouped"]["group"] == "home")["count"] == 32

        send(proto.present(session, 2, 32, -1, 2))
        until(lambda s: s["grouped"]["present_id"] == 32)
        send(proto.present(session, 1, 33, 0, 1))
        assert readback()["grouped"]["present_id"] == 32
        model.close_notification(32)
        send(proto.close(32, total=31, session=session))
        removed = until(lambda s: s["count"] == 31 and s["grouped"]["group"] == "home")
        assert 32 not in removed["ids"]

        # Staging failure must leave committed records intact and request recovery.
        malformed = proto.card_sync_messages(model.card_snapshot(32, retained=True), tx + 1,
                include_dashboard=True, grouped_session=session)[0]
        malformed["grouped"]["session"] = -1
        send(malformed)
        recovery = collect(.2)
        assert sum(m.get("t") == "resync" for m in recovery) == 1
        assert readback()["ids"] == removed["ids"]
        snapshot()
        send({**proto.notify(900, "STALE", "Wrong session", "", 1, 0, 0),
            "session": session + 1, "total": 32})
        assert 900 not in readback()["ids"]

        session += 1
        model = StateModel()
        model.set_zones([]); model.set_clock(int(time.time()), offset)
        model.set_dashboard({"cpu": .18, "mem": .43, "network": True})
        model.retain_notification(proto.notify(900, "NEW SESSION", "Session reset", "", 1, 0, 0))
        snapshot()
        reset = until(lambda s: s["grouped"]["session"] == session)
        assert reset["grouped"]["group"] == "home" and reset["grouped"]["generation"] == 0
        send(proto.close(900, total=0, session=session - 1))
        assert readback()["ids"] == [900]

        # Check the legacy capability path on the same candidate firmware.
        legacy = StateModel(); legacy.set_zones([])
        legacy.set_clock(int(time.time()), offset)
        send(legacy.snapshot())
        until(lambda s: not s["grouped"]["enabled"])
        model.close_notification(900)
        snapshot()
        final = until(lambda s: s["count"] == 0 and s["grouped"]["group"] == "home")
        # Allow one existing 10 s health log without introducing a soak gate.
        health_deadline = time.monotonic() + 12.0
        while not any("alive, host=" in line for line in consoles) and time.monotonic() < health_deadline:
            send({"t": "ping", "ts": int(time.time())})
            collect(1.0)
        health = [line for line in consoles if "alive, host=" in line]
        assert health, "No existing periodic firmware health sample"

    (artifacts / "serial-trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    (artifacts / "console.log").write_text("\n".join(consoles) + "\n")
    result = {"passed": True, "hello": hello, "initial": initial, "replaced": replaced, "expired": expired,
        "removed": removed, "reset": reset, "final": final, "health": health,
        "scope": "Serial cache/lifecycle/staging/readback; no visual or physical-touch claim"}
    (artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    run(args.port, args.artifacts)
