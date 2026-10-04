import asyncio
import json
import time

import pytest

from status349.actions import (
    BRIDGE_MESSAGE_MAX,
    ActionEntry,
    BridgeClient,
    BridgeError,
    NotificationActionManager,
    action_fingerprint,
    validate_activate_reply,
    validate_bind_reply,
    validate_status,
)
from status349.config import NotificationsConfig
from status349.sources import notifications as notification_source
from status349.sources.notifications import NotificationSource


def candidate(version=1, *, pid=40, summary="Open me"):
    return {
        "id": 1,
        "version": version,
        "desktop_id": 10,
        "owner": ":1.40",
        "pid": pid,
        "expected": {
            "app": "Test app",
            "summary": summary,
            "body": "Body",
            "default_label": "Open",
        },
    }


class Source:
    def __init__(self, ids=(1,), candidates=None):
        self.ids = list(ids)
        self.candidates = dict(candidates or {})
        self.actions_changed = asyncio.Event()

    def action_candidate(self, local_id):
        value = self.candidates.get(local_id)
        return None if value is None else dict(value)

    def action_candidates(self):
        return {local_id: dict(value) for local_id, value in self.candidates.items()}


class Provider:
    def __init__(self, *, pid=40):
        self.epoch = "provider-1"
        self.pid = pid
        self.session = None
        self.boot_id = None
        self.bindings = {}
        self.calls = []
        self.bind_count = 0
        self.fail_status = False
        self.bind_started = None
        self.bind_gate = None
        self.bind_error = None
        self.bind_reply = None
        self.activate_status = "dispatched"
        self.activate_pid = None

    async def call(self, method, payload=None):
        self.calls.append((method, payload))
        if method == "status":
            if self.fail_status:
                raise BridgeError("unavailable")
            return {
                "v": 1, "epoch": self.epoch, "pid": self.pid,
                "session": self.session, "boot_id": self.boot_id,
                "bindings": list(self.bindings.values()),
            }
        if method == "release":
            binding = self.bindings.get(payload["id"])
            if (
                binding is None or binding["rev"] != payload["rev"]
                or binding["token"] != payload["token"]
                or (self.session, self.boot_id) != (payload["session"], payload["boot_id"])
            ):
                return {"v": 1, "status": "stale"}
            if payload["final"]:
                self.bindings.pop(payload["id"], None)
                return {"v": 1, "status": "released"}
            binding["live"] = False
            return {"v": 1, "status": "revoked"}
        if method == "bind":
            self.bind_count += 1
            if self.bind_started is not None:
                self.bind_started.set()
            if self.bind_gate is not None:
                await self.bind_gate.wait()
            if self.bind_error is not None:
                raise self.bind_error
            if self.bind_reply is not None:
                return self.bind_reply
            self.session, self.boot_id = payload["session"], payload["boot_id"]
            token = f"token-{self.bind_count}"
            self.bindings[payload["id"]] = {
                "id": payload["id"], "rev": payload["rev"], "token": token, "live": True,
            }
            return {
                "v": 1, "epoch": self.epoch, "pid": self.pid,
                "session": self.session, "boot_id": self.boot_id,
                "id": payload["id"], "rev": payload["rev"], "token": token, "status": "ready",
            }
        if method == "activate":
            return {
                "v": 1, "epoch": self.epoch,
                "pid": self.pid if self.activate_pid is None else self.activate_pid,
                **{key: payload[key] for key in ("session", "boot_id", "id", "rev", "request")},
                "status": self.activate_status,
            }
        raise AssertionError(method)


def make_manager(source=None, provider=None, *, enabled=lambda: True, context=lambda: (5, 9)):
    source = source or Source(candidates={1: candidate()})
    provider = provider or Provider()
    changed = []

    async def on_change(local_id, opened):
        changed.append((local_id, dict(opened)))

    manager = NotificationActionManager(
        source, provider, enabled=enabled, context=context,
        retained_ids=lambda: source.ids, on_change=on_change,
    )
    return manager, source, provider, changed


