#!/usr/bin/env python3
"""Opt-in live DMS proof with owned synthetic desktop notifications.

No application focus is asserted. Successful invocation must produce the real
desktop server's default-action callback, including when default is not first.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host" / "src"))
from status349.actions import BridgeClient, validate_status
from dbus_next import Message

spec = importlib.util.spec_from_file_location("controlled_notification",
    ROOT / "integrations/dms/349NotificationActions/tools/controlled_notification.py")
producer_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer_module)


async def proof(artifacts: Path):
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "result.json").unlink(missing_ok=True)
    events, calls = [], []
    owned, active = set(), set()
    result = None
    producer = producer_module.ControlledNotification("349-status-controlled-proof", None)

    def record(event):
        event = {"time": time.time(), **event}
        if event["event"] == "Notify":
            owned.add(event["assigned_id"])
            active.add(event["assigned_id"])
        elif event.get("id") not in owned:
            return
        if event["event"] == "NotificationClosed":
            active.discard(event["id"])
        events.append(event)
    producer.record = record
    bridge = BridgeClient()
    session, boot = secrets.randbelow(0x7FFFFFFF) + 1, 0xFFFFFFFF

    async def call(method, payload=None):
        response = await bridge.call(method, payload)
        calls.append({"method": method, "request": payload, "response": response})
        return response

    async def bounded(operation):
        return await asyncio.wait_for(operation, 2)

    async def status():
        result = await call("status")
        assert validate_status(result), result
        return result

    def action_count():
        return sum(e["event"] == "ActionInvoked" for e in events)

    async def settle(predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            assert time.monotonic() < deadline, "Desktop callback did not arrive"
            await asyncio.sleep(.01)

    async def bind(nid, revision, source_version):
        payload = {"v": 1, "session": session, "boot_id": boot, "id": nid,
            "rev": revision, "source_version": source_version, "desktop_id": producer.current_id,
            "expected": {"app": producer.app_name, "summary": producer.summary,
                         "body": producer.body, "default_label": "Open controlled proof"}}
        return await call("bind", payload)

    def activation(binding, request):
        return {"v": 1, "epoch": binding["epoch"], "session": session, "boot_id": boot,
                "id": binding["id"], "rev": binding["rev"], "token": binding["token"],
                "request": request}

    async def release(binding, final):
        return await call("release", {"v": 1, "session": session, "boot_id": boot,
            "id": binding["id"], "rev": binding["rev"], "token": binding["token"], "final": final})

    try:
        await asyncio.wait_for(producer.start(), 2)
        owner = await asyncio.wait_for(producer.bus.call(Message(destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus", interface="org.freedesktop.DBus", member="GetNameOwner",
            signature="s", body=["org.freedesktop.Notifications"])), 2)
        pid = await asyncio.wait_for(producer.bus.call(Message(destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
            member="GetConnectionUnixProcessID", signature="s", body=[owner.body[0]])), 2)
        initial = await status()
        assert initial["pid"] == pid.body[0], "Bridge is not in the notification owner's process"
        await bounded(producer.notify())
        first = await bind(1, 1, 1)
        assert first["status"] == "ready", first
        assert (await release(first, False))["status"] == "revoked"
        assert not (await status())["bindings"][0]["live"]
        rebound = await bind(1, 2, 1)
        assert rebound["status"] == "ready", rebound

        # Identical replacement is fenced by the monitor's explicit new version.
        await bounded(producer.notify(identical=True))
        identical = await bind(1, 3, 2)
        assert identical["status"] == "ready", identical
        assert (await call("activate", activation(rebound, 1)))["status"] == "stale"
        assert action_count() == 0

        # A visible replacement invalidates the captured default object binding.
        await bounded(producer.notify())
        assert (await call("activate", activation(identical, 2)))["status"] == "stale"
        latest = await bind(1, 4, 3)
        assert latest["status"] == "ready", latest
        request = activation(latest, 3)
        first_desktop_id = producer.current_id
        assert (await call("activate", request))["status"] == "dispatched"
        await settle(lambda: action_count() == 1)
        assert [e["action"] for e in events if e["event"] == "ActionInvoked"] == ["default"]
        assert [e["id"] for e in events if e["event"] == "ActionInvoked"] == [first_desktop_id]
        assert (await call("activate", request))["status"] == "dispatched"
        assert action_count() == 1, "Duplicate request invoked twice"
        await settle(lambda: producer.current_id not in active)
        assert not next(b for b in (await status())["bindings"] if b["id"] == 1)["live"]
        assert (await call("activate", activation(latest, 4)))["status"] == "stale"
        assert (await release(latest, True))["status"] == "released"
        assert (await bind(1, 5, 4))["status"] == "stale"

        producer.current_id = 0
        producer.actions = ["reply", "Reply only"]
        await bounded(producer.notify())
        assert (await bind(2, 1, 4))["status"] == "unavailable"
        await bounded(producer.close())
        producer.actions = ["reply", "Reply (not default)", "default", "Open controlled proof"]
        await bounded(producer.notify())
        closed = await bind(3, 1, 5)
        assert closed["status"] == "ready", closed
        await bounded(producer.close())
        assert (await call("activate", activation(closed, 5)))["status"] == "stale"
        assert action_count() == 1

        # Scoped reload must establish a new epoch and reject every old token.
        process = await asyncio.create_subprocess_exec("dms", "ipc", "call", "plugins",
            "reload", "status349NotificationActions", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 2)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        assert process.returncode == 0 and b"RELOAD_TRIGGERED" in output
        deadline = time.monotonic() + 3
        while True:
            current = await status()
            if current["epoch"] != initial["epoch"]:
                break
            assert time.monotonic() < deadline, "Provider epoch did not change"
            await asyncio.sleep(.05)
        assert (await call("activate", activation(closed, 6)))["status"] == "stale"
        assert action_count() == 1
        await bounded(producer.notify())
        fresh = await bind(4, 1, 6)
        assert fresh["status"] == "ready", fresh
        fresh_desktop_id = producer.current_id
        assert (await call("activate", activation(fresh, 1)))["status"] == "dispatched"
        await settle(lambda: action_count() == 2)
        await settle(lambda: producer.current_id not in active)
        assert all(e["action"] == "default" for e in events if e["event"] == "ActionInvoked")
        assert [e["id"] for e in events if e["event"] == "ActionInvoked"] == [first_desktop_id, fresh_desktop_id]
        result = {"passed": True, "notification_owner_pid": pid.body[0],
            "initial_epoch": initial["epoch"], "final_epoch": current["epoch"],
            "default_callbacks": action_count(), "owned_notifications": len(owned),
            "cases": ["default-not-first", "revocation-rebind", "identical-replacement-version",
                "visible-replacement", "single-dispatch", "duplicate", "synchronous-close",
                "released-id-fence", "no-default", "explicit-close", "reload-old-token", "fresh-epoch"]}
    finally:
        cleanup_errors = []
        if producer.notifications is not None:
            for desktop_id in tuple(active):
                try:
                    await bounded(producer.notifications.call_close_notification(desktop_id))
                except Exception as exc:
                    cleanup_errors.append(type(exc).__name__)
            producer.current_id = 0
            try:
                await settle(lambda: not active)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        if producer.bus is not None:
            producer.bus.disconnect()
        (artifacts / "desktop-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        (artifacts / "bridge-calls.jsonl").write_text("".join(json.dumps(e) + "\n" for e in calls))
        if result is not None:
            result["cleanup"] = {"remaining_active": sorted(active), "errors": cleanup_errors}
            result["passed"] = not active and not cleanup_errors
            (artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        assert not active and not cleanup_errors, "Owned desktop fixture cleanup failed"
    assert result is not None
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="send owned test notifications and reload this plugin")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this proof sends desktop notifications")
    asyncio.run(proof(args.artifacts.resolve()))
