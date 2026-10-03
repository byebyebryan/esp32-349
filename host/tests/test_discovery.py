import asyncio
import errno
import os
import time
import threading
from functools import wraps
from types import SimpleNamespace

import pytest
import serial

from status349 import discovery, proto
from status349.pairing import UsbIdentity
from status349.serial_session import ProbeResult, SessionSerial

DEVICE_ID = UsbIdentity("349-usb-opaque")
DEVICE_HELLO = {
    "t": "hello",
    "proto": 1,
    "fw": "test-fw",
    "cap": ["link", "bar"],
}


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


@pytest.fixture(autouse=True)
def fake_serial_skips_kernel_tty_exclusive(monkeypatch):
    """Injected protocol fakes have no kernel tty; PTY tests opt back in."""
    actual = discovery._acquire_linux_exclusive
    monkeypatch.setattr(discovery, "_acquire_linux_exclusive", lambda _port: None)
    return actual


class FakeSerial:
    def __init__(self, *, response, port=None, **options):
        self.response = response
        self.port = port
        self.options = options
        self.timeout = options.get("timeout", 0.05)
        self.write_timeout = options.get("write_timeout")
        self.dtr = False
        self.rts = False
        self.is_open = False
        self.writes = []
        self.reads = 0
        self.close_event = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.chunks = []
        self.reset_count = 0
        self.open_controls = None

    def open(self):
        self.open_controls = (self.dtr, self.rts)
        assert self.open_controls == (True, True)
        self.is_open = True

    def write(self, payload):
        self.writes.append(payload)
        is_data, message = proto.classify(payload.decode("utf-8").rstrip("\n"))
        assert is_data and message is not None
        self.response(self, message)
        return len(payload)

    def queue(self, payload):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        with self.lock:
            self.chunks.append(bytes(payload))
            self.ready.set()

    def read(self, size):
        self.reads += 1
        deadline = time.monotonic() + max(float(self.timeout or 0), 0)
        while True:
            with self.lock:
                if self.chunks:
                    chunk = self.chunks[0]
                    result = chunk[:size]
                    rest = chunk[size:]
                    if rest:
                        self.chunks[0] = rest
                    else:
                        self.chunks.pop(0)
                    if not self.chunks:
                        self.ready.clear()
                    return result
                self.ready.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            self.ready.wait(remaining)

    def reset_output_buffer(self):
        self.reset_count += 1

    def close(self):
        self.is_open = False
        self.close_event.set()


def candidate(identity=DEVICE_ID, path="/dev/fake349"):
    return discovery.Candidate(
        path=path,
        resolved_path=os.path.realpath(path),
        identity=identity,
        vid=0x303A,
        pid=0x1001,
        interface="USB JTAG/serial debug unit",
    )


def serial_factory_for(response, opened):
    def factory(**kwargs):
        port = FakeSerial(response=response, **kwargs)
        opened.append(port)
        return port

    return factory


def response_for(*, hello=DEVICE_HELLO, pong=None, trailing=b""):
    def respond(port, request):
        if request.get("t") == "hello":
            port.queue(proto.encode(hello))
        elif request.get("t") == "ping" and pong is not None:
            value = pong(request["ts"]) if callable(pong) else pong
            port.queue(proto.encode({"t": "pong", "ts": value}) + trailing)

    return respond


async def do_probe(response, **kwargs):
    opened = []
    result = await discovery.probe(
        candidate(),
        owner_check=lambda _path: [],
        serial_factory=serial_factory_for(response, opened),
        metadata_lookup=lambda _path: DEVICE_ID,
        **kwargs,
    )
    return result, opened


def test_hello_validator_requires_exact_protocol_integer_and_base_capabilities():
    assert proto.is_device_hello(DEVICE_HELLO)
    for invalid in (
        None,
        [],
        {**DEVICE_HELLO, "proto": True},
        {**DEVICE_HELLO, "proto": 1.0},
        {**DEVICE_HELLO, "proto": 2},
        {**DEVICE_HELLO, "fw": None},
        {**DEVICE_HELLO, "cap": "link,bar"},
        {**DEVICE_HELLO, "cap": ["link", "bar", 1]},
        {**DEVICE_HELLO, "cap": ["link"]},
        {**DEVICE_HELLO, "cap": ["bar"]},
    ):
        assert not proto.is_device_hello(invalid)