def test_manager_binds_once_and_unchanged_polls_do_not_churn():
    async def scenario():
        manager, _source, provider, changed = make_manager()

        await manager.reconcile_once()
        entry = manager.entries[1]
        assert (entry.revision, entry.state) == (3, "ready")
        assert provider.bind_count == 1
        assert changed == [
            (1, {"rev": 2, "state": "unavailable"}),
            (1, {"rev": 3, "state": "ready"}),
        ]
        assert manager.ready_binding(1, 3, 5, 9)

        await manager.reconcile_once()
        assert (entry.revision, entry.state) == (3, "ready")
        assert provider.bind_count == 1
        assert len([call for call in provider.calls if call[0] == "status"]) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [
    BridgeError("unavailable"),
    BridgeError("timeout"),
    BridgeError("timeout", uncertain=True),
    BridgeError("process-error"),
    OSError("temporary IPC failure"),
    asyncio.TimeoutError(),
])
def test_transient_bind_failure_recovers_on_healthy_polls_without_activation(failure):
    async def scenario():
        manager, _source, provider, _changed = make_manager()
        provider.bind_error = failure
        await manager.reconcile_once()
        entry = manager.entries[1]
        assert manager.open_for(1) == {"rev": 2, "state": "unavailable"}
        assert entry.pending_bind_revision == 3

        # Each status reconciliation makes at most one availability attempt,
        # keeping the same revision and exact notification identity throughout.
        for count in range(2, 7):
            await manager.reconcile_once()
            assert provider.bind_count == count
            assert entry.pending_bind_revision == 3
            assert not manager.ready_binding(1, 3, 5, 9)

        attempts = [payload for method, payload in provider.calls if method == "bind"]
        assert all(payload == attempts[0] for payload in attempts)
        provider.bind_error = None
        await manager.reconcile_once()
        assert provider.bind_count == 7
        assert manager.ready_binding(1, 3, 5, 9)
        await manager.reconcile_once()
        assert provider.bind_count == 7
        assert not any(method == "activate" for method, _payload in provider.calls)

    asyncio.run(scenario())


def test_timed_out_bind_that_completed_remotely_is_adopted_without_rebinding():
    class CompletedBindProvider(Provider):
        async def call(self, method, payload=None):
            reply = await super().call(method, payload)
            if method == "bind":
                raise BridgeError("timeout", uncertain=True)
            return reply

    async def scenario():
        manager, _source, provider, _changed = make_manager(provider=CompletedBindProvider())
        await manager.reconcile_once()
        assert manager.entries[1].state == "unavailable"
        assert provider.bind_count == 1
        await manager.reconcile_once()
        assert manager.ready_binding(1, 3, 5, 9)
        assert provider.bind_count == 1
        assert not any(method == "activate" for method, _payload in provider.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [
    BridgeError("malformed-output", uncertain=True),
    BridgeError("oversized-output", uncertain=True),
    {"malformed": True},
    "unavailable",
    "stale",
])
def test_definitive_or_malformed_bind_failure_stays_blocked(failure):
    async def scenario():
        manager, _source, provider, _changed = make_manager()
        if isinstance(failure, Exception):
            provider.bind_error = failure
        elif isinstance(failure, dict):
            provider.bind_reply = failure
        else:
            provider.bind_reply = {
                "v": 1, "epoch": provider.epoch, "pid": provider.pid,
                "session": 5, "boot_id": 9, "id": 1, "rev": 3, "status": failure,
            }
        await manager.reconcile_once()
        assert manager.entries[1].blocked_epoch == provider.epoch
        provider.bind_error = None
        provider.bind_reply = None
        for _ in range(5):
            await manager.reconcile_once()
        assert provider.bind_count == 1
        assert manager.entries[1].state == "unavailable"
        assert not any(method == "activate" for method, _payload in provider.calls)

    asyncio.run(scenario())


