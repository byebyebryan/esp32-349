import pytest

from status349 import proto
from status349.state import StateModel


def test_snapshot_shape():
    model = StateModel()
    snapshot = model.snapshot()
    assert snapshot["t"] == "sync"
    assert snapshot["clock"] is None
    assert snapshot["media"] is None
    assert snapshot["notifs"] == []
    assert snapshot["bar"]["zones"] == []
    assert "dashboard" not in snapshot


def test_change_detection_bumps_rev_once():
    model = StateModel()
    zones = [{"id": "clock", "kind": "clock"}]
    assert model.set_zones(zones) is True
    rev = model.rev
    assert model.set_zones(list(zones)) is False
    assert model.rev == rev

    assert model.set_clock(100, 3600) is True
    assert model.set_clock(100, 3600) is False
    assert model.rev == rev + 1

    assert model.set_media({"t": "media", "state": "playing"}) is True
    assert model.set_media({"t": "media", "state": "playing"}) is False
    assert model.rev == rev + 2


def test_snapshot_carries_current_state():
    model = StateModel()
    model.set_zones([{"id": "cpu"}])
    model.set_clock(5, -18000)
    snapshot = model.snapshot()
    assert snapshot["rev"] == model.rev
    assert snapshot["bar"]["zones"] == [{"id": "cpu"}]
    assert snapshot["clock"] == {"epoch": 5, "offset": -18000}


def test_dashboard_metadata_is_sanitized_and_kept_out_of_legacy_sync():
    model = StateModel()
    payload = {
        "cpu": 0.423,
        "mem": float("nan"),
        "network": True,
        "battery": {"level": 0.787, "charging": False},
        "volume": None,
        "bluetooth": 2,
        "interface": "private-host-detail",
    }

    assert model.set_dashboard(payload)
    rev = model.rev
    assert model.set_dashboard({**payload, "cpu": 0.424}) is False
    assert model.rev == rev
    assert model.dashboard == {
        "cpu": 0.42,
        "mem": None,
        "network": True,
        "rx_bytes_per_s": None,
        "tx_bytes_per_s": None,
        "battery": {"level": 0.79, "charging": False},
        "volume": None,
        "bluetooth": 2,
    }
    assert "dashboard" not in model.snapshot()
    assert model.card_snapshot()["dashboard"] == model.dashboard


def test_notifications():
    model = StateModel()
    assert model.add_notification({"t": "notify", "id": 1, "summary": "hi"}) is True
    assert model.add_notification({"t": "notify", "id": 1, "summary": "hi"}) is False
    assert [n["id"] for n in model.snapshot()["notifs"]] == [1]
    assert model.close_notification(1) is True
    assert model.close_notification(1) is False
    assert model.snapshot()["notifs"] == []


def test_close_removes_a_card_and_a_later_new_id_can_appear():
    model = StateModel()
    assert model.add_notification({"t": "notify", "id": 1, "summary": "old"})
    assert model.close_notification(1)
    assert model.add_notification({"t": "notify", "id": 2, "summary": "new"})
    assert [notice["id"] for notice in model.snapshot()["notifs"]] == [2]


def test_snapshot_stays_within_device_line_limit_for_escaped_notification_text():
    model = StateModel(max_visible=8)
    for nid in range(8):
        model.add_notification({"t": "notify", "id": nid, "body": "\0" * 159})
    snapshot = model.snapshot()
    assert len(proto.encode(snapshot)) <= proto.LINE_MAX
    assert snapshot["notifs_overflow"] == 8 - len(snapshot["notifs"])


def test_notifications_capped_in_sync():
    model = StateModel(max_visible=2)
    for nid in range(4):
        model.add_notification({"t": "notify", "id": nid})
    snapshot = model.snapshot()
    assert [n["id"] for n in snapshot["notifs"]] == [2, 3]
    assert snapshot["notifs_overflow"] == 2


def test_notifications_capped_to_zero():
    model = StateModel(max_visible=0)
    model.add_notification({"t": "notify", "id": 1})
    snapshot = model.snapshot()
    assert snapshot["notifs"] == []
    assert snapshot["notifs_overflow"] == 1


@pytest.mark.parametrize(
    ("active", "expected_ids", "overflow"),
    [
        (0, [], 0),
        (1, [0], 0),
        (20, list(range(20)), 0),
        (32, list(range(32)), 0),
        (35, list(range(3, 35)), 3),
    ],
)
def test_card_snapshot_selects_newest_cache_and_reports_overflow(active, expected_ids, overflow):
    model = StateModel(cache_limit=32)
    for nid in range(active):
        model.add_notification({"t": "notify", "id": nid})

    snapshot = model.card_snapshot(device_capacity=32)

    assert [message["id"] for message in snapshot["notifs"]] == expected_ids
    assert snapshot["limit"] == 32
    assert snapshot["overflow"] == overflow