def test_enumeration_filters_real_usb_metadata_prefers_by_id_and_deduplicates(tmp_path):
    tty = tmp_path / "ttyACM0"
    tty.write_text("")
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    if00 = by_id / "usb-Espressif_USB_JTAG_serial_debug_unit_349-if00"
    if01 = by_id / "usb-Espressif_USB_JTAG_serial_debug_unit_349-if01"
    generic = by_id / "usb-other-alias"
    if00.symlink_to(tty)
    if01.symlink_to(tty)
    generic.symlink_to(tty)
    good = SimpleNamespace(
        device=str(tty), vid=0x303A, pid=0x1001,
        serial_number="exact-349-serial", interface="USB JTAG/serial debug unit",
    )
    duplicate = SimpleNamespace(**good.__dict__)
    unrelated = SimpleNamespace(
        device=str(tmp_path / "unrelated"), vid=0x10C4, pid=0xEA60,
        serial_number="349-looking", interface="USB JTAG/serial debug unit",
    )
    wrong_interface = SimpleNamespace(
        device=str(tmp_path / "other-303a"), vid=0x303A, pid=0x1001,
        serial_number="not-jtag", interface="USB Serial Converter",
    )

    found = discovery.enumerate_candidates(
        port_infos=[unrelated, wrong_interface, good, duplicate],
        by_id_paths=[str(generic), str(if01), str(if00)],
    )

    assert len(found) == 1
    assert found[0].path == str(if00)
    assert found[0].identity == UsbIdentity("exact-349-serial")
    assert found[0].resolved_path == str(tty.resolve())


def test_interface_can_be_confirmed_by_existing_jtag_by_id_alias(tmp_path):
    tty = tmp_path / "ttyACM1"
    tty.write_text("")
    alias = tmp_path / "usb-Espressif_USB_JTAG_serial_debug_unit_serial-if00"
    alias.symlink_to(tty)
    info = SimpleNamespace(
        device=str(tty), vid=0x303A, pid=0x1001,
        serial_number="serial", interface=None,
    )

    found = discovery.enumerate_candidates(port_infos=[info], by_id_paths=[str(alias)])

    assert [item.path for item in found] == [str(alias)]


def test_exact_serial_resolution_returns_none_and_reports_ambiguity(monkeypatch):
    one = candidate(UsbIdentity("serial"), "/dev/one")
    two = candidate(UsbIdentity("other"), "/dev/two")
    monkeypatch.setattr(discovery, "enumerate_candidates", lambda: [one, two])

    assert discovery.resolve_serial("serial") is one
    assert discovery.resolve_serial("missing") is None

    duplicate = candidate(UsbIdentity("serial"), "/dev/duplicate")
    monkeypatch.setattr(discovery, "enumerate_candidates", lambda: [one, duplicate])
    with pytest.raises(discovery.DiscoveryError, match="ambiguous"):
        discovery.resolve_serial("serial")


def test_identity_lookup_matches_resolved_path_and_leaves_pty_unpaired(tmp_path, monkeypatch):
    tty = tmp_path / "ttyACM9"
    tty.write_text("")
    alias = tmp_path / "serial-alias"
    alias.symlink_to(tty)
    item = discovery.Candidate(
        path=str(tty), resolved_path=str(tty.resolve()), identity=DEVICE_ID,
        vid=0x303A, pid=0x1001, interface="USB JTAG/serial debug unit",
    )
    monkeypatch.setattr(discovery, "enumerate_candidates", lambda: [item])

    assert discovery.identity_for_path(alias) == DEVICE_ID
    assert discovery.identity_for_path(item.path) == DEVICE_ID
    assert discovery.identity_for_path("/dev/pts/999") is None


@async_test
async def test_probe_ignores_logs_echo_and_malformed_lines_and_preserves_trailing_frame():
    opened = []

    def respond(port, request):
        if request["t"] == "hello":
            port.queue(b"I boot log\n@349 malformed\n")
            port.queue(proto.encode(proto.hello()))  # echoed request is not a device hello
            valid = proto.encode(DEVICE_HELLO)
            for start in range(0, len(valid), 3):
                port.queue(valid[start:start + 3])
        else:
            port.queue(
                proto.encode({"t": "pong", "ts": float(request["ts"])})
                + b"@349 {\"t\":\"later-device-frame\",\"v\":7}\n"
            )

    result = await discovery.probe(
        candidate(),
        owner_check=lambda _path: [],
        serial_factory=serial_factory_for(respond, opened),
        metadata_lookup=lambda _path: DEVICE_ID,
    )

    assert result.hello == DEVICE_HELLO
    assert result.identity == DEVICE_ID
    assert result.path == "/dev/fake349"
    assert result.buffered == b'@349 {"t":"later-device-frame","v":7}\n'
    assert opened[0].open_controls == (True, True)
    assert opened[0].options["exclusive"] is True
    assert len(opened[0].writes) == 2
    await result.close()
    assert not opened[0].is_open
    assert opened[0].reset_count == 1