def test_retry_does_not_bind_a_closed_source_or_revive_a_revoked_binding():
    async def scenario():
        manager, source, provider, _changed = make_manager()
        provider.bind_error = BridgeError("unavailable")
        await manager.reconcile_once()
        source.candidates.clear()
        provider.bind_error = None
        await manager.reconcile_once()
        assert provider.bind_count == 1
        assert manager.entries[1].pending_bind_revision is None
        assert manager.entries[1].state == "unavailable"

        source.candidates[1] = candidate(version=2)
        await manager.reconcile_once()
        assert manager.entries[1].state == "ready"
        assert provider.bind_count == 2
        provider.bindings[1]["live"] = False
        for _ in range(5):
            await manager.reconcile_once()
        assert provider.bind_count == 2
        assert manager.entries[1].state == "unavailable"
        assert not any(method == "activate" for method, _payload in provider.calls)

    asyncio.run(scenario())


def test_sync_during_bind_keeps_unavailable_revision_until_ready_is_proven():
    async def scenario():
        manager, _source, provider, _changed = make_manager()
        provider.bind_started = asyncio.Event()
        provider.bind_gate = asyncio.Event()

        attempt = asyncio.create_task(manager.reconcile_once())
        await asyncio.wait_for(provider.bind_started.wait(), timeout=1)
        pending_open = manager.open_for(1)
        assert pending_open == {"rev": 2, "state": "unavailable"}
        assert manager.entries[1].pending_bind_revision == 3

        provider.bind_gate.set()
        await attempt
        assert manager.open_for(1) == {"rev": 3, "state": "ready"}
    asyncio.run(scenario())


def test_replacement_revokes_old_token_and_closed_or_no_default_stays_unavailable():
    async def scenario():
        manager, source, provider, _changed = make_manager()
        await manager.reconcile_once()
        first_revision = manager.entries[1].revision

        # The payload is identical, but the source version identifies a new Notify.
        source.candidates[1] = candidate(version=2)
        await manager.reconcile_once()
        entry = manager.entries[1]
        assert entry.state == "ready" and entry.revision > first_revision
        assert provider.bind_count == 2
        assert any(method == "release" and not payload["final"] for method, payload in provider.calls)

        source.candidates.clear()  # NotificationClosed or a missing default action.
        previous_revision = entry.revision
        await manager.reconcile_once()
        assert entry.state == "unavailable" and entry.revision > previous_revision
        assert not manager.ready_binding(1, entry.revision, 5, 9)
        assert provider.bind_count == 2

        source.ids.clear()
        await manager.reconcile_once()
        assert not manager.entries
        assert any(method == "release" and payload["final"] for method, payload in provider.calls)
    asyncio.run(scenario())


def test_no_default_and_provider_pid_mismatch_never_bind():
    async def scenario():
        source = Source(candidates={})
        provider = Provider()
        manager, _source, _provider, _changed = make_manager(source, provider)
        await manager.reconcile_once()
        assert manager.entries[1].state == "unavailable"
        assert provider.bind_count == 0

        source.candidates[1] = candidate(pid=41)
        await manager.reconcile_once()
        assert provider.bind_count == 0
        provider.pid = 41
        await manager.reconcile_once()
        assert provider.bind_count == 1
        assert manager.entries[1].state == "ready"
    asyncio.run(scenario())


def test_provider_missing_and_epoch_reload_revoke_then_rebind_availability_only():
    async def scenario():
        manager, _source, provider, _changed = make_manager()
        await manager.reconcile_once()
        entry = manager.entries[1]
        old_revision = entry.revision

        provider.fail_status = True
        await manager.reconcile_once()
        assert entry.state == "unavailable" and entry.revision > old_revision
        assert manager._provider_epoch is None

        provider.fail_status = False
        await manager.reconcile_once()
        assert entry.state == "ready" and entry.revision > old_revision
        binds_before_reload = provider.bind_count

        provider.epoch = "provider-2"
        provider.bindings.clear()
        await manager.reconcile_once()
        assert entry.state == "ready" and provider.bind_count == binds_before_reload + 1
        assert entry.epoch == "provider-2"
    asyncio.run(scenario())


