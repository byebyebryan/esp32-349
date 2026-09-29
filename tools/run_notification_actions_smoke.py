#!/usr/bin/env python3
"""Opt-in physical Open/× smoke with persistent, owned desktop notifications.

No configuration changes, simulated touch or automatic action invocation.
Records callbacks and device readbacks; human observations accept the UI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host/src'))
from dbus_next import BusType, Message, MessageType
from dbus_next.aio import MessageBus
from status349.__main__ import ipc_request
from status349.actions import BridgeClient, validate_status

APP = '349-Snap-Acceptance'
NAME = 'org.freedesktop.Notifications'
INTERFACE = 'org.freedesktop.Notifications'
FIXTURES = (
    ('READY A — we’ve 東京 → ✓', ['default', 'Open test card']),
    ('DISABLED B — no default action', []),
    ('READY C — tap Open', ['reply', 'Reply (not default)', 'default', 'Open test card']),
)


class Recorder:
    def __init__(self, artifacts: Path):
        self.artifacts = artifacts
        self.owned: set[int] = set()
        self.active: set[int] = set()
        self.events: list[dict] = []
        self.owner = ''

    def record(self, event: dict) -> None:
        event = {'time': time.time(), **event}
        self.events.append(event)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        with (self.artifacts / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def signal(self, message: Message) -> bool:
        if (message.message_type != MessageType.SIGNAL or message.interface != INTERFACE or
                message.sender != self.owner or len(message.body) < 2 or
                message.body[0] not in self.owned):
            return False
        if message.member in ('ActionInvoked', 'NotificationClosed'):
            if message.member == 'NotificationClosed':
                self.active.discard(message.body[0])
            self.record({'event': message.member, 'id': message.body[0], 'value': message.body[1]})
        return False

    async def snapshot(self, reason: str) -> None:
        status = await asyncio.to_thread(ipc_request, {'cmd': 'status'})
        cards = await asyncio.to_thread(ipc_request, {'cmd': 'device_cards'})
        self.record({'event': 'readback', 'reason': reason, 'status': status, 'device_cards': cards})

    async def close_owned(self, notifications, signal_timeout: float = 2) -> list[dict]:
        errors = []
        for nid in sorted(self.active):
            try:
                await asyncio.wait_for(notifications.call_close_notification(nid), 2)
            except Exception as error:
                failure = {'event': 'cleanup-error', 'id': nid, 'type': type(error).__name__}
                errors.append(failure)
                self.record(failure)
        deadline = time.monotonic() + signal_timeout
        while self.active and time.monotonic() < deadline:
            await asyncio.sleep(.02)
        if self.active:
            failure = {'event': 'cleanup-incomplete', 'remaining_active': sorted(self.active)}
            errors.append(failure)
            self.record(failure)
        return errors

    async def finish(self, notifications) -> dict:
        cleanup_errors = await self.close_owned(notifications)
        try:
            await self.snapshot('cleanup')
        except Exception as error:
            failure = {'event': 'cleanup-readback-error', 'type': type(error).__name__}
            cleanup_errors.append(failure)
            self.record(failure)
        result = {'owned_notifications': sorted(self.owned), 'remaining_active': sorted(self.active),
                  'default_callbacks': sum(e.get('event') == 'ActionInvoked' and e.get('value') == 'default'
                                           for e in self.events),
                  'cleanup_errors': cleanup_errors,
                  'human_observation': 'pending; record separately',
                  'raw_input_envelopes': 'not exposed by existing API'}
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        self.record({'event': 'finished', **result})
        return result

    async def run(self) -> None:
        status = await asyncio.to_thread(ipc_request, {'cmd': 'status'})
        assert status.get('ok') and status.get('link') and not status.get('paused'), status
        assert status.get('notification_actions', {}).get('negotiated'), status
        provider = await BridgeClient().call('status')
        assert validate_status(provider), provider
        self.record({'event': 'provider', 'pid': provider['pid'], 'epoch': provider['epoch']})
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        try:
            owner = await bus.call(Message(destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                interface='org.freedesktop.DBus', member='GetNameOwner', signature='s', body=[NAME]))
            assert owner.message_type != MessageType.ERROR
            self.owner = owner.body[0]
            pid = await bus.call(Message(destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                interface='org.freedesktop.DBus', member='GetConnectionUnixProcessID', signature='s',
                body=[self.owner]))
            assert pid.message_type != MessageType.ERROR and pid.body == [provider['pid']], pid.body
            match = await bus.call(Message(destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                interface='org.freedesktop.DBus', member='AddMatch', signature='s',
                body=["type='signal',sender='" + self.owner + "',interface='" + INTERFACE + "'"]))
            assert match.message_type != MessageType.ERROR
            bus.add_message_handler(self.signal)
            # Pin all fixture calls to this unique owner. A replacement notification
            # server may reuse numeric IDs; cleanup must never follow NAME to it.
            introspection = await bus.introspect(self.owner, '/org/freedesktop/Notifications')
            notifications = bus.get_proxy_object(self.owner, '/org/freedesktop/Notifications', introspection).get_interface(INTERFACE)

            async def cards() -> None:
                errors = await self.close_owned(notifications)
                if errors:
                    raise RuntimeError('Owned fixture cleanup did not complete; see trace')
                for summary, actions in FIXTURES:
                    nid = await asyncio.wait_for(notifications.call_notify(APP, 0, '', summary,
                        "We've / we’ve 東京 が → ✓. Body taps stay inert; swipe to browse.", actions, {}, 0), 2)
                    self.owned.add(nid); self.active.add(nid)
                    self.record({'event': 'Notify', 'id': nid, 'summary': summary, 'expire_ms': 0,
                                 'has_default': 'default' in actions[::2]})
                    await asyncio.sleep(.5)
                await asyncio.sleep(1)
                await self.snapshot('cards-ready')

            try:
                await cards()
                self.record({'event': 'ready', 'commands': ['cards', 'status', 'finish'],
                             'deadline_minutes': 15, 'raw_input_envelopes': 'not exposed by existing API'})
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    # Poll stdin without a blocked reader thread surviving cancellation.
                    import select
                    if select.select([sys.stdin], [], [], 0)[0]:
                        line = sys.stdin.readline()
                        if not line or line.strip() in ('finish', 'quit'):
                            break
                        if line.strip() == 'cards':
                            await cards()
                        elif line.strip() == 'status':
                            await self.snapshot('operator')
                    await asyncio.sleep(.1)
            finally:
                result = await self.finish(notifications)
                if result['cleanup_errors']:
                    raise RuntimeError('Cleanup/readback incomplete; see recorded result')
        finally:
            bus.disconnect()


def self_test(artifacts: Path) -> None:
    recorder = Recorder(artifacts)
    recorder.owner = ':1.test'
    recorder.owned = {7}; recorder.active = {7}
    def event(sender, member, nid, value):
        return Message(message_type=MessageType.SIGNAL, sender=sender, path='/org/freedesktop/Notifications',
                       interface=INTERFACE, member=member, signature='us' if member == 'ActionInvoked' else 'uu',
                       body=[nid, value])
    recorder.signal(event(':1.other', 'ActionInvoked', 7, 'default'))
    recorder.signal(event(':1.test', 'ActionInvoked', 99, 'default'))
    assert not recorder.events
    recorder.signal(event(':1.test', 'ActionInvoked', 7, 'default'))
    assert len(recorder.events) == 1 and recorder.active == {7}
    recorder.signal(event(':1.test', 'NotificationClosed', 7, 2))
    assert len(recorder.events) == 2 and not recorder.active
    async def cleanup_test():
        failed = Recorder(artifacts / 'cleanup-failure')
        failed.owned = {1, 2}; failed.active = {1, 2}
        attempted = []
        class Notifications:
            async def call_close_notification(self, nid):
                attempted.append(nid)
                if nid == 1:
                    raise OSError('controlled failure')
                failed.active.discard(nid)
        errors = await failed.close_owned(Notifications(), signal_timeout=0)
        assert attempted == [1, 2] and failed.active == {1}
        assert {e['event'] for e in errors} == {'cleanup-error', 'cleanup-incomplete'}

        complete = Recorder(artifacts / 'readback-failure')
        async def unavailable_snapshot(reason):
            raise ConnectionError('controlled readback failure')
        complete.snapshot = unavailable_snapshot
        await complete.finish(Notifications())
        result = json.loads((complete.artifacts / 'result.json').read_text())
        assert result['remaining_active'] == []
        assert result['cleanup_errors'] == [{'event': 'cleanup-readback-error', 'type': 'ConnectionError'}]
    asyncio.run(cleanup_test())
    print('owned callback filtering and cleanup failure handling: passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    if args.self_test:
        self_test(args.artifacts)
    elif args.live:
        asyncio.run(Recorder(args.artifacts).run())
    else:
        parser.error('choose --live explicitly or --self-test')
