import json
import os
from pathlib import Path

import pytest

from status349.pairing import PairingError, PairingStore, UsbIdentity, pairing_path


def test_pairing_store_roundtrips_version_one_record_and_uses_xdg_state_home(tmp_path, monkeypatch):
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    store = PairingStore()
    identity = UsbIdentity("349-opaque-serial")

    assert pairing_path() == state_home / "349d" / "device.json"
    assert store.load() is None
    assert store.save(identity) == pairing_path()
    assert store.load() == identity
    assert json.loads(store.path.read_text()) == {
        "version": 1,
        "vid": "303a",
        "pid": "1001",
        "serial": "349-opaque-serial",
    }


def test_pairing_path_falls_back_to_local_state(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert pairing_path() == tmp_path / ".local" / "state" / "349d" / "device.json"


@pytest.mark.parametrize(
    "record",
    [
        b"{truncated",
        b"\xff",
        json.dumps({"version": True, "vid": "303a", "pid": "1001", "serial": "x"}).encode(),
        json.dumps({"version": 2, "vid": "303a", "pid": "1001", "serial": "x"}).encode(),
        json.dumps({"version": 1, "vid": "303A", "pid": "1001", "serial": "x"}).encode(),
        json.dumps({"version": 1, "vid": "303a", "pid": "1001", "serial": "  "}).encode(),
        json.dumps({"version": 1, "vid": "303a", "pid": "1001", "serial": "x", "extra": 1}).encode(),
        b"[]",
    ],
)
def test_invalid_or_unsupported_pairing_records_raise_useful_errors(tmp_path, record):
    path = tmp_path / "device.json"
    path.write_bytes(record)

    with pytest.raises(PairingError, match="pairing record"):
        PairingStore(path).load()


def test_failed_atomic_replace_keeps_old_record_and_removes_temporary_file(tmp_path, monkeypatch):
    store = PairingStore(tmp_path / "349d" / "device.json")
    old_identity = UsbIdentity("old-serial")
    store.save(old_identity)
    old_contents = store.path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(PairingError, match="cannot save pairing record"):
        store.save(UsbIdentity("replacement-serial"))

    assert store.path.read_bytes() == old_contents
    assert store.load() == old_identity
    assert list(store.path.parent.glob(".device.json.*.tmp")) == []


def test_before_commit_guard_runs_after_temp_write_and_preserves_old_record(tmp_path):
    store = PairingStore(tmp_path / "device.json")
    existing = UsbIdentity("bound-device")
    store.save(existing)
    seen = []

    def reject_commit():
        seen.append(list(tmp_path.glob(".device.json.*.tmp")))
        return False

    with pytest.raises(PairingError, match="generation guard"):
        store.save(UsbIdentity("other-device"), before_commit=reject_commit)

    assert len(seen) == 1 and len(seen[0]) == 1
    assert store.load() == existing
    assert list(tmp_path.glob(".device.json.*.tmp")) == []


def test_usb_identity_rejects_non_target_devices_and_invalid_serials():
    for serial_number in ("", "\t  ", None, 7):
        with pytest.raises(ValueError, match="serial"):
            UsbIdentity(serial_number)
    with pytest.raises(ValueError, match="VID"):
        UsbIdentity("device", vid=True)
    with pytest.raises(ValueError, match="PID"):
        UsbIdentity("device", pid=0x1002)