@async_test
async def test_probe_rejects_invalid_hello_wrong_nonce_and_boolean_echo(monkeypatch):
    monkeypatch.setattr(discovery, "ATTEMPT_TIMEOUT_S", 0.12)
    opened = []

    def invalid_hello(port, request):
        if request["t"] == "hello":
            port.queue(proto.encode({**DEVICE_HELLO, "proto": True}))

    with pytest.raises(discovery.DiscoveryError, match="timed out"):
        await discovery.probe(
            candidate(), owner_check=lambda _path: [],
            serial_factory=serial_factory_for(invalid_hello, opened),
            metadata_lookup=lambda _path: DEVICE_ID,
        )
    assert len(opened[0].writes) == 1
    assert not opened[0].is_open

    opened.clear()

    def wrong_nonce(port, request):
        if request["t"] == "hello":
            port.queue(proto.encode(DEVICE_HELLO))
        else:
            port.queue(proto.encode({"t": "pong", "ts": request["ts"] + 1}))
            port.queue(proto.encode({"t": "pong", "ts": True}))

    with pytest.raises(discovery.DiscoveryError, match="timed out"):
        await discovery.probe(
            candidate(), owner_check=lambda _path: [],
            serial_factory=serial_factory_for(wrong_nonce, opened),
            metadata_lookup=lambda _path: DEVICE_ID,
        )
    assert not opened[0].is_open


@async_test
async def test_probe_does_not_accept_a_matching_pong_queued_before_the_challenge(monkeypatch):
    monkeypatch.setattr(discovery.secrets, "randbelow", lambda _upper: 40)
    monkeypatch.setattr(discovery, "ATTEMPT_TIMEOUT_S", 0.12)
    opened = []

    def stale_nonce(port, request):
        if request["t"] == "hello":
            port.queue(proto.encode(DEVICE_HELLO) + proto.encode({"t": "pong", "ts": 41}))
        # Deliberately remain silent after the challenge.

    with pytest.raises(discovery.DiscoveryError, match="timed out"):
        await discovery.probe(
            candidate(), owner_check=lambda _path: [],
            serial_factory=serial_factory_for(stale_nonce, opened),
            metadata_lookup=lambda _path: DEVICE_ID,
        )
    assert len(opened[0].writes) == 2
    assert not opened[0].is_open


@async_test
async def test_busy_port_is_skipped_without_opening_or_writing():
    opened = []
    with pytest.raises(discovery.DiscoveryError, match="already open"):
        await discovery.probe(
            candidate(), owner_check=lambda _path: [1234],
            serial_factory=serial_factory_for(response_for(pong=lambda nonce: nonce), opened),
            metadata_lookup=lambda _path: DEVICE_ID,
        )
    assert opened == []


@async_test
async def test_probe_rejects_changed_metadata_before_returning_verified_handle():
    opened = []
    current = [DEVICE_ID]

    def respond(port, request):
        if request["t"] == "hello":
            port.queue(proto.encode(DEVICE_HELLO))
        else:
            current[0] = UsbIdentity("replacement-usb-serial")
            port.queue(proto.encode({"t": "pong", "ts": request["ts"]}))

    with pytest.raises(discovery.DiscoveryError, match="changed during probe"):
        await discovery.probe(
            candidate(), owner_check=lambda _path: [],
            serial_factory=serial_factory_for(respond, opened),
            metadata_lookup=lambda _path: current[0],
        )
    assert not opened[0].is_open


