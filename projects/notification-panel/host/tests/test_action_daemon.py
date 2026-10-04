import asyncio
import json
from types import SimpleNamespace

from status349 import proto
from status349.config import default_config
from status349.daemon import Daemon


class Writer:
    def __init__(self):
        self.frames = []

    def write(self, payload):
        is_data, message = proto.classify(payload.decode("utf-8").rstrip("\n"))
        assert is_data and message is not None
        self.frames.append(message)

    async def drain(self):
        return None


class ActionManager:
    def __init__(self):
        self.entries = {1: SimpleNamespace(revision=3, state="ready")}
        self.active = True
        self.started = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.activate_calls = []
        self.refreshes = []
        self.activate_status = "unknown"

    def ready_binding(self, local_id, revision, session, boot_id):
        entry = self.entries.get(local_id)
        return bool(
            self.active and entry is not None and entry.state == "ready"
            and entry.revision == revision and (session, boot_id) == (5, 9)
        )

    def open_for(self, local_id):
        entry = self.entries.setdefault(local_id, SimpleNamespace(revision=1, state="unavailable"))
        return {"rev": entry.revision, "state": entry.state}

    async def activate(self, local_id, revision, request, session, boot_id):
        self.activate_calls.append((local_id, revision, request, session, boot_id))
        self.started.set()
        await self.gate.wait()
        return self.activate_status

    async def refresh_after_terminal(self, local_id, revision):
        self.refreshes.append((local_id, revision))
        entry = self.entries.get(local_id)
        if entry is not None and entry.revision == revision and entry.state == "ready":
            entry.revision += 1
            entry.state = "unavailable"

    def invalidate_for_link_reset(self):
        self.active = False
        for entry in self.entries.values():
            entry.revision += 1
            entry.state = "unavailable"


def make_daemon():
    cfg = default_config()
    cfg.notifications.device_open = "dms"
    daemon = Daemon(cfg, asyncio.Event())
    daemon.grouped_session = 5
    daemon._grouped_enabled = True
    daemon._grouped_mode = True
    daemon._actions_capable = True
    daemon._actions_negotiated = True
    daemon._action_ledger_boot_id = 9
    daemon._device_boot_id = 9
    daemon._card_sync_capacity = 32
    daemon._writer = Writer()
    daemon._sample = lambda: {}
    daemon.model.retain_notification(proto.notify(1, "app", "one", "body", 1, -1, 1))
    manager = ActionManager()
    daemon.action_manager = manager
    return daemon, manager


def activation(request, *, local_id=1, revision=3, session=5, boot_id=9):
    return {
        "t": "input", "action": "activate", "session": session,
        "boot_id": boot_id, "id": local_id, "open_rev": revision, "request": request,
    }


def results(daemon):
    return [frame for frame in daemon._writer.frames if frame.get("t") == "action_result"]


async def finish_tasks(daemon):
    tasks = tuple(daemon._action_tasks)
    if tasks:
        await asyncio.gather(*tasks)


def test_action_inputs_reject_wrong_scope_revision_and_exhausted_counters():
    async def scenario():
        daemon, manager = make_daemon()

        await daemon._handle_input(activation(1, session=6))
        await daemon._handle_input(activation(1, boot_id=10))
        assert [frame["status"] for frame in results(daemon)] == ["stale", "stale"]
        assert daemon._action_highwater == 0
        assert not manager.activate_calls

        await daemon._handle_input(activation(1, revision=4))
        assert results(daemon)[-1]["status"] == "stale"
        assert daemon._action_highwater == 1
        assert not manager.activate_calls
        before = len(results(daemon))
        await daemon._handle_input({**activation(True), "request": True})
        assert len(results(daemon)) == before

        daemon._action_highwater = proto.IDENTITY_MAX
        await daemon._handle_input(activation(proto.IDENTITY_MAX))
        assert results(daemon)[-1]["status"] == "stale"
        assert daemon._action_highwater == proto.IDENTITY_MAX
        assert not manager.activate_calls

    asyncio.run(scenario())


