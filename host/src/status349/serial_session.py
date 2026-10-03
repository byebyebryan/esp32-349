"""Serial ownership and discard-first cleanup for discovery sessions."""

from __future__ import annotations

import asyncio
import fcntl
import sys
import termios
from dataclasses import dataclass, field

import serial

from .pairing import UsbIdentity

CLEANUP_TIMEOUT_S = 0.5


async def _await_reaped(task: asyncio.Task, *, propagate_cancel: bool = True):
    """Wait for an owned task to finish through repeated caller cancellation."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except BaseException:
            # Retrieve the task exception below after it is marked complete.
            break
    result = task.result()
    if interrupted and propagate_cancel:
        raise asyncio.CancelledError
    return result, interrupted


class SessionSerial(serial.Serial):
    """PySerial handle whose flush can be disabled only during discard teardown."""

    def __init__(self, *args, **kwargs):
        self._discard_teardown = False
        self._status349_tty_exclusive = False
        super().__init__(*args, **kwargs)

    def begin_discard_teardown(self) -> None:
        self._discard_teardown = True

    def flush(self) -> None:
        if self._discard_teardown:
            return
        super().flush()

    def close(self) -> None:
        self.begin_discard_teardown()
        release_linux_exclusive(self)
        super().close()

    def read(self, size: int = 1) -> bytes:
        try:
            return super().read(size)
        except (OSError, serial.SerialException):
            self._mark_failed_transport()
            raise

    def write(self, data: bytes) -> int:
        try:
            return super().write(data)
        except (OSError, serial.SerialException):
            self._mark_failed_transport()
            raise

    def _mark_failed_transport(self) -> None:
        self.begin_discard_teardown()
        try:
            self.reset_output_buffer()
        except (OSError, serial.SerialException, termios.error):
            pass


def discard_serial_output(port: serial.Serial) -> None:
    """Stop flush-on-close and discard driver output before closing the tty."""
    begin_discard = getattr(port, "begin_discard_teardown", None)
    if callable(begin_discard):
        begin_discard()
    try:
        port.reset_output_buffer()
    except (OSError, serial.SerialException, termios.error, AttributeError):
        pass


def release_linux_exclusive(port: serial.Serial) -> None:
    """Release TIOCEXCL only when this handle successfully acquired it."""
    if not getattr(port, "_status349_tty_exclusive", False):
        return
    try:
        if sys.platform.startswith("linux"):
            fcntl.ioctl(port.fileno(), termios.TIOCNXCL)
    except (AttributeError, OSError, ValueError):
        # A removed tty releases exclusivity with its file descriptor.
        pass
    finally:
        port._status349_tty_exclusive = False


def discard_and_close(port: serial.Serial) -> None:
    discard_serial_output(port)
    release_linux_exclusive(port)
    try:
        port.close()
    except (OSError, serial.SerialException):
        pass


@dataclass
class ProbeResult:
    """Verified, open serial handle awaiting adoption or explicit disposal."""

    handle: serial.Serial
    hello: dict
    identity: UsbIdentity | None
    buffered: bytes
    path: str
    _closed: bool = False
    _adopted: bool = False
    _close_task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def serial(self) -> serial.Serial:
        return self.handle

    def _take_for_adoption(self) -> None:
        if self._closed:
            raise RuntimeError("probe result is already closed")
        if self._adopted:
            raise RuntimeError("probe result was already adopted")
        self._adopted = True

    async def close(self) -> None:
        if self._adopted:
            raise RuntimeError("adopted probe result must be closed through its session")
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(_close_unadopted(self.handle))
        await _await_reaped(self._close_task)


@dataclass
class Session:
    """Adopted reader/writer and the serial handle that owns their lifetime."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    hello: dict
    identity: UsbIdentity | None
    path: str
    _serial: serial.Serial = field(repr=False)
    _close_task: asyncio.Task[None] | None = field(default=None, repr=False)

    async def close(self) -> None:
        """Abort, discard queued output, and release tty exclusivity once."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_impl())
        await _await_reaped(self._close_task)

    async def _close_impl(self) -> None:
        begin_discard = getattr(self._serial, "begin_discard_teardown", None)
        if callable(begin_discard):
            begin_discard()
        transport = self.writer.transport
        abort = getattr(transport, "abort", None)
        is_closing = getattr(transport, "is_closing", None)
        buffered = getattr(transport, "get_write_buffer_size", None)
        closing = callable(is_closing) and is_closing()
        # SerialTransport.abort is not idempotent after connection_lost. A
        # closing transport with an empty queue already scheduled that callback
        # or finished it. Abort only an active or still-draining transport.
        if not closing or (callable(buffered) and buffered() > 0):
            if callable(abort):
                abort()
            else:
                self.writer.close()
        # Abort synchronously disables future transport writes and clears its
        # queue. Clear the tty queue in the same event-loop turn, before the
        # scheduled pyserial-asyncio loss callback can flush and close it.
        try:
            self._serial.reset_output_buffer()
        except (OSError, serial.SerialException, termios.error, AttributeError):
            pass
        closed = asyncio.create_task(self.writer.wait_closed())
        try:
            await asyncio.wait_for(asyncio.shield(closed), CLEANUP_TIMEOUT_S)
        except (asyncio.TimeoutError, OSError, serial.SerialException):
            # The event-loop transport normally closes the FD in its loss
            # callback. This fallback covers partially created transports.
            await _close_unadopted(self._serial)
            if not closed.done():
                closed.cancel()
                try:
                    await _await_reaped(closed)
                except asyncio.CancelledError:
                    pass
            else:
                try:
                    closed.result()
                except BaseException:
                    pass


async def _close_unadopted(port: serial.Serial) -> None:
    """Dispose of a probe handle away from the event loop and reap its close."""
    task = asyncio.create_task(asyncio.to_thread(discard_and_close, port))
    await _await_reaped(task)


async def close_session(session: Session) -> None:
    await session.close()


__all__ = [
    "CLEANUP_TIMEOUT_S",
    "ProbeResult",
    "Session",
    "SessionSerial",
    "close_session",
    "discard_and_close",
    "discard_serial_output",
    "release_linux_exclusive",
]
