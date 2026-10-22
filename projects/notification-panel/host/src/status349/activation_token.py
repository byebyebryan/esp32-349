"""Obtain a compositor-issued token for an explicit device Open on Niri.

Niri's supported IPC spawn operation gives a short-lived relay its activation
token. The relay sends it over a private one-shot Unix socket; it starts no UI.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import sys
import tempfile

TOKEN_MAX = 4096


def _valid_token(token):
    return isinstance(token, str) and bool(token) and "\0" not in token and len(token.encode()) <= TOKEN_MAX


async def niri_activation_token(*, executable="niri", timeout_s=1.5):
    """Mint one token using existing Niri IPC; never change focus directly."""
    loop = asyncio.get_running_loop()
    result = loop.create_future()
    nonce = secrets.token_hex(24)
    tasks = set()
    process = None
    runtime = os.environ.get("XDG_RUNTIME_DIR") or None
    with tempfile.TemporaryDirectory(prefix="349-act-", dir=runtime) as directory:
        path = str(Path(directory) / "s")

        async def receive(reader, writer):
            try:
                peer = writer.get_extra_info("socket")
                _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    return
                async with asyncio.timeout(1):
                    data = await reader.readline()
                if len(data) > 8192:
                    return
                payload = json.loads(data)
                if not isinstance(payload, dict) or not isinstance(payload.get("nonce"), str):
                    return
                if not secrets.compare_digest(payload["nonce"], nonce) or result.done():
                    return
                token = payload.get("token")
                if _valid_token(token):
                    result.set_result(token)
                else:
                    result.set_exception(RuntimeError("Niri did not supply an activation token"))
            except (OSError, ValueError, TypeError, TimeoutError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

        def connected(reader, writer):
            task = asyncio.create_task(receive(reader, writer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        server = await asyncio.start_unix_server(connected, path=path, limit=8192)
        os.chmod(path, 0o600)
        try:
            async with asyncio.timeout(timeout_s):
                process = await asyncio.create_subprocess_exec(
                    executable, "msg", "action", "spawn", "--", sys.executable,
                    "-m", "status349.activation_token", "--relay", path, nonce,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                if await process.wait() != 0:
                    raise RuntimeError("Niri activation handoff unavailable")
                return await result
        finally:
            server.close()
            await server.wait_closed()
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            for task in tuple(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not result.done():
                result.cancel()
            elif not result.cancelled():
                result.exception()


async def session_activation_token():
    if not os.environ.get("NIRI_SOCKET"):
        return None
    return await niri_activation_token()


def relay(path, nonce):
    payload = {"nonce": nonce, "token": os.environ.get("XDG_ACTIVATION_TOKEN")}
    data = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    if len(data) > 8192:
        raise ValueError("activation handoff exceeds bound")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1)
        client.connect(path)
        client.sendall(data)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--relay":
        raise SystemExit("usage: python -m status349.activation_token --relay SOCKET NONCE")
    relay(sys.argv[2], sys.argv[3])
