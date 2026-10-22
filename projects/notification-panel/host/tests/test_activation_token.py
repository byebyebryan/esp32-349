import asyncio
from pathlib import Path
import tempfile
import socket

import pytest

from status349.activation_token import niri_activation_token, session_activation_token


def fake_niri(tmp_path, token):
    executable = tmp_path / "niri"
    executable.write_text(
        "#!/usr/bin/env python3\nimport os,subprocess,sys\n"
        "assert sys.argv[1:5] == ['msg','action','spawn','--']\n"
        f"env = {{**os.environ, 'XDG_ACTIVATION_TOKEN': {token!r}}}\n"
        "raise SystemExit(subprocess.call(sys.argv[5:], env=env))\n"
    )
    executable.chmod(0o700)
    return executable


@pytest.fixture
def runtime(monkeypatch):
    # A test-owned directory avoids global /tmp races and AF_UNIX path limits.
    with tempfile.TemporaryDirectory(prefix="349-test-") as directory:
        monkeypatch.setenv("XDG_RUNTIME_DIR", directory)
        yield Path(directory)
        assert not list(Path(directory).iterdir())


def test_token_handoff_uses_private_socket_and_cleans_up(tmp_path, runtime):
    executable = fake_niri(tmp_path, "compositor-issued-token")
    assert asyncio.run(niri_activation_token(executable=str(executable))) == "compositor-issued-token"
    assert not list(runtime.iterdir())


def test_missing_token_fails_without_an_action(tmp_path, runtime):
    executable = fake_niri(tmp_path, "")
    with pytest.raises(RuntimeError, match="did not supply"):
        asyncio.run(niri_activation_token(executable=str(executable)))


def test_handoff_timeout_is_bounded_and_cleans_up(tmp_path, runtime):
    executable = tmp_path / "niri"
    executable.write_text("#!/usr/bin/env python3\n")
    executable.chmod(0o700)
    with pytest.raises(TimeoutError):
        asyncio.run(niri_activation_token(executable=str(executable), timeout_s=0.05))
    assert not list(runtime.iterdir())


def test_wrong_nonce_cannot_supply_an_activation_token(tmp_path, runtime):
    executable = tmp_path / "niri"
    executable.write_text(
        "#!/usr/bin/env python3\nimport socket,sys,json\n"
        "s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[-2])\n"
        "s.sendall((json.dumps({'nonce':'wrong','token':'forged'})+'\\n').encode());s.close()\n"
    )
    executable.chmod(0o700)
    with pytest.raises(TimeoutError):
        asyncio.run(niri_activation_token(executable=str(executable), timeout_s=0.2))


def test_other_compositors_do_not_launch_a_niri_helper(monkeypatch):
    monkeypatch.delenv("NIRI_SOCKET", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
    assert asyncio.run(session_activation_token()) is None


def test_notifier_context_overrides_stale_service_environment(runtime, monkeypatch):
    path = runtime / "niri.sock"
    captured = []
    async def acquire(**kwargs):
        captured.append(kwargs)
        return "owner-token"
    monkeypatch.setenv("NIRI_SOCKET", "/stale/previous-session.sock")
    monkeypatch.setattr("status349.activation_token._notifier_environment",
                        lambda pid: {"NIRI_SOCKET": str(path), "XDG_CURRENT_DESKTOP": "niri"} if pid == 123 else {})
    monkeypatch.setattr("status349.activation_token.niri_activation_token", acquire)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        assert asyncio.run(session_activation_token(notifier_pid=123)) == "owner-token"
        assert captured == [{"niri_socket": str(path)}]
    path.unlink()


def test_missing_notifier_socket_never_uses_another_session(monkeypatch):
    monkeypatch.setenv("NIRI_SOCKET", "/some/other-session.sock")
    monkeypatch.setattr("status349.activation_token._notifier_environment",
                        lambda pid: {"XDG_CURRENT_DESKTOP": "niri"})
    with pytest.raises(RuntimeError, match="owner's Niri socket"):
        asyncio.run(session_activation_token(notifier_pid=123))


def test_regular_file_cannot_be_used_as_a_compositor_socket(tmp_path, monkeypatch):
    path = tmp_path / "not-a-socket"
    path.write_text("owned fixture")
    monkeypatch.setattr("status349.activation_token._notifier_environment",
                        lambda pid: {"NIRI_SOCKET": str(path)})
    with pytest.raises(RuntimeError, match="socket is invalid"):
        asyncio.run(session_activation_token(notifier_pid=123))
