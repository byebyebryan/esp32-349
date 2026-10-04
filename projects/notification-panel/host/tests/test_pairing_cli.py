"""CLI behavior for observation-only USB pairing requests."""

import pytest

from status349 import __main__ as cli


def _status(operation, state="pairing", result=None):
    return {
        "ok": True,
        "pairing": {"state": state, "operation": operation, "result": result},
    }


def test_pair_cli_polls_only_the_started_operation(monkeypatch, capsys, tmp_path):
    requests = []
    statuses = iter([
        _status(41),
        _status(41, "paired", {"operation": 41, "ok": True, "serial": "board-opaque"}),
    ])

    def request(payload, path=None, timeout=5.0):
        requests.append((payload, path, timeout))
        if payload["cmd"] == "pair":
            return {"ok": True, "done": False, "operation": 41}
        return next(statuses)

    monkeypatch.setattr(cli, "ipc_request", request)
    assert cli.main(["--socket", str(tmp_path / "349d.sock"), "pair"]) == 0
    assert capsys.readouterr().out == "paired board-opaque\n"
    assert requests[0][0] == {"cmd": "pair", "replace": False}
    assert [item[0] for item in requests[1:]] == [{"cmd": "status"}, {"cmd": "status"}]
    assert all(item[1] == tmp_path / "349d.sock" for item in requests)


def test_pair_cli_idempotent_done_returns_serial_without_polling(monkeypatch, capsys):
    requests = []

    def request(payload, path=None, timeout=5.0):
        requests.append(payload)
        return {"ok": True, "done": True, "serial": "board-opaque"}

    monkeypatch.setattr(cli, "ipc_request", request)
    assert cli.main(["pair"]) == 0
    assert capsys.readouterr().out == "paired board-opaque\n"
    assert requests == [{"cmd": "pair", "replace": False}]


def test_pair_cli_replace_propagates_replace_and_reports_operation_failure(monkeypatch, capsys):
    requests = []
    statuses = iter([
        _status(78),
        _status(78, "paired", {"operation": 78, "ok": False, "error": "no compatible 349 found"}),
    ])

    def request(payload, path=None, timeout=5.0):
        requests.append(payload)
        if payload["cmd"] == "pair":
            return {"ok": True, "done": False, "operation": 78}
        return next(statuses)

    monkeypatch.setattr(cli, "ipc_request", request)
    assert cli.main(["pair", "--replace", "--wait-timeout", "2"]) == 1
    assert requests[0] == {"cmd": "pair", "replace": True}
    assert "no compatible 349 found" in capsys.readouterr().err


def test_pair_cli_detects_changed_operation_as_daemon_restart(monkeypatch, capsys):
    def request(payload, path=None, timeout=5.0):
        if payload["cmd"] == "pair":
            return {"ok": True, "done": False, "operation": 52}
        return _status(1)

    monkeypatch.setattr(cli, "ipc_request", request)
    assert cli.main(["pair", "--wait-timeout", "1"]) == 1
    assert "daemon restarted" in capsys.readouterr().err


def test_pair_cli_observation_timeout_does_not_cancel_pairing(monkeypatch, capsys):
    requests = []

    def request(payload, path=None, timeout=5.0):
        requests.append(payload)
        if payload["cmd"] == "pair":
            return {"ok": True, "done": False, "operation": 90}
        return _status(90)

    monkeypatch.setattr(cli, "ipc_request", request)
    assert cli.main(["pair", "--wait-timeout", "0.001"]) == 1
    assert all(request["cmd"] != "cancel" for request in requests)
    assert "is still running" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_pair_cli_rejects_invalid_wait_timeout(value):
    with pytest.raises(SystemExit):
        cli.main(["pair", "--wait-timeout", value])


def test_pair_cli_rejects_direct_port_mode(capsys):
    assert cli.main(["--port", "/dev/ttyUSB0", "pair"]) == 1
    assert "requires the daemon" in capsys.readouterr().err
