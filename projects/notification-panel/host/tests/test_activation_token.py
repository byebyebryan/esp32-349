import asyncio
from pathlib import Path
import tempfile

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
    assert asyncio.run(session_activation_token()) is None
