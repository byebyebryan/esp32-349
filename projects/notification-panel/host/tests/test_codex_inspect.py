"""Inspector contract, isolated CLI and real Unix/WebSocket history reads."""

import base64
import hashlib
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from status349.codex_inspect import default_socket, inspect, relation, source_identity
from status349.codex_native import Client
from status349.rich_protocol import event_id, metadata

THREAD = '11111111-1111-4111-8111-111111111111'
OLD = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
NEW = '00000000-0000-4000-8000-000000000000'
HOST = Path(__file__).resolve().parents[1]


class HistoryServer:
    """Own a real listener; reject every operation beyond initialization/history."""

    def __init__(self, path):
        self.path = str(path)
        self.calls = []
        self.error = None
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(self.path)
        self.socket.listen(1)
        self.socket.settimeout(10)
        self.worker = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, *args):
        self.worker.join(10)
        self.socket.close()
        assert not self.worker.is_alive()
        if self.error:
            raise self.error

    def serve(self):
        try:
            connection, _ = self.socket.accept()
            with connection, connection.makefile('rb') as stream:
                connection.settimeout(8)
                assert stream.readline() == b'GET / HTTP/1.1\r\n'
                headers = {}
                while (line := stream.readline()) != b'\r\n':
                    key, value = line.split(b':', 1)
                    headers[key.lower()] = value.strip()
                accept = base64.b64encode(hashlib.sha1(
                    headers[b'sec-websocket-key'] + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                connection.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                                   b'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
                while header := stream.read(2):
                    first, size = header
                    assert first == 0x81 and size & 128
                    size &= 127
                    if size == 126:
                        size = struct.unpack('!H', stream.read(2))[0]
                    elif size == 127:
                        size = struct.unpack('!Q', stream.read(8))[0]
                    assert size < 16384
                    mask = stream.read(4)
                    raw = stream.read(size)
                    request = json.loads(bytes(c ^ mask[i % 4] for i, c in enumerate(raw)))
                    self.calls.append(request)
                    method = request['method']
                    assert method in ('initialize', 'initialized', 'thread/turns/list')
                    if method == 'initialized':
                        continue
                    result = {} if method == 'initialize' else {'data': [
                        {'id': NEW, 'status': 'inProgress'}, {'id': OLD, 'status': 'completed'}]}
                    reply = json.dumps({'id': request['id'], 'result': result}).encode()
                    frame = bytes([0x81, len(reply)]) if len(reply) < 126 else b'\x81\x7e' + struct.pack('!H', len(reply))
                    connection.sendall(frame + reply)
        except BaseException as error:
            self.error = error


def request(path):
    host, instance = source_identity(str(path))
    card = metadata(host, instance, THREAD, OLD)
    return {'v': 1, 'nonce': 'a' * 32, 'cards': [card]}


def test_native_order_requires_original_completed_turn():
    assert relation({'data': [{'id': NEW, 'status': 'inProgress'},
                              {'id': OLD, 'status': 'completed'}]}, OLD) == 'superseded'
    assert relation({'data': [{'id': OLD, 'status': 'completed'}]}, OLD) == 'current'
    for page in ({}, {'data': []}, {'data': [{'id': NEW, 'status': 'completed'}]},
                 {'data': [{'id': OLD, 'status': 'inProgress'}]},
                 {'data': [{'id': OLD, 'status': 'completed'}] * 2}):
        assert relation(page, OLD) == 'unknown'


def test_missing_source_preserves_reply_identity_and_unknown(tmp_path):
    path = tmp_path / 'missing.sock'
    value = request(path)
    result = inspect(value, str(path))
    assert result == {'v': 1, 'nonce': value['nonce'],
                      'host': value['cards'][0]['host'], 'instance': value['cards'][0]['instance'],
                      'results': {event_id(value['cards'][0]): 'unknown'}}


def test_replaced_owner_invalidates_actual_history_read(tmp_path):
    path = tmp_path / 'native.sock'
    with HistoryServer(path):
        value = request(path)
        result = inspect(value, str(path), identity=lambda _: {'changed': True})
    assert result['results'] == {event_id(value['cards'][0]): 'unknown'}


def test_readonly_client_rejects_native_mutations(tmp_path):
    path = tmp_path / 'native.sock'
    with HistoryServer(path) as server, Client(str(path)) as client:
        for method in ('thread/start', 'thread/resume', 'thread/fork', 'turn/start', 'command/exec'):
            with pytest.raises(ValueError, match='read-only'):
                client.call(method, {})
    assert [c['method'] for c in server.calls] == ['initialize', 'initialized']


def test_standalone_cli_reads_exact_history_without_runtime_dependencies(tmp_path):
    # These are the complete remotely vendored files, with no installed status349
    # package, Codex Plus, desktop bus, pyserial or other third-party imports.
    bundle = tmp_path / 'bundle'
    names = ['scripts/349-codex-inspect', 'src/status349/__init__.py',
             'src/status349/codex_inspect.py', 'src/status349/codex_native.py',
             'src/status349/rich_protocol.py']
    for name in names:
        destination = bundle / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HOST / name, destination)
    script = bundle / names[0]
    path = tmp_path / 'native.sock'
    value = request(path)
    with HistoryServer(path) as server:
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), 'inspect', '--socket', str(path)],
                                input=json.dumps(value), text=True, capture_output=True, timeout=8, check=True)
    assert json.loads(result.stdout)['results'] == {event_id(value['cards'][0]): 'superseded'}
    reads = [c for c in server.calls if c['method'] == 'thread/turns/list']
    assert len(reads) == 1
    assert reads[0]['params'] == {'threadId': THREAD, 'limit': 32,
                                 'itemsView': 'summary', 'sortDirection': 'desc'}
    assert not list(bundle.rglob('*.pyc'))
    bad = subprocess.run([sys.executable, '-I', '-S', str(script), 'launch'], capture_output=True)
    assert bad.returncode == 2


def test_selected_homes_do_not_depend_on_codex_plus(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('CODEX_HOME', raising=False)
    monkeypatch.setenv('CODEX_PLUS_SOCKET', '/other/home.sock')
    first = default_socket()
    assert first == str(tmp_path / '.codex/app-server-control/app-server-control.sock')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex-work'))
    second = default_socket()
    assert second == str(tmp_path / '.codex-work/app-server-control/app-server-control.sock')
    assert source_identity(first) != source_identity(second)
    monkeypatch.setenv('CODEX_HOME', 'relative')
    with pytest.raises(ValueError, match='absolute'):
        default_socket()


def test_foreign_source_never_reads_its_thread(tmp_path):
    path = tmp_path / 'native.sock'
    value = request(path)
    value['cards'][0]['instance'] = 'different-home'
    with HistoryServer(path) as server:
        result = inspect(value, str(path))
    assert list(result['results'].values()) == ['unknown']
    assert [c['method'] for c in server.calls] == ['initialize', 'initialized']


def test_inspection_rejects_oversized_request_before_connect(tmp_path):
    value = request(tmp_path / 'not-created.sock')
    value['cards'] *= 33
    with patch('status349.codex_native.socket.socket', side_effect=AssertionError('must not connect')):
        with pytest.raises(ValueError):
            inspect(value, str(tmp_path / 'not-created.sock'))
