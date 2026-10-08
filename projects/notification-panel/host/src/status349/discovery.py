"""Metadata-filtered, nonresetting discovery for the 349 USB link."""

from __future__ import annotations

import asyncio
import glob
import math
import os
import secrets
import stat
import threading
import time
import termios
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import serial
import serial_asyncio
from serial.tools import list_ports

from . import proto
from .pairing import USB_PID, USB_VID, UsbIdentity
from .serial_session import ProbeResult, Session, SessionSerial, _await_reaped, discard_and_close

BAUDRATE = 115200
ATTEMPT_TIMEOUT_S = 2.0
WRITE_TIMEOUT_S = 0.5
READ_POLL_S = 0.05
MAX_PROBE_LINE_BYTES = proto.LINE_MAX
MAX_PROBE_LINES = 128
MAX_PROBE_INPUT_BYTES = 64 * 1024
BY_ID_DIRECTORY = "/dev/serial/by-id"
_INTERFACE_MARKER = "usb_jtag_serial_debug_unit"


class DiscoveryError(RuntimeError):
    """A candidate is absent, busy, ambiguous, or failed identification."""


@dataclass(frozen=True)
class Candidate:
    """A serial interface selected from USB metadata without opening it."""

    path: str
    resolved_path: str
    identity: UsbIdentity | None
    vid: int
    pid: int
    interface: str | None

    @property
    def serial(self) -> str | None:
        return self.identity.serial if self.identity is not None else None


def _resolved(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.fspath(path))


def _interface_matches(info: object, aliases: Iterable[str]) -> bool:
    value = getattr(info, "interface", None)
    if isinstance(value, str) and value.strip():
        normalized = value.casefold()
        return "jtag" in normalized and ("serial" in normalized or "debug" in normalized)
    return any(_INTERFACE_MARKER in Path(alias).name.casefold() for alias in aliases)


def _alias_rank(path: str) -> tuple[int, str]:
    basename = Path(path).name.casefold()
    preferred = _INTERFACE_MARKER in basename and ("-if00" in basename or "-if" not in basename)
    return (0 if preferred else 1, path)


def enumerate_candidates(
    *,
    port_infos: Iterable[object] | None = None,
    by_id_paths: Iterable[str | os.PathLike[str]] | None = None,
) -> list[Candidate]:
    """Return deduplicated 303a:1001 USB Serial/JTAG candidates.

    Enumeration reads PySerial metadata and symlink names only. It never
    opens a tty or sends a byte to a device.
    """
    infos = list(list_ports.comports() if port_infos is None else port_infos)
    aliases = list(
        glob.glob(os.path.join(BY_ID_DIRECTORY, "*"))
        if by_id_paths is None
        else (os.fspath(path) for path in by_id_paths)
    )
    aliases_by_target: dict[str, list[str]] = {}
    for alias in aliases:
        aliases_by_target.setdefault(_resolved(alias), []).append(alias)

    selected: dict[str, Candidate] = {}
    for info in infos:
        device = getattr(info, "device", None)
        vid = getattr(info, "vid", None)
        pid = getattr(info, "pid", None)
        if (
            not isinstance(device, str)
            or type(vid) is not int
            or type(pid) is not int
            or vid != USB_VID
            or pid != USB_PID
        ):
            continue
        resolved_path = _resolved(device)
        device_aliases = aliases_by_target.get(resolved_path, [])
        if not _interface_matches(info, device_aliases):
            continue
        interface = getattr(info, "interface", None)
        interface = interface if isinstance(interface, str) and interface.strip() else None
        serial_number = getattr(info, "serial_number", None)
        identity = None
        if isinstance(serial_number, str) and serial_number.strip():
            identity = UsbIdentity(serial_number)

        paths = [*device_aliases, device]
        path = min(paths, key=_alias_rank)
        candidate = Candidate(path, resolved_path, identity, vid, pid, interface)
        previous = selected.get(resolved_path)
        if previous is not None and previous.identity != candidate.identity:
            raise DiscoveryError(f"conflicting USB serial metadata for tty {resolved_path}")
        if previous is None or _alias_rank(candidate.path) < _alias_rank(previous.path):
            selected[resolved_path] = candidate

    return sorted(selected.values(), key=lambda candidate: candidate.path)


def resolve_serial(serial_number: str) -> Candidate | None:
    """Resolve one exact opaque USB serial without opening any candidate."""
    if not isinstance(serial_number, str) or not serial_number.strip():
        raise ValueError("USB serial must be a nonempty string")
    matches = [
        candidate for candidate in enumerate_candidates()
        if candidate.identity is not None and candidate.identity.serial == serial_number
    ]
    if len(matches) > 1:
        paths = ", ".join(candidate.path for candidate in matches)
        raise DiscoveryError(f"USB serial {serial_number!r} is ambiguous across {paths}")
    return matches[0] if matches else None


