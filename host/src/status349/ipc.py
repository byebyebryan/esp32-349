"""Unix-socket control interface and sticky pause flag for 349d.

The socket lives in $XDG_RUNTIME_DIR (falling back to /tmp for the current
user), so `349ctl` can talk to the daemon without touching the serial port.
The pause flag is a file: `349ctl pause` creates it even when the daemon is
down, so a `Restart=always` unit cannot grab the tty mid-flash.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import logging
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger("349d.ipc")


def _runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) if runtime else Path(f"/tmp")


def socket_path() -> Path:
    return _runtime_dir() / "349d.sock"


def lock_path() -> Path:
    return _runtime_dir() / "349d.lock"


def pause_path() -> Path:
    return _runtime_dir() / "349d.paused"


class IpcServer:
    def __init__(self, handler: Callable[[dict], Awaitable[dict]]):
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None
        self._lock_fd: int | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._owned_socket_path: Path | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        if self._lock_fd is not None:
            raise RuntimeError("IPC startup is already in progress")

        self._lock_fd = self._acquire_lock()
        path = socket_path()
        self._owned_socket_path = path
        try:
            await self._remove_stale_socket(path)
            self._server = await asyncio.start_unix_server(self._serve, path=str(path))
            identity = self._socket_identity_at(path)
            if identity is None:
                raise RuntimeError(f"349d socket disappeared during startup: {path}")
            self._socket_identity = identity
            os.chmod(path, 0o600)
            log.info("ipc listening on %s", path)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        server, self._server = self._server, None
        try:
            if server is not None:
                server.close()
                await server.wait_closed()
        finally:
            try:
                self._unlink_owned_socket()
            finally:
                self._release_lock()

    @staticmethod
    def _socket_identity_at(path: Path) -> tuple[int, int] | None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISSOCK(info.st_mode):
            raise RuntimeError(f"refusing to replace non-socket at {path}")
        return info.st_dev, info.st_ino

    def _acquire_lock(self) -> int:
        path = lock_path()
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError(f"refusing to use non-regular IPC lock file: {path}")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"349d is already running in runtime directory {path.parent}") from exc
            return fd
        except BaseException:
            os.close(fd)
            raise

    async def _remove_stale_socket(self, path: Path) -> None:
        identity = self._socket_identity_at(path)
        if identity is None:
            return

        try:
            _, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), timeout=0.5)
        except (ConnectionRefusedError, FileNotFoundError):
            pass
        except TimeoutError as exc:
            raise RuntimeError(f"cannot determine whether an existing 349d socket is live: {path}") from exc
        except OSError as exc:
            if exc.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                raise RuntimeError(f"cannot probe existing 349d socket: {path}") from exc
        else:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            raise RuntimeError(f"a 349d listener already owns {path}")

        current = self._socket_identity_at(path)
        if current is None:
            return
        if current != identity:
            raise RuntimeError(f"349d socket changed during stale-socket recovery: {path}")
        path.unlink()

    def _unlink_owned_socket(self) -> None:
        identity, self._socket_identity = self._socket_identity, None
        path, self._owned_socket_path = self._owned_socket_path, None
        if identity is None or path is None:
            return
        try:
            current = self._socket_identity_at(path)
            if current == identity:
                path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, RuntimeError) as exc:
            log.warning("could not remove owned IPC socket %s: %s", path, exc)

    def _release_lock(self) -> None:
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            os.close(fd)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request = json.loads(line.decode() or "{}")
            response = await self._handler(request)
        except Exception as exc:  # noqa: BLE001 - report every failure to the client
            response = {"ok": False, "error": str(exc)}
        try:
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
