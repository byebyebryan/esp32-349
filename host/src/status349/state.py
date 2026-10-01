"""Host-side state model: active legacy cards and retained grouped cards.

Every change bumps `rev`; `snapshot()` produces the bounded legacy `sync`,
while `card_snapshot()` preserves the newest active or retained cards for
chunked sync.
"""

from __future__ import annotations

import time

from . import proto

RETAINED_LIMIT = 32


class StateModel:
    def __init__(self, max_visible: int = 3, cache_limit: int = 32, retention_s: int = 600) -> None:
        self.max_visible = max(0, int(max_visible))
        self.cache_limit = max(0, min(32, int(cache_limit)))
        self.retention_s = int(retention_s)
        self.rev = 0
        self.clock: dict | None = None
        self.media: dict | None = None
        self.dashboard: dict = proto.dashboard_payload(None)
        # Keep the original active-only view for old firmware. Grouped-capable
        # firmware receives the separate bounded retained collection.
        self.notifs: dict[int, dict] = {}
        self.retained_notifs: dict[int, dict] = {}
        self.retained_received_mono: dict[int, float] = {}
        self.retained_history_rev: dict[int, int] = {}
        self._history_revision = 0
        self.zones: list[dict] = []

    def snapshot(self) -> dict:
        notifs = []
        for source in self.notifs.values():
            message = dict(source)
            message.pop("history", None)
            if isinstance(message.get("body"), str):
                message["body"] = proto.clip_utf8_ellipsis(
                    proto.display_text(message["body"]), proto.NOTIFICATION_BODY_LEGACY_BYTES
                )
            notifs.append(message)
        overflow = max(0, len(notifs) - self.max_visible)
        if overflow and self.max_visible:
            notifs = notifs[-self.max_visible:]
        elif overflow:
            notifs = []

        snapshot = {
            "t": "sync",
            "rev": self.rev,
            "bar": {"t": "bar", "rev": self.rev, "zones": self.zones},
            "clock": self.clock,
            "media": self.media,
            "notifs": notifs,
            "notifs_overflow": overflow,
        }

        # The device has one fixed 8192-byte input line. Notification strings
        # are individually bounded, but JSON escaping can expand them, so trim
        # the oldest cards until the actual encoded snapshot fits.
        def fits() -> bool:
            try:
                proto.encode(snapshot)
            except ValueError:
                return False
            return True

        while snapshot["notifs"] and not fits():
            snapshot["notifs"].pop(0)
            snapshot["notifs_overflow"] += 1
        if not fits():
            raise ValueError("sync message exceeds the device line limit after notification trimming")
        return snapshot

    def card_snapshot(self, device_capacity: int | None = None, *, retained: bool = False) -> dict:
        """Return full metadata and newest active or retained cards."""
        capacity = self.cache_limit
        if device_capacity is not None:
            capacity = min(capacity, max(0, int(device_capacity)))

        collection = self.retained_notifs if retained else self.notifs
        all_notifs = list(collection.values())
        if capacity:
            notifs = all_notifs[-capacity:]
        else:
            notifs = []

        return {
            "rev": self.rev,
            "bar": proto.bar(self.zones, self.rev),
            "clock": self.clock,
            "media": self.media,
            "dashboard": self.dashboard,
            "notifs": notifs,
            "limit": capacity,
            "overflow": len(all_notifs) - len(notifs),
        }

    def cached_notification_ids(self, device_capacity: int | None = None, *, retained: bool = False) -> set[int]:
        """Return IDs selected by ``card_snapshot`` without building its envelope."""
        capacity = self.cache_limit
        if device_capacity is not None:
            capacity = min(capacity, max(0, int(device_capacity)))
        collection = self.retained_notifs if retained else self.notifs
        notifs = list(collection.values())
        if capacity:
            notifs = notifs[-capacity:]
        else:
            notifs = []
        return {int(message["id"]) for message in notifs}

    def set_clock(self, epoch: int, offset: int) -> bool:
        if self.clock is not None and self.clock["epoch"] == int(epoch) and self.clock["offset"] == int(offset):
            return False
        self.clock = {"epoch": int(epoch), "offset": int(offset)}
        self.rev += 1
        return True

    def set_media(self, message: dict | None) -> bool:
        if message == self.media:
            return False
        self.media = message
        self.rev += 1
        return True

    def set_dashboard(self, payload: object) -> bool:
        dashboard = proto.dashboard_payload(payload)
        if dashboard == self.dashboard:
            return False
        self.dashboard = dashboard
        self.rev += 1
        return True

    def set_zones(self, zones: list[dict]) -> bool:
        if zones == self.zones:
            return False
        self.zones = zones
        self.rev += 1
        return True

    def add_notification(self, message: dict) -> bool:
        nid = int(message["id"])
        if self.notifs.get(nid) == message:
            return False
        self.notifs[nid] = message
        self.rev += 1
        return True

    def retain_notification(
        self,
        message: dict,
        *,
        receipt_mono: float | None = None,
        bump_rev: bool = True,
    ) -> tuple[bool, list[int]]:
        """Record an accepted arrival/replacement and evict oldest past 32."""
        nid = int(message["id"])
        history_revision = self._allocate_history_revision()
        self.retained_notifs.pop(nid, None)
        self.retained_received_mono.pop(nid, None)
        self.retained_history_rev.pop(nid, None)
        self.retained_notifs[nid] = message
        self.retained_received_mono[nid] = time.monotonic() if receipt_mono is None else float(receipt_mono)
        self.retained_history_rev[nid] = history_revision
        evicted: list[int] = []
        while len(self.retained_notifs) > RETAINED_LIMIT:
            evicted_id = next(iter(self.retained_notifs))
            self.retained_notifs.pop(evicted_id)
            self.retained_received_mono.pop(evicted_id, None)
            self.retained_history_rev.pop(evicted_id, None)
            evicted.append(evicted_id)
        if bump_rev:
            self.rev += 1
        return True, evicted

    def _allocate_history_revision(self) -> int:
        if self._history_revision >= proto.IDENTITY_MAX:
            raise OverflowError("notification history revision space exhausted")
        self._history_revision += 1
        return self._history_revision

    def history_metadata(self, nid: int, now_mono: float | None = None) -> dict | None:
        """Build fresh age metadata for a live retained record."""
        nid = int(nid)
        receipt = self.retained_received_mono.get(nid)
        revision = self.retained_history_rev.get(nid)
        if receipt is None or revision is None:
            return None
        now = time.monotonic() if now_mono is None else float(now_mono)
        retention_ms = self.retention_s * 1000
        age_ms = max(0, int((now - receipt) * 1000))
        if age_ms >= retention_ms:
            return None
        return {
            "rev": revision,
            "age_ms": min(proto.IDENTITY_MAX, age_ms),
            "remaining_ms": min(proto.IDENTITY_MAX, retention_ms - age_ms),
        }

    def expire_retained(
        self,
        now_mono: float | None = None,
        *,
        exclude_ids: set[int] | None = None,
    ) -> list[int]:
        """Remove records past their configured age, including action owners."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        exclude_ids = exclude_ids or set()
        expired = [
            nid for nid, receipt in self.retained_received_mono.items()
            if nid not in exclude_ids and (now - receipt) * 1000 >= self.retention_s * 1000
        ]
        for nid in expired:
            self.retained_notifs.pop(nid, None)
            self.retained_received_mono.pop(nid, None)
            self.retained_history_rev.pop(nid, None)
            self.notifs.pop(nid, None)
        if expired:
            self.rev += 1
        return expired

    def close_active_notification(self, nid: int) -> bool:
        """Remove a record only from the legacy active projection."""
        if self.notifs.pop(int(nid), None) is None:
            return False
        self.rev += 1
        return True

    def close_notification(self, nid: int) -> bool:
        nid = int(nid)
        active = self.notifs.pop(nid, None) is not None
        retained = self.retained_notifs.pop(nid, None) is not None
        self.retained_received_mono.pop(nid, None)
        self.retained_history_rev.pop(nid, None)
        if not active and not retained:
            return False
        self.rev += 1
        return True
