"""Host-side state model: active legacy cards and retained grouped cards.

Every change bumps `rev`; `snapshot()` produces the bounded legacy `sync`,
while `card_snapshot()` preserves the newest active or retained cards for
chunked sync.
"""

from __future__ import annotations

from . import proto

RETAINED_LIMIT = 32


class StateModel:
    def __init__(self, max_visible: int = 3, cache_limit: int = 32) -> None:
        self.max_visible = max(0, int(max_visible))
        self.cache_limit = max(0, min(32, int(cache_limit)))
        self.rev = 0
        self.clock: dict | None = None
        self.media: dict | None = None
        self.dashboard: dict = proto.dashboard_payload(None)
        # Keep the original active-only view for old firmware. Grouped-capable
        # firmware receives the separate bounded retained collection.
        self.notifs: dict[int, dict] = {}
        self.retained_notifs: dict[int, dict] = {}
        self.zones: list[dict] = []

    def snapshot(self) -> dict:
        notifs = list(self.notifs.values())
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

    def retain_notification(self, message: dict, *, bump_rev: bool = True) -> tuple[bool, list[int]]:
        """Insert or move a record to newest order, evicting oldest past 32."""
        nid = int(message["id"])
        was_newest = bool(self.retained_notifs) and next(reversed(self.retained_notifs)) == nid
        old = self.retained_notifs.pop(nid, None)
        moved = old is not None and (old != message or not was_newest)
        if old == message and not moved:
            # Restore the value so an unchanged newest record remains present.
            self.retained_notifs[nid] = old
            return False, []

        self.retained_notifs[nid] = message
        evicted: list[int] = []
        while len(self.retained_notifs) > RETAINED_LIMIT:
            evicted_id = next(iter(self.retained_notifs))
            self.retained_notifs.pop(evicted_id)
            evicted.append(evicted_id)
        if bump_rev:
            self.rev += 1
        return True, evicted

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
        if not active and not retained:
            return False
        self.rev += 1
        return True