def identity_for_path(path: str | os.PathLike[str]) -> UsbIdentity | None:
    """Return USB metadata for one explicit path; PTYs and non-USB paths yield None."""
    target = _resolved(path)
    matches = [candidate for candidate in enumerate_candidates() if candidate.resolved_path == target]
    if not matches:
        return None
    identities = {candidate.identity for candidate in matches}
    if len(identities) > 1:
        raise DiscoveryError(f"USB metadata for {path!s} changed during enumeration")
    return matches[0].identity


def _candidate_for_path(path: str | os.PathLike[str]) -> Candidate:
    target_path = os.fspath(path)
    target = _resolved(target_path)
    matches = [candidate for candidate in enumerate_candidates() if candidate.resolved_path == target]
    if len(matches) > 1:
        raise DiscoveryError(f"multiple USB metadata entries resolve to {target_path!r}")
    if matches:
        return matches[0]
    return Candidate(target_path, target, None, USB_VID, USB_PID, None)


def _visible_port_owners(path: str) -> list[int]:
    """Best-effort Linux scan for processes holding the same character tty."""
    if not sys_platform_linux():
        return []
    try:
        target_stat = os.stat(path)
    except OSError:
        return []
    if not stat.S_ISCHR(target_stat.st_mode):
        return []
    resolved_target = _resolved(path)
    owners: list[int] = []
    try:
        processes = os.scandir("/proc")
    except OSError:
        return []
    with processes:
        for process in processes:
            if not process.name.isdecimal():
                continue
            pid = int(process.name)
            try:
                descriptors = os.scandir(os.path.join("/proc", process.name, "fd"))
            except OSError:
                continue
            found = False
            with descriptors:
                for descriptor in descriptors:
                    try:
                        # Following every /proc fd can block on an unrelated
                        # FUSE/network mount before the bounded USB probe starts.
                        # Kernel tty fd links resolve to /dev paths; retain a
                        # direct match for explicitly selected non-/dev nodes.
                        target = os.readlink(descriptor.path)
                        if target != resolved_target and not target.startswith("/dev/"):
                            continue
                        descriptor_stat = descriptor.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    if (
                        stat.S_ISCHR(descriptor_stat.st_mode)
                        and descriptor_stat.st_rdev == target_stat.st_rdev
                    ):
                        found = True
                        break
            if found:
                owners.append(pid)
    return owners


def sys_platform_linux() -> bool:
    return sys.platform.startswith("linux") and os.path.exists("/proc/self/fd")


def _acquire_linux_exclusive(port: serial.Serial) -> None:
    if not sys_platform_linux():
        return
    try:
        fcntl_ioctl = __import__("fcntl").ioctl
        fcntl_ioctl(port.fileno(), termios.TIOCEXCL)
        port._status349_tty_exclusive = True
    except (AttributeError, OSError) as exc:
        raise DiscoveryError(f"cannot acquire Linux tty exclusivity: {exc}") from exc


def _open_serial(path: str, serial_factory=SessionSerial) -> serial.Serial:
    port = serial_factory(
        port=None,
        baudrate=BAUDRATE,
        timeout=READ_POLL_S,
        write_timeout=WRITE_TIMEOUT_S,
        exclusive=True,
    )
    # PySerial applies these levels during open. Keeping both asserted avoids
    # the DTR/RTS reset combination used only by the direct diagnostic path.
    port.dtr = True
    port.rts = True
    port.port = path
    try:
        open_port = getattr(port, "open", None)
        if callable(open_port) and not getattr(port, "is_open", False):
            open_port()
        _acquire_linux_exclusive(port)
    except BaseException as exc:
        discard_and_close(port)
        if isinstance(exc, (OSError, serial.SerialException, DiscoveryError)):
            raise DiscoveryError(f"cannot open exclusive tty {path}: {exc}") from exc
        raise
    return port


class _ProbeCancelled(Exception):
    pass


