#!/usr/bin/env python3
"""Native private-bus proof for the real 349 notification source/action manager.

Run as --client-test in the SwayNC fork's tests/notification-actions.py harness.
That harness supplies a disposable Niri compositor and notification daemon.
"""

import asyncio
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus

from status349.actions import NotificationActionManager
from status349.config import NotificationsConfig
from status349.sources.notifications import NotificationSource
from status349.swaync_actions import ConfiguredBridgeClient


async def main(*, kitty_focus=False):
    if os.environ.get("SWAYNC_PRIVATE_ACTION_TEST") != "1":
        raise SystemExit("Run this proof through the private SwayNC test harness")
    retained = {}
    expired = set()
    states = {}

    async def on_notify(card):
        retained[card["id"]] = card

    async def on_close(nid):
        retained.pop(nid, None)

    async def on_expire(nid):
        expired.add(nid)

    async def changed(nid, state):
        states[nid] = state

    async def wait_for(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.02)

    source = NotificationSource(NotificationsConfig(popup_timeout_ms=1000), on_notify, on_close,
                                on_active_expire=on_expire, grouped_mode=lambda: True)
    manager = NotificationActionManager(source, ConfiguredBridgeClient(source, lambda: "swaync"), enabled=lambda: True,
                                        context=lambda: (5, 9), retained_ids=lambda: list(retained), on_change=changed)
    bus = await MessageBus().connect()
    signals = []
    bus.add_message_handler(lambda m: signals.append(m) if m.message_type == MessageType.SIGNAL else None)
    await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                           interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                           body=["type='signal',interface='org.freedesktop.Notifications'"]))

    async def notify(title):
        reply = await bus.call(Message(destination="org.freedesktop.Notifications", path="/org/freedesktop/Notifications",
                                       interface="org.freedesktop.Notifications", member="Notify",
                                       signature="susssasa{sv}i", body=["349-native-proof", 0, "", title, "readable body",
                                       ["default", "Open"], {"resident": Variant("b", True)}, 100]))
        assert reply.message_type == MessageType.METHOD_RETURN
        return reply.body[0]

    try:
        await source.start()
        await wait_for(lambda: source.identity_status()["state"] == "ready")
        older_desktop = await notify("older retained")
        await wait_for(lambda: len(retained) == 1)
        older = next(iter(retained))
        await wait_for(lambda: source.action_candidate(older) is not None)
        await wait_for(lambda: older in expired)
        newer_desktop = await notify("newer retained")
        await wait_for(lambda: len(retained) == 2)
        newer = next(nid for nid in retained if nid != older)
        await wait_for(lambda: source.action_candidate(newer) is not None)
        await manager.reconcile_once()
        revision = manager.open_for(older)["rev"]
        assert manager.open_for(older)["state"] == "ready"
        assert await manager.activate(older, revision, 1, 5, 9) == "dispatched"
        await wait_for(lambda: any(m.member == "ActionInvoked" for m in signals))
        assert await manager.activate(older, revision, 1, 5, 9) == "dispatched"
        await asyncio.sleep(0.1)
        actions = [m.body for m in signals if m.member == "ActionInvoked"]
        assert actions == [[older_desktop, "default"]], actions
        assert newer_desktop != older_desktop
        assert manager.open_for(newer)["state"] == "ready"
        assert len(retained) == 2
        print(json.dumps({"result": "passed", "proof": "real 349 source and action manager to real SwayNC",
                          "expired_older_card": True, "exact_original_action": True,
                          "newer_card_preserved": True, "duplicate_not_reinvoked": True}))
        if kitty_focus:
            await prove_kitty_focus(source, manager, notify, signals, expired, wait_for)
    finally:
        await source.stop()
        bus.disconnect()


async def prove_kitty_focus(source, manager, notify, signals, expired, wait_for):
    """Exercise native OSC 9 and actual window focus without desktop input."""
    if not os.environ.get("NIRI_SOCKET"):
        raise RuntimeError("the private Niri compositor socket is required")
    processes = []
    logs = []
    classes = {name: f"349-private-{name}-{os.getpid()}" for name in ("source", "other")}
    marker = f"PRIVATE_KITTY_HISTORY_{os.getpid()}"
    env = {**os.environ, "XDG_SESSION_TYPE": "wayland"}

    def windows():
        return json.loads(subprocess.check_output(["niri", "msg", "-j", "windows"],
                                                 env=env, text=True, timeout=2))

    def focused(name):
        return any(w["app_id"] == classes[name] and w["is_focused"] for w in windows())

    def candidate_id():
        for nid, candidate in source.action_candidates().items():
            if any(marker in value for value in candidate["expected"].values()):
                return nid
        return None

    with tempfile.TemporaryDirectory(prefix="349-kitty-proof-") as directory:
        root = Path(directory)
        terminal = root / "terminal.py"
        terminal.write_text(
            "import sys,time\nfrom pathlib import Path\np=Path(sys.argv[1])\n"
            "while not p.exists():time.sleep(.03)\n"
            f"sys.stdout.write({chr(27) + ']9;' + marker + chr(7)!r});sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        try:
            for name in ("source", "other"):
                log = (root / f"{name}.log").open("w+")
                logs.append(log)
                command = ["kitty", "--debug-rendering", "--config", "NONE", "-o", "linux_display_server=wayland",
                           "--class", classes[name], "--title", f"349 private {name}"]
                command += (["python3", str(terminal), str(root / "trigger")] if name == "source"
                            else ["python3", "-c", "import time;time.sleep(30)"])
                processes.append(subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT))
                await wait_for(lambda: any(w["app_id"] == classes[name] for w in windows()))
            await wait_for(lambda: focused("other"))
            (root / "trigger").touch()
            await wait_for(lambda: candidate_id() is not None)
            local_id = candidate_id()
            desktop_id = source.action_candidate(local_id)["desktop_id"]
            await wait_for(lambda: local_id in expired)
            # Keep a newer popup present while the chosen Kitty card is historical.
            newer_desktop = await notify("newer than Kitty")
            await wait_for(lambda: any(c["desktop_id"] == newer_desktop for c in source.action_candidates().values()))
            newer_local = next(nid for nid, c in source.action_candidates().items() if c["desktop_id"] == newer_desktop)
            await manager.reconcile_once()
            assert focused("other")
            revision = manager.open_for(local_id)["rev"]
            assert await manager.activate(local_id, revision, 2, 5, 9) == "dispatched"
            await wait_for(lambda: focused("source"))
            # Only the originating Kitty connection receives its token/action.
            # This unrelated test client must not see a broadcast copy.
            assert not any(m.member in {"ActionInvoked", "ActivationToken"}
                           and m.body[0] == desktop_id for m in signals)
            assert manager.open_for(newer_local)["state"] == "ready"
            logs[0].flush(); logs[0].seek(0)
            trace = logs[0].read()
            assert "Focusing window:" in trace
            assert f"dbus_notification_id={desktop_id}" in trace
            print(json.dumps({"result": "passed", "proof": "real Kitty OSC 9 through the real 349 adapter",
                              "popup_expired": True, "newer_card_preserved": True,
                              "originating_window_focused": True, "fresh_desktop_click": False}))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)
            for log in logs:
                log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kitty-focus", action="store_true", help="also verify native Kitty focus inside private Niri")
    args = parser.parse_args()
    asyncio.run(main(kitty_focus=args.kitty_focus))