@async_test
async def test_probe_rejects_disappeared_path_signature(monkeypatch):
    signatures = iter([(1, 9, 99), None])
    monkeypatch.setattr(discovery, "_device_signature", lambda _path: next(signatures))
    monkeypatch.setattr(discovery, "_handle_signature", lambda _port: (1, 9, 99))
    opened = []

    with pytest.raises(discovery.DiscoveryError, match="disappeared"):
        await discovery.probe(
            candidate(), owner_check=lambda _path: [],
            serial_factory=serial_factory_for(response_for(pong=lambda nonce: nonce), opened),
            metadata_lookup=lambda _path: DEVICE_ID,
        )
    assert not opened[0].is_open


@async_test
async def test_stalled_write_is_bounded_and_runs_off_the_event_loop(monkeypatch):
    monkeypatch.setattr(discovery, "WRITE_TIMEOUT_S", 0.05)
    opened = []
    ticked = asyncio.Event()

    def stalled(_port, _request):
        time.sleep(0.1)
        raise serial.SerialTimeoutException("simulated stalled write")

    task = asyncio.create_task(discovery.probe(
        candidate(), owner_check=lambda _path: [],
        serial_factory=serial_factory_for(stalled, opened),
        metadata_lookup=lambda _path: DEVICE_ID,
    ))
    await asyncio.sleep(0.01)
    ticked.set()
    assert ticked.is_set()
    started = time.monotonic()
    with pytest.raises(discovery.DiscoveryError, match="write failed"):
        await task
    assert time.monotonic() - started < 0.5
    assert opened[0].write_timeout <= 0.05
    assert not opened[0].is_open


@async_test
async def test_probe_cancellation_reaps_worker_and_closes_open_handle(monkeypatch):
    monkeypatch.setattr(discovery, "ATTEMPT_TIMEOUT_S", 10)
    opened = []
    worker_done = threading.Event()
    original = discovery._probe_sync

    def tracked(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            worker_done.set()

    monkeypatch.setattr(discovery, "_probe_sync", tracked)
    task = asyncio.create_task(discovery.probe(
        candidate(), owner_check=lambda _path: [],
        serial_factory=serial_factory_for(lambda _port, _request: None, opened),
        metadata_lookup=lambda _path: DEVICE_ID,
    ))
    while not opened:
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.5)

    assert worker_done.is_set()
    assert not opened[0].is_open


@async_test
async def test_unadopted_slow_close_reaps_worker_through_repeated_cancellation():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class SlowClose(FakeSerial):
        def __init__(self, **kwargs):
            super().__init__(response=lambda *_: None, **kwargs)
            self.is_open = True

        def close(self):
            started.set()
            release.wait(1)
            self.is_open = False
            finished.set()

    port = SlowClose()
    result = ProbeResult(port, DEVICE_HELLO, DEVICE_ID, b"", "/dev/fake349")
    closing = asyncio.create_task(result.close())
    await asyncio.to_thread(started.wait, 0.5)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, 0.5)
    assert finished.is_set()
    assert not port.is_open


@async_test
async def test_adopt_keeps_buffered_bytes_and_abort_bypasses_blocking_flush(
    monkeypatch, fake_serial_skips_kernel_tty_exclusive
):
    import pty

    master_fd, slave_fd = pty.openpty()
    path = os.ttyname(slave_fd)
    os.close(slave_fd)
    handle = SessionSerial(port=None, baudrate=115200, timeout=0.05, write_timeout=0.5)
    handle.dtr = True
    handle.rts = True
    handle.port = path
    handle.open()
    fake_serial_skips_kernel_tty_exclusive(handle)
    with pytest.raises(OSError) as occupied:
        os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    assert occupied.value.errno == errno.EBUSY
    original_flush = SessionSerial.flush
    calls = []

    def observed_flush(self):
        calls.append(self._discard_teardown)
        if not self._discard_teardown:
            time.sleep(1)
        return original_flush(self)

    monkeypatch.setattr(SessionSerial, "flush", observed_flush)
    result = ProbeResult(
        handle=handle,
        hello=DEVICE_HELLO,
        identity=None,
        buffered=b"boot log\n",
        path=path,
    )
    try:
        session = await discovery.adopt(result)
        assert await session.reader.readexactly(len(b"boot log\n")) == b"boot log\n"
        session.writer.write(b"queued output")
        started = time.monotonic()
        await asyncio.wait_for(session.close(), 0.5)
        await session.close()
        assert time.monotonic() - started < 0.5
        assert calls == [True]
        assert not handle.is_open
        reopened = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        os.close(reopened)
    finally:
        os.close(master_fd)