def _read_protocol_line(
    port: serial.Serial,
    buffer: bytearray,
    *,
    deadline: float,
    cancelled: threading.Event,
    counters: list[int],
) -> str:
    while True:
        if cancelled.is_set():
            raise _ProbeCancelled("USB probe cancelled")
        newline = buffer.find(b"\n")
        if newline >= 0:
            raw = bytes(buffer[:newline])
            del buffer[:newline + 1]
            counters[0] += 1
            counters[1] += len(raw) + 1
            if counters[0] > MAX_PROBE_LINES or counters[1] > MAX_PROBE_INPUT_BYTES:
                raise DiscoveryError("probe received too many log or malformed lines")
            if len(raw) > MAX_PROBE_LINE_BYTES:
                return ""  # A bounded overlong frame is ignored as malformed input.
            return raw.decode("utf-8", "replace").rstrip("\r")

        if len(buffer) > MAX_PROBE_INPUT_BYTES:
            raise DiscoveryError("probe input exceeded its 64 KiB limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DiscoveryError("device hello/ping probe timed out")
        port.timeout = min(READ_POLL_S, remaining)
        chunk = port.read(256)
        if chunk:
            buffer.extend(chunk)


def _next_data_frame(
    port: serial.Serial,
    buffer: bytearray,
    *,
    deadline: float,
    cancelled: threading.Event,
    counters: list[int],
) -> dict:
    while True:
        line = _read_protocol_line(
            port, buffer, deadline=deadline, cancelled=cancelled, counters=counters
        )
        is_data, message = proto.classify(line)
        if is_data and message is not None:
            return message


def _discard_queued_pongs_before_challenge(
    port: serial.Serial,
    buffer: bytearray,
    cancelled: threading.Event,
) -> None:
    """Drain bytes already queued before sending the fresh challenge.

    Keep all non-pong trailing bytes for adoption. A pong already received
    before the challenge cannot satisfy it, even if its numeric value happens
    to match the newly generated nonce.
    """
    total = len(buffer)
    while True:
        if cancelled.is_set():
            raise _ProbeCancelled("USB probe cancelled before ping challenge")
        port.timeout = 0
        chunk = port.read(256)
        if not chunk:
            break
        buffer.extend(chunk)
        total += len(chunk)
        if total > MAX_PROBE_INPUT_BYTES:
            raise DiscoveryError("probe input exceeded its 64 KiB limit")

    kept = bytearray()
    while (newline := buffer.find(b"\n")) >= 0:
        raw = bytes(buffer[:newline])
        del buffer[:newline + 1]
        is_data, message = proto.classify(raw.decode("utf-8", "replace").rstrip("\r"))
        if not (is_data and message is not None and message.get("t") == "pong"):
            kept.extend(raw)
            kept.extend(b"\n")
    kept.extend(buffer)
    buffer[:] = kept
    port.timeout = READ_POLL_S


def _bounded_write(port: serial.Serial, payload: bytes, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DiscoveryError("device hello/ping probe timed out before write")
    port.write_timeout = min(WRITE_TIMEOUT_S, remaining)
    try:
        written = port.write(payload)
    except (serial.SerialTimeoutException, OSError, serial.SerialException) as exc:
        raise DiscoveryError(f"device probe write failed: {exc}") from exc
    if written != len(payload):
        raise DiscoveryError(f"device probe wrote {written} of {len(payload)} bytes")


def _device_signature(path: str) -> tuple[int, int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISCHR(info.st_mode):
        return None
    return info.st_dev, info.st_ino, info.st_rdev


def _handle_signature(port: serial.Serial) -> tuple[int, int, int] | None:
    try:
        info = os.fstat(port.fileno())
    except (AttributeError, OSError, ValueError):
        return None
    if not stat.S_ISCHR(info.st_mode):
        return None
    return info.st_dev, info.st_ino, info.st_rdev


def _signature_changed(first: tuple[int, int, int] | None, second: tuple[int, int, int] | None) -> bool:
    return first != second and (first is not None or second is not None)


def _current_identity(path: str) -> UsbIdentity | None:
    return identity_for_path(path)


def _probe_sync(
    candidate: Candidate,
    expected_identity: UsbIdentity | None,
    cancelled: threading.Event,
    owner_check,
    serial_factory,
    metadata_lookup,
) -> ProbeResult:
    path = candidate.path
    owners = owner_check(path)
    if owners:
        owner_list = ", ".join(str(pid) for pid in owners)
        raise DiscoveryError(f"tty {path} is already open by visible process(es): {owner_list}")

    current_identity = metadata_lookup(path)
    if expected_identity is not None and current_identity != expected_identity:
        raise DiscoveryError(
            f"USB identity for {path} changed before open: expected serial "
            f"{expected_identity.serial!r}, found "
            f"{current_identity.serial if current_identity else None!r}"
        )
    if expected_identity is None:
        expected_identity = current_identity or candidate.identity
    before_signature = _device_signature(path)
    if cancelled.is_set():
        raise _ProbeCancelled("USB probe cancelled before open")

    port: serial.Serial | None = None
    success = False
    try:
        port = _open_serial(path, serial_factory)

        opened_signature = _handle_signature(port)
        if _signature_changed(before_signature, opened_signature):
            raise DiscoveryError(f"tty device at {path} changed between metadata lookup and open")
        if cancelled.is_set():
            raise _ProbeCancelled("USB probe cancelled while opening tty")

        deadline = time.monotonic() + ATTEMPT_TIMEOUT_S
        receive_buffer = bytearray()
        counters = [0, 0]
        # Discarding queued output on the previous close may leave a partial
        # application frame in the board's line buffer. Finish that line before
        # the discovery hello so this attempt can identify the still-running peer.
        _bounded_write(port, b"\n" + proto.encode(proto.hello()), deadline)

        while True:
            message = _next_data_frame(
                port, receive_buffer, deadline=deadline,
                cancelled=cancelled, counters=counters,
            )
            if not proto.is_device_hello(message):
                continue
            device_hello = message
            break

        if cancelled.is_set():
            raise _ProbeCancelled("USB probe cancelled after hello")
        _discard_queued_pongs_before_challenge(port, receive_buffer, cancelled)
        nonce = secrets.randbelow(0x7FFFFFFF) + 1
        _bounded_write(port, proto.encode({"t": "ping", "ts": nonce}), deadline)

        while True:
            message = _next_data_frame(
                port, receive_buffer, deadline=deadline,
                cancelled=cancelled, counters=counters,
            )
            echoed = message.get("ts")
            echoed_nonce = (
                type(echoed) is int and echoed == nonce
            ) or (
                type(echoed) is float and math.isfinite(echoed) and echoed == nonce
            )
            if message.get("t") == "pong" and echoed_nonce:
                break

        if cancelled.is_set():
            raise _ProbeCancelled("USB probe cancelled after pong")
        after_signature = _device_signature(path)
        if _signature_changed(before_signature, after_signature):
            detail = "disappeared" if after_signature is None else "changed"
            raise DiscoveryError(f"tty device at {path} {detail} during probe")
        final_handle_signature = _handle_signature(port)
        if _signature_changed(opened_signature, final_handle_signature):
            raise DiscoveryError(f"open tty handle for {path} changed during probe")
        if _signature_changed(after_signature, final_handle_signature):
            raise DiscoveryError(f"tty path {path} no longer names the opened device")
        after_identity = metadata_lookup(path)
        if expected_identity is not None and after_identity != expected_identity:
            raise DiscoveryError(
                f"USB identity for {path} changed during probe: expected serial "
                f"{expected_identity.serial!r}, found "
                f"{after_identity.serial if after_identity else None!r}"
            )
        identity = after_identity or expected_identity
        result = ProbeResult(
            handle=port,
            hello=device_hello,
            identity=identity,
            buffered=bytes(receive_buffer),
            path=path,
        )
        success = True
        return result
    except _ProbeCancelled as exc:
        raise DiscoveryError(str(exc)) from exc
    finally:
        if port is not None and not success:
            discard_and_close(port)


async def probe(
    target: str | os.PathLike[str] | Candidate,
    *,
    identity: UsbIdentity | None = None,
    owner_check=None,
    serial_factory=None,
    metadata_lookup=None,
) -> ProbeResult:
    """Verify one target off-loop and return its still-open handle and buffer."""
    if isinstance(target, Candidate):
        candidate = target
    else:
        target_path = os.fspath(target)
        candidate = await asyncio.to_thread(_candidate_for_path, target_path)
    expected_identity = identity or candidate.identity
    if identity is not None and candidate.identity is not None and identity != candidate.identity:
        raise DiscoveryError(
            f"USB identity for {candidate.path} does not match requested serial {identity.serial!r}"
        )
    owner_check = owner_check or _visible_port_owners
    if serial_factory is None:
        serial_factory = SessionSerial
    metadata_lookup = metadata_lookup or _current_identity

    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        _probe_sync, candidate, expected_identity, cancelled, owner_check,
        serial_factory, metadata_lookup,
    ))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancelled_error:
        cancelled.set()
        late_result = None
        try:
            late_result, _ = await _await_reaped(worker, propagate_cancel=False)
        except Exception:
            pass
        if late_result is not None:
            try:
                await late_result.close()
            except asyncio.CancelledError:
                pass
        raise asyncio.CancelledError from cancelled_error


async def adopt(result: ProbeResult) -> Session:
    """Attach asyncio streams to the exact verified serial instance."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    try:
        transport, protocol = await serial_asyncio.connection_for_serial(
            loop, lambda: protocol, result.handle
        )
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        if result.buffered:
            reader.feed_data(result.buffered)
        result._take_for_adoption()
    except BaseException:
        await result.close()
        raise
    return Session(
        reader=reader,
        writer=writer,
        hello=result.hello,
        identity=result.identity,
        path=result.path,
        _serial=result.handle,
    )


__all__ = [
    "ATTEMPT_TIMEOUT_S",
    "BAUDRATE",
    "BY_ID_DIRECTORY",
    "Candidate",
    "DiscoveryError",
    "ProbeResult",
    "Session",
    "adopt",
    "enumerate_candidates",
    "identity_for_path",
    "probe",
    "resolve_serial",
]
