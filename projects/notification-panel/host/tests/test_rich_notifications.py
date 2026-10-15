import asyncio
import json
import sys

import pytest
from dbus_next import Variant

from status349 import daemon_rich, proto
from status349.config import default_config, validate_config
from status349.rich_protocol import decode, encode, event_id, metadata
from status349.sources.notifications import parse_notify_body
from test_notification_history_host import _history_daemon

THREAD = '11111111-1111-4111-8111-111111111111'
OLD = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
NEW = '00000000-0000-4000-8000-000000000000'
SOURCE = {'host': 'snap', 'instance': 'native', 'command': ['/usr/bin/false']}


def card(nid=1, turn=OLD, version=1, receipt=None):
    result = proto.notify(nid, 'Codex @ snap', 'Thread', 'body', 1, 0, 1)
    result['_rich'] = metadata('snap', 'native', THREAD, turn)
    result['_source_version'] = version
    if receipt is not None:
        result['_received_mono'] = receipt
    return result


def test_envelope_and_source_format_fallback():
    rich = metadata('snap', 'native', THREAD, OLD)
    envelope = encode(rich)
    assert decode(envelope) == rich
    raw = ['kitty', 0, '', 'A useful thread title', '**Done**', [], {'category': Variant('s', envelope)}, 0]
    parsed = parse_notify_body(raw)
    assert parsed['app'] == 'Codex @ snap'
    assert parsed['body'] == 'Done'
    assert parsed['body_runs'] == [{'start': 0, 'end': 4, 'style': 1}]
    raw[6]['category'] = Variant('s', envelope + '=')
    parsed = parse_notify_body(raw)
    assert '_rich' not in parsed and parsed['body'] == '**Done**'
    assert parsed['app'] == 'kitty'


def test_native_supersession_closes_only_local_card_after_popup_expiry(monkeypatch):
    async def scenario():
        daemon, writer = _history_daemon()
        daemon.cfg.notifications.rich_sources = [SOURCE]
        await daemon._device_notify(card())
        await daemon._device_expire(1)
        assert 1 in daemon.model.retained_notifs
        async def evidence(source, cards):
            assert source == SOURCE and cards == [card()['_rich']]
            return {event_id(cards[0]): 'superseded'}
        monkeypatch.setattr(daemon_rich, 'query', evidence)
        async def must_not_close_desktop(*args):
            pytest.fail('automatic supersession must stay local')
        daemon.notifications.dismiss = must_not_close_desktop
        await daemon._check_rich()
        assert 1 not in daemon.model.retained_notifs
        assert daemon._rich_superseded == 1
        assert any(f['t'] == 'close' and f['id'] == 1 for f in writer.frames)
    asyncio.run(scenario())


@pytest.mark.parametrize('relation', ['current', 'unknown', None])
def test_uncertain_evidence_does_not_renew_or_remove_card(monkeypatch, relation):
    async def scenario():
        daemon, writer = _history_daemon()
        daemon.cfg.notifications.rich_sources = [SOURCE]
        await daemon._device_notify(card())
        receipt = daemon.model.retained_received_mono[1]
        rev = daemon.model.retained_history_rev[1]
        before = len(writer.frames)
        async def evidence(source, cards):
            return {event_id(cards[0]): relation} if relation else {}
        monkeypatch.setattr(daemon_rich, 'query', evidence)
        await daemon._check_rich()
        assert daemon.model.retained_received_mono[1] == receipt
        assert daemon.model.retained_history_rev[1] == rev
        assert len(writer.frames) == before
    asyncio.run(scenario())


def test_no_queries_without_matching_outstanding_cards(monkeypatch):
    async def scenario():
        daemon, _ = _history_daemon()
        daemon.cfg.notifications.rich_sources = [dict(SOURCE, host='other')]
        await daemon._device_notify(card())
        async def forbidden(*args): pytest.fail('unexpected query')
        monkeypatch.setattr(daemon_rich, 'query', forbidden)
        await daemon._check_rich()
        await daemon._device_close(1)
        daemon.cfg.notifications.rich_sources = [SOURCE]
        await daemon._check_rich()
        assert not daemon._rich_cards
    asyncio.run(scenario())


def test_stale_reply_does_not_remove_replacement_or_newer_source_queue():
    async def scenario():
        daemon, _ = _history_daemon()
        daemon.cfg.notifications.rich_sources = [SOURCE]
        await daemon._device_notify(card())
        old = daemon._rich_cards[1]
        queued = card(turn=NEW, version=2)
        # A source replacement arrived while the lifecycle query was in flight.
        daemon.notifications._open_info[1] = {'version': 2}
        daemon.notifications._outbox[1] = queued
        await daemon._retire_rich(1, old, SOURCE)
        assert daemon.notifications._outbox[1] is queued
        assert not daemon.notifications.is_forgotten(1)
        await daemon._device_notify(queued)
        assert daemon._rich_cards[1].metadata['turn'] == NEW
        await daemon._retire_rich(1, old, SOURCE)
        assert 1 in daemon.model.retained_notifs
    asyncio.run(scenario())


def test_replayed_episode_does_not_renew_attention_or_resurrect(monkeypatch):
    async def scenario():
        now = [100.0]
        monkeypatch.setattr('status349.daemon.time.monotonic', lambda: now[0])
        daemon, writer = _history_daemon()
        await daemon._device_notify(card(receipt=100))
        before = len(writer.frames)
        now[0] = 101.0
        await daemon._device_notify(card(version=2, receipt=101))
        assert daemon.model.retained_received_mono[1] == 100
        assert len(writer.frames) == before
        await daemon._device_close(1)
        await daemon._device_notify(card(nid=2, version=3, receipt=101))
        assert not daemon.model.retained_notifs
    asyncio.run(scenario())


def test_sources_require_fixed_bounded_argv_and_unique_identity():
    cfg = default_config()
    cfg.notifications.rich_sources = [SOURCE]
    validate_config(cfg)
    for bad in ([SOURCE, SOURCE], [dict(SOURCE, command=['ssh snap'])], [dict(SOURCE, command=['/bin/echo', '\0'])]):
        cfg.notifications.rich_sources = bad
        with pytest.raises(ValueError): validate_config(cfg)


def test_subprocess_response_requires_nonce_and_bounds(tmp_path):
    async def scenario():
        path = tmp_path / 'inspect.py'
        path.write_text('import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps(dict(v=1,nonce=r["nonce"],host="snap",instance="native",results={"' + event_id(card()['_rich']) + '":"superseded"})))\n')
        source = dict(SOURCE, command=[sys.executable, '-B', str(path)])
        assert (await daemon_rich.query(source, [card()['_rich']]))[event_id(card()['_rich'])] == 'superseded'
        path.write_text('print("x" * 100000)')
        assert await daemon_rich.query(source, [card()['_rich']]) == {}
        path.write_text('import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps(dict(v=1,nonce="wrong",host="snap",instance="native",results={})))')
        assert await daemon_rich.query(source, [card()['_rich']]) == {}
    asyncio.run(scenario())