def test_card_snapshot_respects_smaller_device_capacity_and_zero_host_limit():
    model = StateModel(cache_limit=20)
    for nid in range(25):
        model.add_notification({"t": "notify", "id": nid})
    limited = model.card_snapshot(4)
    assert [message["id"] for message in limited["notifs"]] == [21, 22, 23, 24]
    assert limited["limit"] == 4

    model.cache_limit = 0
    snapshot = model.card_snapshot(32)
    assert snapshot["notifs"] == []
    assert snapshot["limit"] == 0
    assert snapshot["overflow"] == 25


@pytest.mark.parametrize(
    ("cache_limit", "active", "expected_ids", "overflow"),
    [
        (0, 3, [], 3),
        (8, 12, list(range(4, 12)), 4),
    ],
)
def test_card_snapshot_exposes_configured_limit(cache_limit, active, expected_ids, overflow):
    model = StateModel(cache_limit=cache_limit)
    for nid in range(active):
        model.add_notification({"t": "notify", "id": nid})

    snapshot = model.card_snapshot(device_capacity=32)

    assert snapshot["limit"] == cache_limit
    assert [message["id"] for message in snapshot["notifs"]] == expected_ids
    assert snapshot["overflow"] == overflow


def test_retained_collection_moves_replacements_and_caps_at_32_independent_of_active_view():
    model = StateModel(cache_limit=4)
    for nid in range(32):
        message = {"t": "notify", "id": nid, "summary": f"card {nid}"}
        model.add_notification(message)
        model.retain_notification(message)

    replacement = {"t": "notify", "id": 0, "summary": "updated"}
    changed, evicted = model.retain_notification(replacement)
    assert changed
    assert evicted == []
    assert list(model.retained_notifs)[-1] == 0
    assert 0 in model.notifs

    changed, evicted = model.retain_notification({"t": "notify", "id": 32, "summary": "new"})
    assert changed
    assert evicted == [1]
    assert len(model.retained_notifs) == 32
    assert list(model.retained_notifs)[-4:] == [30, 31, 0, 32]
    assert 1 in model.notifs

    snapshot = model.card_snapshot(4, retained=True)
    assert [card["id"] for card in snapshot["notifs"]] == [30, 31, 0, 32]
    assert snapshot["overflow"] == 28


def test_identical_accepted_replacement_renews_history_revision_and_deadline():
    model = StateModel(retention_s=5)
    message = {"t": "notify", "id": 7, "summary": "same"}
    changed, evicted = model.retain_notification(message, receipt_mono=10.0)
    assert changed and evicted == []
    first_rev = model.retained_history_rev[7]
    first_deadline = model.retained_received_mono[7] + model.retention_s
    model_rev = model.rev
    changed, evicted = model.retain_notification(message, receipt_mono=12.0)
    assert changed and evicted == []
    assert model.rev == model_rev + 1
    assert model.retained_history_rev[7] > first_rev
    assert model.retained_received_mono[7] + model.retention_s > first_deadline
    assert model.history_metadata(7, now_mono=12.25) == {
        "rev": model.retained_history_rev[7], "age_ms": 250, "remaining_ms": 4750,
    }


def test_retention_expiry_is_exact_and_removes_active_text_and_history_metadata():
    model = StateModel(retention_s=3)
    message = {"t": "notify", "id": 7, "summary": "persistent"}
    model.add_notification(message)
    model.retain_notification(message, receipt_mono=10.0)

    assert model.expire_retained(12.999) == []
    assert model.history_metadata(7, now_mono=12.999)["remaining_ms"] == 1
    assert model.expire_retained(13.0) == [7]
    assert 7 not in model.notifs
    assert 7 not in model.retained_notifs
    assert 7 not in model.retained_received_mono
    assert 7 not in model.retained_history_rev


def test_history_sync_keeps_long_text_while_legacy_snapshot_projects_159_bytes():
    model = StateModel(max_visible=1)
    message = proto.notify(7, "app", "sum", "東京" * 150, 1, 0, 1)
    model.add_notification(message)
    model.retain_notification(message, receipt_mono=10.0)

    legacy = model.snapshot()["notifs"][0]["body"]
    history = model.card_snapshot(retained=True)["notifs"][0]["body"]
    assert len(legacy.encode("utf-8")) <= proto.NOTIFICATION_BODY_LEGACY_BYTES
    assert legacy.endswith("…")
    assert len(history.encode("utf-8")) <= proto.NOTIFICATION_BODY_HISTORY_BYTES
    assert history.endswith("…")


def test_history_revision_exhaustion_fails_without_wrapping_or_replacing_live_record():
    model = StateModel()
    original = {"t": "notify", "id": 7, "summary": "kept"}
    model.retain_notification(original, receipt_mono=10.0)
    original_history_revision = model.retained_history_rev[7]
    original_receipt = model.retained_received_mono[7]
    model._history_revision = proto.IDENTITY_MAX

    with pytest.raises(OverflowError, match="history revision space exhausted"):
        model.retain_notification({**original, "summary": "replacement"}, receipt_mono=20.0)

    assert model.retained_notifs[7] == original
    assert model.retained_history_rev[7] == original_history_revision
    assert model.retained_received_mono[7] == original_receipt
