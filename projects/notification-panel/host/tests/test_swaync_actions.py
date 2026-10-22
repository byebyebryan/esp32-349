import asyncio
from types import SimpleNamespace

import pytest
from dbus_next import MessageType, Variant

from status349.actions import BridgeError, NotificationActionManager
from status349.config import load_config
from status349.swaync_actions import ConfiguredBridgeClient, SwayncBridgeClient


class Source:
    def __init__(self):
        self.owner, self.pid = ":1.7", 123
        self.actions_changed = asyncio.Event()
        self.candidates = {}
        self.snapshots = {}
        self.calls = []
        self.invoked = []
        self.gate = None
        self.add(10, 101, "older")
        self.add(11, 102, "newer")

    def add(self, local_id, desktop_id, text):
        self.candidates[local_id] = {
            "id": local_id, "version": 1, "desktop_id": desktop_id, "owner": self.owner, "pid": self.pid,
            "expected": {"app": "kitty", "summary": text, "body": "body", "default_label": "Open"},
        }
        self.snapshots[desktop_id] = {"id": desktop_id, "revision": "revision-" + str(desktop_id),
                                      "app": "kitty", "summary": text, "body": "body", "actions": ["default", "Open"]}

    def identity_status(self):
        return {"state": "ready", "owner": self.owner, "pid": self.pid}

    def action_candidate(self, local_id):
        return self.candidates.get(local_id)

    def action_candidates(self):
        return self.candidates

    async def action_provider_call(self, owner, member, signature, body):
        self.calls.append((owner, member, signature, list(body)))
        if member == "GetNotification":
            snapshot = self.snapshots.get(body[0], {})
            values = {k: Variant("u" if k == "id" else "as" if k == "actions" else "s", v)
                      for k, v in snapshot.items()}
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, sender=self.owner,
                                   signature="a{sv}", body=[values])
        if self.gate:
            await self.gate.wait()
        snapshot = self.snapshots.get(body[0])
        result = "unavailable" if snapshot is None else "stale" if snapshot["revision"] != body[1] else "dispatched"
        if result == "dispatched":
            self.invoked.append((body[0], body[2]))
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, sender=self.owner, signature="s", body=[result])


def manager_for(source):
    async def changed(_nid, _state):
        pass
    return NotificationActionManager(source, SwayncBridgeClient(source), enabled=lambda: True,
                                     context=lambda: (5, 9), retained_ids=lambda: list(source.candidates), on_change=changed)


def test_opt_in_config_and_default_off(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[notifications]\ndevice_open = 'swaync'\n")
    assert load_config(str(p)).notifications.device_open == "swaync"
    p.write_text("")
    assert load_config(str(p)).notifications.device_open == "off"


def test_older_retained_card_dispatches_exact_id_and_duplicate_request_once():
    async def scenario():
        source = Source()
        manager = manager_for(source)
        await manager.reconcile_once()
        revision = manager.open_for(10)["rev"]
        assert manager.open_for(10)["state"] == "ready"
        assert await manager.activate(10, revision, 1, 5, 9) == "dispatched"
        assert await manager.activate(10, revision, 1, 5, 9) == "dispatched"
        assert source.invoked == [(101, "default")]
        assert manager.open_for(11)["state"] == "ready"
        assert not any("Latest" in member for _, member, _, _ in source.calls)
    asyncio.run(scenario())


def test_unseen_identical_replacement_is_rejected_by_server_revision():
    async def scenario():
        source = Source()
        manager = manager_for(source)
        await manager.reconcile_once()
        revision = manager.open_for(10)["rev"]
        source.snapshots[101]["revision"] = "new-identical-notify"
        assert await manager.activate(10, revision, 1, 5, 9) == "stale"
        assert not source.invoked
        source.candidates[10]["version"] += 1
        await manager.reconcile_once()
        new_revision = manager.open_for(10)["rev"]
        assert new_revision > revision
        assert await manager.activate(10, new_revision, 2, 5, 9) == "dispatched"
        assert source.invoked == [(101, "default")]
    asyncio.run(scenario())


def test_changed_owner_or_closed_card_cannot_inherit_binding():
    async def scenario():
        source = Source()
        manager = manager_for(source)
        await manager.reconcile_once()
        revision = manager.open_for(10)["rev"]
        source.owner, source.pid = ":1.99", 456
        assert await manager.activate(10, revision, 1, 5, 9) in {"stale", "unknown"}
        assert not source.invoked
        assert not any(member == "InvokeAction" for _, member, _, _ in source.calls)
        source.candidates.pop(10)
        await manager.reconcile_once()
        assert await manager.activate(10, revision, 2, 5, 9) == "unavailable"
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["missing-default", "wrong-body", "oversized", "missing"])
def test_binding_requires_exact_bounded_default_action_metadata(mutation):
    async def scenario():
        source = Source()
        if mutation == "missing-default":
            source.snapshots[101]["actions"] = ["other", "Other"]
        elif mutation == "wrong-body":
            source.snapshots[101]["body"] = "different"
        elif mutation == "oversized":
            source.snapshots[101]["body"] = "x" * 8193
        else:
            source.snapshots.pop(101)
        manager = manager_for(source)
        await manager.reconcile_once()
        assert manager.open_for(10)["state"] == "unavailable"
        assert not source.invoked
    asyncio.run(scenario())


