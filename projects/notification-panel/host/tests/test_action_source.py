"""Action identity is independent of popup deadlines and display text."""

import asyncio
import time

from dbus_next import Message, MessageType

from status349.config import NotificationsConfig
from status349.sources.notifications import (
    ASSOCIATION_LIMIT, NOTIFICATIONS_NAME, PENDING_REPLY_LIMIT,
    NotificationSource, parse_open_metadata,
)


def call(serial=1, replaces=0, actions=None, text="we’ve 東京", expire=10):
    return Message(
        destination=NOTIFICATIONS_NAME, path="/org/freedesktop/Notifications",
        interface=NOTIFICATIONS_NAME, member="Notify", signature="susssasa{sv}i",
        body=["Test", replaces, "", "Title", text,
              ["default", "Open"] if actions is None else actions, {}, expire],
        sender=":1.50", serial=serial,
    )


def reply(serial, desktop_id=9, sender=":1.40"):
    return Message(message_type=MessageType.METHOD_RETURN, destination=":1.50",
                   sender=sender, reply_serial=serial, signature="u", body=[desktop_id])


def closed(reason=1, desktop_id=9):
    return Message(message_type=MessageType.SIGNAL, sender=":1.40",
                   path="/org/freedesktop/Notifications", interface=NOTIFICATIONS_NAME,
                   member="NotificationClosed", signature="uu", body=[desktop_id, reason])


async def noop(_):
    pass


def source():
    result = NotificationSource(NotificationsConfig(), noop, noop, grouped_mode=lambda: True)
    result._server_owner, result._server_pid = ":1.40", 40
    return result


def test_action_metadata_preserves_raw_text_and_requires_unique_default():
    expected = parse_open_metadata(call().body)
    assert expected == {"app": "Test", "summary": "Title", "body": "we’ve 東京", "default_label": "Open"}
    for actions in ([], ["reply", "Reply"], ["default"], ["default", "A", "default", "B"],
                    ["default", 42], ["reply", "x"] * 65):
        assert parse_open_metadata(call(actions=actions).body) is None
    assert parse_open_metadata(call(text="x" * 8192).body) is None


def test_confirmed_reply_and_owner_are_required_and_popup_expiry_keeps_action():
    async def scenario():
        src = source()
        await src._handle(call())
        assert src.action_candidate(1) is None
        await src._handle(reply(1))
        candidate = src.action_candidate(1)
        assert candidate["desktop_id"] == 9 and candidate["owner"] == ":1.40" and candidate["pid"] == 40
        src._expiry_deadlines[1] = time.monotonic() - 1
        await src._expire_due()
        assert src.action_candidate(1) == candidate
        await src._handle(closed())
        assert src.action_candidate(1) is None
        assert src._daemon_to_local[9] == 1  # Retained replacement correlation.

        foreign = source()
        await foreign._handle(call())
        await foreign._handle(reply(1, sender=":1.99"))
        assert foreign.action_candidate(1) is None
    asyncio.run(scenario())


def test_identical_replacement_revokes_until_its_own_reply_not_an_older_reply():
    async def scenario():
        src = source()
        await src._handle(call())
        await src._handle(reply(1))
        old = src.action_candidate(1)
        await src._handle(call(serial=2, replaces=9))
        assert src.action_candidate(1) is None
        await src._handle(call(serial=3, replaces=9))
        await src._handle(reply(2))
        assert src.action_candidate(1) is None
        await src._handle(reply(3))
        assert src.action_candidate(1)["version"] > old["version"]
    asyncio.run(scenario())


def test_local_hide_replacement_and_eviction_release_action_identity():
    async def scenario():
        src = source()
        await src._handle(call())
        await src._handle(reply(1))
        src.hide_locally(1)
        assert src.action_candidate(1) is None
        await src._handle(call(serial=2, replaces=9))
        await src._handle(reply(2))
        assert src.action_candidate(1) is not None
        src.forget(1)
        assert src.action_candidate(1) is None and not src._open_info
    asyncio.run(scenario())


def test_closed_id_and_late_reply_cannot_restore_previous_action():
    async def scenario():
        src = source()
        await src._handle(call())
        await src._handle(reply(1))
        await src._handle(closed(reason=2))
        await src._handle(call(serial=2))
        await src._handle(reply(2))
        assert src.action_candidate(1) is None
        assert src.action_candidate(2)["desktop_id"] == 9
        await src._handle(reply(1))
        assert src.action_candidate(1) is None
    asyncio.run(scenario())


def test_action_metadata_and_pending_versions_remain_bounded():
    async def scenario():
        src = source()
        for serial in range(1, 101):
            await src._handle(call(serial=serial))
        assert len(src._open_info) <= ASSOCIATION_LIMIT
        assert len(src._open_reply_versions) <= PENDING_REPLY_LIMIT
        assert set(src._open_reply_versions) <= set(src._by_serial)
        await src._teardown()
        assert not src._open_info and not src._open_reply_versions
        assert src._server_owner is None and src.actions_changed.is_set()
    asyncio.run(scenario())


def test_notification_owner_change_archives_without_reusing_associations():
    async def scenario():
        src = source()
        archived = []
        async def archive(ids):
            archived.extend(ids)
        src._on_monitor_reset = archive
        await src._handle(call())
        await src._handle(reply(1))
        await src._handle(Message(
            message_type=MessageType.SIGNAL, sender="org.freedesktop.DBus",
            path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
            member="NameOwnerChanged", signature="sss",
            body=[NOTIFICATIONS_NAME, ":1.40", ":1.99"],
        ))
        assert archived == [1]
        assert not src.action_candidates() and not src._daemon_to_local
    asyncio.run(scenario())