def test_prior_boot_binding_is_never_adopted_as_current_ready_state():
    async def scenario():
        source = Source(candidates={1: candidate()})
        provider = Provider()
        provider.session, provider.boot_id = 5, 8
        provider.bindings[1] = {"id": 1, "rev": 3, "token": "old-boot", "live": True}
        manager, _source, _provider, _changed = make_manager(source, provider)
        entry = ActionEntry(
            revision=2,
            state="unavailable",
            candidate=candidate(),
            fingerprint=action_fingerprint(candidate()),
            pending_bind_revision=3,
        )
        manager.entries[1] = entry

        await manager.reconcile_once()
        assert entry.state == "ready"
        assert entry.scope == (5, 9)
        assert entry.token != "old-boot"
        assert provider.bind_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("bind_failure", [BridgeError("timeout"), {"malformed": True}])
def test_late_bind_failure_from_replaced_candidate_does_not_block_new_candidate(bind_failure):
    async def scenario():
        source = Source(candidates={1: candidate()})
        provider = Provider()
        provider.bind_started = asyncio.Event()
        provider.bind_gate = asyncio.Event()
        if isinstance(bind_failure, Exception):
            provider.bind_error = bind_failure
        else:
            provider.bind_reply = bind_failure
        manager, _source, _provider, _changed = make_manager(source, provider)

        old_attempt = asyncio.create_task(manager.reconcile_once())
        await asyncio.wait_for(provider.bind_started.wait(), timeout=1)
        source.candidates[1] = candidate(version=2, summary="Replacement")
        manager.open_for(1)
        provider.bind_gate.set()
        await old_attempt
        assert manager.entries[1].blocked_epoch is None

        provider.bind_gate = None
        provider.bind_error = None
        provider.bind_reply = None
        await manager.reconcile_once()
        assert provider.bind_count == 2
        assert manager.entries[1].state == "ready"
        assert manager.entries[1].candidate["version"] == 2
    asyncio.run(scenario())


def test_immediate_open_retention_is_bounded_and_does_not_evict_retained_cards():
    async def scenario():
        source = Source(ids=list(range(1, 101)))
        manager, _source, _provider, _changed = make_manager(source, Provider())
        for local_id in source.ids:
            opened = manager.open_for(local_id)
            if local_id <= 68:
                assert opened == {"rev": 1, "state": "unavailable"}
        assert len(manager.entries) == 32
        assert list(manager.entries) == list(range(69, 101))

        source.ids = list(range(1, 32)) + [100]
        manager.open_for(100)
        assert len(manager.entries) == 32
        assert 1 in manager.entries and 31 in manager.entries and 100 in manager.entries
        assert 32 not in manager.entries
    asyncio.run(scenario())


def test_link_reset_requires_a_new_availability_revision_before_reconnect():
    async def scenario():
        online = True
        manager, _source, provider, _changed = make_manager(
            enabled=lambda: online,
        )
        await manager.reconcile_once()
        entry = manager.entries[1]
        old_revision = entry.revision

        online = False
        manager.invalidate_for_link_reset()
        await manager.reconcile_once()
        assert entry.state == "unavailable" and entry.revision > old_revision
        binds_before = provider.bind_count

        online = True
        await manager.reconcile_once()
        assert entry.state == "ready" and entry.revision > old_revision
        assert provider.bind_count == binds_before + 1
    asyncio.run(scenario())


def test_activation_requires_correlated_pid_and_never_retries_wrong_pid():
    async def scenario():
        manager, _source, provider, _changed = make_manager()
        await manager.reconcile_once()
        entry = manager.entries[1]
        provider.activate_pid = 41

        result = await manager.activate(1, entry.revision, 1, 5, 9)
        assert result == "unknown"
        assert len([call for call in provider.calls if call[0] == "activate"]) == 1
    asyncio.run(scenario())


