"""Persistent host binding for a single 349 USB Serial/JTAG device."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1
USB_VID = 0x303A
USB_PID = 0x1001


class PairingError(RuntimeError):
    """The saved device binding is invalid or could not be committed."""


@dataclass(frozen=True)
class UsbIdentity:
    """Stable USB identity. The serial is opaque and is never normalized."""

    serial: str
    vid: int = USB_VID
    pid: int = USB_PID

    def __post_init__(self) -> None:
        if not isinstance(self.serial, str) or not self.serial.strip():
            raise ValueError("USB serial must be a nonempty string")
        if type(self.vid) is not int or self.vid != USB_VID:
            raise ValueError(f"USB VID must be {USB_VID:04x}")
        if type(self.pid) is not int or self.pid != USB_PID:
            raise ValueError(f"USB PID must be {USB_PID:04x}")

    def as_record(self) -> dict[str, object]:
        return {
            "version": SCHEMA_VERSION,
            "vid": f"{self.vid:04x}",
            "pid": f"{self.pid:04x}",
            "serial": self.serial,
        }


def pairing_path() -> Path:
    """Return the XDG state path, falling back to the standard home path."""
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "349d" / "device.json"


def _parse_record(value: object) -> UsbIdentity:
    if not isinstance(value, dict):
        raise PairingError("pairing record must be a JSON object")
    expected = {"version", "vid", "pid", "serial"}
    if set(value) != expected:
        raise PairingError("pairing record must contain only version, vid, pid, and serial")
    version = value.get("version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise PairingError(f"unsupported pairing record version: {version!r}")
    if value.get("vid") != "303a" or value.get("pid") != "1001":
        raise PairingError("pairing record is not for USB device 303a:1001")
    serial_number = value.get("serial")
    if not isinstance(serial_number, str) or not serial_number.strip():
        raise PairingError("pairing record has an empty or invalid USB serial")
    return UsbIdentity(serial=serial_number)


class PairingStore:
    """Read and atomically replace the version-1 USB pairing record."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path is not None else pairing_path()

    def load(self) -> UsbIdentity | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError as exc:
            raise PairingError(f"invalid pairing record {self.path}: {exc}") from exc
        except OSError as exc:
            raise PairingError(f"cannot read pairing record {self.path}: {exc}") from exc
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PairingError(f"invalid pairing record {self.path}: {exc}") from exc
        return _parse_record(record)

    def save(
        self,
        identity: UsbIdentity,
        before_commit: Callable[[], object] | None = None,
    ) -> Path:
        """Write a same-directory temporary file and atomically replace state.

        ``before_commit`` runs after the temporary record is fully written and
        immediately before replacement. Callers can use it for a thread-safe
        cancellation or target-generation guard; raising (or returning
        ``False``) leaves the previous binding intact.
        """
        if not isinstance(identity, UsbIdentity):
            raise TypeError("identity must be a UsbIdentity")
        directory = self.path.parent
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise PairingError(f"cannot create pairing directory {directory}: {exc}") from exc

        payload = json.dumps(identity.as_record(), separators=(",", ":"), sort_keys=True) + "\n"
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if before_commit is not None and before_commit() is False:
                raise PairingError("pairing commit was rejected by its generation guard")
            os.replace(temporary, self.path)
            temporary = None
        except PairingError:
            raise
        except OSError as exc:
            raise PairingError(f"cannot save pairing record {self.path}: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        return self.path


__all__ = [
    "PairingError",
    "PairingStore",
    "SCHEMA_VERSION",
    "USB_PID",
    "USB_VID",
    "UsbIdentity",
    "pairing_path",
]