def test_pending_request_deduplicates_allows_other_input_and_unknown_is_not_retried():
    async def scenario():
        daemon, manager = make_daemon()
        manager.gate.clear()
        await daemon._handle_input(activation(1))
        await asyncio.wait_for(manager.started.wait(), timeout=1)
        assert len(manager.activate_calls) == 1
        assert daemon._manual_notifications

        # Navigation still acquires the state lock and proceeds while IPC waits.
        await asyncio.wait_for(
            daemon._handle_input(
                {"t": "input", "action": "browse", "session": 5,
                 "generation": 0, "group": "home"}
            ),
            timeout=0.2,
        )
        assert daemon._grouped_group == "home"

        frame_count = len(results(daemon))
        await daemon._handle_input(activation(1))
        assert len(manager.activate_calls) == 1
        assert len(results(daemon)) == frame_count

        manager.gate.set()
        await finish_tasks(daemon)
        assert results(daemon)[-1]["status"] == "unknown"
        assert manager.refreshes == [(1, 3)]
        await daemon._handle_input(activation(1))
        assert results(daemon)[-1]["status"] == "unknown"
        assert len(manager.activate_calls) == 1

    asyncio.run(scenario())


def test_terminal_result_window_is_bounded_and_evicted_requests_stay_stale():
    async def scenario():
        daemon, manager = make_daemon()
        for request in range(1, 66):
            await daemon._handle_input(activation(request, local_id=999))
        assert daemon._action_highwater == 65
        assert len(daemon._action_results) == 64
        assert list(daemon._action_results) == list(range(2, 66))
        assert not manager.activate_calls

        await daemon._handle_input(activation(1, local_id=999))
        assert results(daemon)[-1]["status"] == "stale"
        await daemon._handle_input(activation(65, local_id=999))
        assert results(daemon)[-1]["status"] == "stale"
        assert daemon._action_highwater == 65

    asyncio.run(scenario())


def test_removal_cancels_unsent_action_but_cannot_cancel_dispatched_action():
    async def scenario():
        # The close wins before the newly scheduled dispatch task starts.
        daemon, manager = make_daemon()
        await daemon._handle_input(activation(1))
        await daemon._handle_input(
            {"t": "input", "action": "dismiss", "session": 5,
             "generation": 0, "id": 1}
        )
        await finish_tasks(daemon)
        assert not manager.activate_calls
        assert results(daemon)[-1]["status"] == "unavailable"

        # Once provider dispatch starts, a synchronous close only removes UI state.
        daemon, manager = make_daemon()
        manager.activate_status = "dispatched"
        manager.gate.clear()
        await daemon._handle_input(activation(1))
        await asyncio.wait_for(manager.started.wait(), timeout=1)
        await daemon._device_close(1)
        assert daemon._action_pending is not None
        manager.gate.set()
        await finish_tasks(daemon)
        assert len(manager.activate_calls) == 1
        assert results(daemon)[-1]["status"] == "dispatched"
        assert results(daemon)[-1]["id"] == 1
        assert daemon._action_pending is None

    asyncio.run(scenario())


def test_same_boot_reconnect_preserves_ledger_and_new_boot_resets_it():
    async def scenario():
        daemon, manager = make_daemon()
        identity = (5, 9, 1, 3, 4)
        daemon._action_highwater = 4
        daemon._action_results[4] = (identity, "failed")

        # Model the link loop's reset before a same-boot hello.
        daemon._writer = Writer()
        daemon._device_boot_id = None
        daemon._actions_negotiated = False
        daemon._actions_capable = False
        daemon._grouped_enabled = False
        manager.invalidate_for_link_reset()
        hello = {
            "t": "hello", "proto": 1, "fw": "test", "boot_id": 9, "cache_cards": 32,
            "cap": [
                "link", "bar", "card-sync-v1", "dashboard-v1", "grouped-ui-v1", "notification-actions-v1",
            ],
        }
        await daemon._on_line(proto.PREFIX + json.dumps(hello))
        assert daemon._action_highwater == 4
        assert daemon._action_results[4] == (identity, "failed")

        hello["boot_id"] = 10
        await daemon._on_line(proto.PREFIX + json.dumps(hello))
        assert daemon._action_ledger_boot_id == 10
        assert daemon._action_highwater == 0
        assert not daemon._action_results

    asyncio.run(scenario())