def test_activation_timeout_reserves_request_and_is_never_retried(monkeypatch):
    async def scenario():
        source = Source()
        manager = manager_for(source)
        await manager.reconcile_once()
        revision = manager.open_for(10)["rev"]
        source.gate = asyncio.Event()
        monkeypatch.setattr("status349.swaync_actions.BRIDGE_TIMEOUT_S", 0.03)
        assert await manager.activate(10, revision, 1, 5, 9) == "unknown"
        source.gate.set()
        assert await manager.activate(10, revision, 1, 5, 9) == "unknown"
        assert sum(member == "InvokeAction" for _, member, _, _ in source.calls) == 1
        assert not source.invoked
    asyncio.run(scenario())


def test_router_keeps_unselected_provider_idle_and_releases_original_provider():
    class Provider:
        def __init__(self):
            self.calls = []
        async def call(self, method, payload):
            self.calls.append(method)
            return {"v": 1, "epoch": "provider-epoch", "pid": 123,
                    **(payload or {}), "status": "ready", "token": "bound-token"}

    async def scenario():
        selected = "off"
        router = ConfiguredBridgeClient(Source(), lambda: selected)
        dms, swaync = Provider(), Provider()
        router.providers = {"dms": dms, "swaync": swaync}
        with pytest.raises(BridgeError):
            await router.call("status")
        assert not dms.calls and not swaync.calls
        selected = "swaync"
        binding = await router.call("bind", {"session": 5, "boot_id": 9, "id": 10, "rev": 3})
        selected = "dms"
        await router.call("release", binding)
        assert swaync.calls == ["bind", "release"]
        assert not dms.calls
    asyncio.run(scenario())


def test_native_calls_pin_the_owner_and_reject_identity_changes():
    from status349.config import NotificationsConfig
    from status349.sources.notifications import NotificationSource

    async def scenario():
        async def callback(_value):
            pass
        source = NotificationSource(NotificationsConfig(), callback, callback)
        source._identity_state = "ready"
        source._server_owner = ":1.7"
        calls = []
        class Control:
            async def call(self, message):
                calls.append(message)
                source._owner_change_generation += 1
                return object()
        source._control = Control()
        with pytest.raises(RuntimeError, match="owner changed"):
            await source.action_provider_call(":1.7", "GetNotification", "u", [101])
        assert calls[0].destination == ":1.7"
        assert calls[0].path == "/org/erikreider/swaync/cc"
        with pytest.raises(RuntimeError, match="owner unavailable"):
            await source.action_provider_call(":1.8", "GetNotification", "u", [101])
        assert len(calls) == 1
    asyncio.run(scenario())


def test_external_token_is_forwarded_only_after_rechecking_binding():
    async def scenario():
        source = Source()
        requested = []
        async def token():
            requested.append(True)
            return "compositor-token"
        manager = manager_for(source)
        manager.provider = SwayncBridgeClient(source, token_provider=token)
        await manager.reconcile_once()
        revision = manager.open_for(10)["rev"]
        assert not requested
        assert await manager.activate(10, revision, 1, 5, 9) == "dispatched"
        assert requested == [True]
        assert source.calls[-1][1:] == ("InvokeActionWithToken", "usss", [101, "revision-101", "default", "compositor-token"])
        async def changed_token():
            source.candidates[11]["version"] += 1
            return "token-after-replacement"
        manager.provider.token_provider = changed_token
        revision = manager.open_for(11)["rev"]
        before = len(source.calls)
        assert await manager.activate(11, revision, 2, 5, 9) == "stale"
        assert len(source.calls) == before
        assert source.invoked == [(101, "default")]
    asyncio.run(scenario())
