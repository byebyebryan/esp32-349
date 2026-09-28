#!/usr/bin/env python3
"""Controlled D-Bus notification source for the 349 action-provider proof.

Run this only for an explicitly scheduled integration proof. It owns no daemon
name; it sends test notifications to the currently running desktop notification
server and records ActionInvoked/NotificationClosed signals.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from dbus_next import BusType, Message, MessageType
from dbus_next.aio import MessageBus


SERVICE = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
INTERFACE = "org.freedesktop.Notifications"


class ControlledNotification:
    def __init__(self, app_name: str, log_file: Path | None):
        self.app_name = app_name
        self.log_file = log_file
        self.bus: MessageBus | None = None
        self.notifications = None
        self.current_id = 0
        self.generation = 0
        self.summary = "349 controlled action proof"
        self.body = "Use the default action to record ActionInvoked."
        self.icon = ""
        # Keeping a non-default action first proves the provider selects the
        # identifier `default`, rather than whichever action happens to be first.
        self.actions = ["reply", "Reply (not default)", "default", "Open controlled proof"]

    def record(self, event: dict) -> None:
        event = {"time": time.time(), **event}
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        print(line, flush=True)
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()

    def on_message(self, message: Message) -> bool:
        if message.message_type is not MessageType.SIGNAL or message.interface != INTERFACE:
            return False
        if message.member == "ActionInvoked" and len(message.body) >= 2:
            notification_id, action_key = message.body[:2]
            self.record({
                "event": "ActionInvoked",
                "id": notification_id,
                "action": action_key,
                "producer_generation": self.generation,
                "sender": message.sender,
            })
            return False
        if message.member == "NotificationClosed" and len(message.body) >= 2:
            notification_id, reason = message.body[:2]
            self.record({
                "event": "NotificationClosed",
                "id": notification_id,
                "reason": reason,
                "producer_generation": self.generation,
                "sender": message.sender,
            })
            return False
        return False

    async def start(self) -> None:
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        match = "type='signal',sender='org.freedesktop.Notifications',interface='org.freedesktop.Notifications'"
        reply = await self.bus.call(Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[match],
        ))
        if reply.message_type is MessageType.ERROR:
            raise RuntimeError(reply.body[0] if reply.body else "AddMatch failed")
        self.bus.add_message_handler(self.on_message)
        introspection = await self.bus.introspect(SERVICE, OBJECT_PATH)
        proxy = self.bus.get_proxy_object(SERVICE, OBJECT_PATH, introspection)
        self.notifications = proxy.get_interface(INTERFACE)

    async def notify(self, identical: bool = False) -> None:
        self.generation += 1
        if not identical:
            self.summary = f"349 controlled action proof {self.generation}"
            self.body = f"Replace generation {self.generation}; tap only after the bridge reports this live revision."
        replaces_id = self.current_id
        self.current_id = await self.notifications.call_notify(
            self.app_name,
            replaces_id,
            self.icon,
            self.summary,
            self.body,
            self.actions,
            {},
            0,
        )
        self.record({
            "event": "Notify",
            "assigned_id": self.current_id,
            "replaces_id": replaces_id,
            "generation": self.generation,
            "identical": identical,
            "summary": self.summary,
            "body": self.body,
        })

    async def close(self) -> None:
        if self.current_id:
            await self.notifications.call_close_notification(self.current_id)
            self.record({"event": "CloseNotification", "id": self.current_id})
            self.current_id = 0

    async def command_loop(self) -> None:
        self.record({
            "event": "ready",
            "assigned_id": self.current_id,
            "commands": ["replace", "replace-identical", "new", "close", "quit"],
        })
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            command = line.strip()
            if command == "replace":
                await self.notify()
            elif command == "replace-identical":
                await self.notify(identical=True)
            elif command == "new":
                self.current_id = 0
                await self.notify()
            elif command == "close":
                await self.close()
            elif command in {"quit", "exit"}:
                break
            elif command:
                self.record({"event": "error", "message": "unknown command", "command": command})

    async def run(self) -> None:
        await self.start()
        try:
            await self.notify()
            await self.command_loop()
        finally:
            try:
                await self.close()
            except Exception as error:  # best-effort cleanup for the test notification
                self.record({"event": "cleanup_error", "error": str(error)})
            if self.bus:
                self.bus.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", default="349-status-controlled-proof")
    parser.add_argument("--log-file", type=Path, help="append proof events as JSONL")
    args = parser.parse_args()
    try:
        asyncio.run(ControlledNotification(args.app_name, args.log_file).run())
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"controlled notification producer failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