def test_revision_exhaustion_never_wraps_or_publishes_conflicting_ready_state():
    async def scenario():
        manager, _source, _provider, changed = make_manager()
        await manager.reconcile_once()
        entry = manager.entries[1]
        changed.clear()
        entry.revision = 0x7FFFFFFF
        entry.binding_revision = entry.revision

        await manager.reconcile_once()
        assert entry.revision == 0x7FFFFFFF
        assert entry.exhausted
        assert entry.state == "ready"  # Keep the published tuple stable; dispatch fails closed.
        assert not manager.ready_binding(1, entry.revision, 5, 9)
        assert changed == []
    asyncio.run(scenario())


def test_bridge_response_validation_rejects_bool_and_float_identities():
    status = {
        "v": 1, "epoch": "e", "pid": 40, "session": 5, "boot_id": 9,
        "bindings": [{"id": 1, "rev": 2, "token": "t", "live": True}],
    }
    assert validate_status(status) is not None
    assert validate_status({**status, "v": 1.0}) is None
    assert validate_status({**status, "pid": True}) is None
    assert validate_status({**status, "bindings": [{**status["bindings"][0], "rev": 2.0}]}) is None

    expected_bind = {"session": 5, "boot_id": 9, "id": 1, "rev": 2}
    bind_reply = {
        "v": 1, "epoch": "e", "pid": 40, **expected_bind,
        "token": "t", "status": "ready",
    }
    assert validate_bind_reply(bind_reply, expected_bind) is not None
    assert validate_bind_reply({**bind_reply, "id": 1.0}, expected_bind) is None

    expected_activation = {**expected_bind, "request": 3, "epoch": "e", "pid": 40}
    activation_reply = {
        "v": 1, **expected_activation, "status": "unknown",
    }
    assert validate_activate_reply(activation_reply, expected_activation) is not None
    assert validate_activate_reply({key: value for key, value in activation_reply.items() if key != "pid"},
                                   expected_activation) is None
    assert validate_activate_reply({**activation_reply, "request": True}, expected_activation) is None
    assert validate_activate_reply({**activation_reply, "pid": 41}, expected_activation) is None
    assert validate_activate_reply({**activation_reply, "pid": True}, expected_activation) is None


def test_bridge_client_uses_argument_vector_and_bounds_payload(tmp_path):
    async def scenario():
        executable = tmp_path / "fake-dms"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "print(json.dumps({'args': sys.argv[1:]}))\n"
        )
        executable.chmod(executable.stat().st_mode | 0o111)
        payload = {"v": 1, "expected": "$(must remain literal)"}
        reply = await BridgeClient(str(executable)).call("bind", payload)
        args = reply["args"]
        assert args[:5] == ["ipc", "call", "349-notification-actions", "bind", json.dumps(
            payload, separators=(",", ":")
        )]
        assert "$(must remain literal)" in args[4]

        too_large = {"value": "x" * BRIDGE_MESSAGE_MAX}
        with pytest.raises(ValueError, match="8192 bytes"):
            await BridgeClient(str(executable)).call("bind", too_large)
    asyncio.run(scenario())


def test_bridge_client_timeout_kills_and_reaps_child(tmp_path):
    async def scenario():
        executable = tmp_path / "slow-dms"
        executable.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
        executable.chmod(executable.stat().st_mode | 0o111)
        started = time.monotonic()
        with pytest.raises(BridgeError, match="timeout"):
            await BridgeClient(str(executable), timeout_s=0.05).call("status")
        assert time.monotonic() - started < 2
    asyncio.run(scenario())


def test_server_identity_lookup_is_bounded_and_clears_stale_identity(monkeypatch):
    async def scenario():
        class HangingControl:
            async def call(self, _message):
                await asyncio.Event().wait()

        async def noop(*_args):
            return None

        source = NotificationSource(NotificationsConfig(), noop, noop)
        source._control = HangingControl()
        source._server_owner, source._server_pid = ":1.4", 4
        monkeypatch.setattr(notification_source, "IDENTITY_QUERY_TIMEOUT_S", 0.01)

        started = time.monotonic()
        await source._refresh_server_identity()
        assert time.monotonic() - started < 0.2
        assert source._server_owner is None and source._server_pid is None
        assert source.actions_changed.is_set()
    asyncio.run(scenario())
