"""Optional read-only lifecycle evidence for outstanding rich notification cards."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
import os
import secrets
import signal

from .rich_protocol import event_id, validate


@dataclass(frozen=True)
class RichCard:
    metadata: dict
    version: int
    receipt: float


async def query(source, cards):
    """Fixed configured argv; notification content cannot select executable/host."""
    nonce = secrets.token_hex(16)
    request = json.dumps({'v': 1, 'nonce': nonce, 'cards': cards}, separators=(',', ':')).encode()
    process = None
    try:
        async with asyncio.timeout(8):
            process = await asyncio.create_subprocess_exec(*source['command'], stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            process.stdin.write(request)
            await process.stdin.drain()
            process.stdin.close()
            raw = bytearray()
            while chunk := await process.stdout.read(8193 - len(raw)):
                raw.extend(chunk)
                if len(raw) > 8192:
                    return {}
            if await process.wait() != 0:
                return {}
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {'v', 'nonce', 'host', 'instance', 'results'}
                    or type(value['v']) is not int or value['v'] != 1 or value['nonce'] != nonce
                    or (value['host'], value['instance']) != (source['host'], source['instance'])
                    or not isinstance(value['results'], dict)):
                return {}
            allowed = {event_id(card) for card in cards}
            return {key: relation for key, relation in value['results'].items()
                    if key in allowed and relation in ('current', 'superseded', 'unknown')}
    except (OSError, TimeoutError, ValueError, TypeError):
        return {}
    finally:
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


class RichNotificationsMixin:
    def _init_rich(self):
        self._rich_cards = {}
        # RAM-only replay protection, at most 256 episodes for their original TTL.
        self._rich_seen = OrderedDict()
        self._rich_checks = 0
        self._rich_superseded = 0

    def _admit_rich_locked(self, message, receipt, now):
        local_id = int(message['id'])
        self._rich_snapshot_locked()
        for key, expiry in tuple(self._rich_seen.items()):
            if expiry <= now:
                self._rich_seen.pop(key, None)
        rich, version = message.get('_rich'), message.get('_source_version')
        try:
            rich = validate(rich)
            if type(version) is not int or version <= 0:
                raise ValueError('missing source version')
        except (ValueError, TypeError, UnicodeError):
            self._rich_cards.pop(local_id, None)
            return True  # Ordinary generic notification fallback.
        key = event_id(rich)
        if key in self._rich_seen:
            # A replay is not a new arrival: no retention, focus or boost renewal.
            self.notifications.forget(local_id, expected_version=version)
            self._cancel_unsent_action(local_id)
            return False
        self._rich_seen[key] = receipt + self.cfg.notifications.retention_s
        while len(self._rich_seen) > 256:
            self._rich_seen.popitem(last=False)
        self._rich_cards[local_id] = RichCard(dict(rich), version, receipt)
        return True

    def _rich_snapshot_locked(self):
        self._rich_cards = {nid: card for nid, card in self._rich_cards.items()
                            if nid in self.model.retained_notifs
                            and self.model.retained_received_mono.get(nid) == card.receipt}
        return dict(self._rich_cards)

    async def _retire_rich(self, local_id, card, source):
        async with self._state_lock:
            current = self._rich_snapshot_locked().get(local_id)
            if current != card or source not in self.cfg.notifications.rich_sources:
                return
            # Do not forget a newer Notify replacement already waiting in the outbox.
            self.notifications.forget(local_id, expected_version=card.version)
            self._rich_cards.pop(local_id, None)
            await self._device_close_locked(local_id)
            self._rich_superseded += 1

    async def _check_rich(self):
        async with self._state_lock:
            snapshot = self._rich_snapshot_locked()
            sources = [dict(source, command=list(source['command'])) for source in self.cfg.notifications.rich_sources]
        for source in sources:
            selected = {nid: card for nid, card in snapshot.items()
                        if (card.metadata['host'], card.metadata['instance']) == (source['host'], source['instance'])}
            if not selected:
                continue
            results = await query(source, [card.metadata for card in selected.values()])
            self._rich_checks += 1
            for nid, card in selected.items():
                if results.get(event_id(card.metadata)) == 'superseded':
                    await self._retire_rich(nid, card, source)

    async def _rich_loop(self):
        # Daemon.run owns cancellation, as it does for its other long-lived workers.
        while True:
            await self._check_rich()
            await asyncio.sleep(5)
