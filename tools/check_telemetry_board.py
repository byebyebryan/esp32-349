#!/usr/bin/env python3
"""Compare real host telemetry with production firmware readback.

Requires a paused 349d and resets the board when opening USB. The caller owns
resuming 349d. This checks sampling/serialization/state, not visual appearance.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host" / "src"))
from status349 import proto
from status349.ipc import pause_path
from status349.link import Link, open_port
from status349.sources.clock import ClockSource
from status349.sources.network import NetworkSource
from status349.sources.sysinfo import SysinfoSource
from status349.state import StateModel


FIELDS = ("cpu", "cpu_freq_mhz", "mem", "mem_used_bytes", "network",
          "rx_bytes_per_s", "tx_bytes_per_s")


def compare_dashboard(expected: dict, actual: dict) -> None:
    for field in FIELDS:
        want, got = expected.get(field), actual.get(field)
        if want is None or isinstance(want, bool):
            if got is not want:
                raise AssertionError(f"{field}: expected {want!r}, got {got!r}")
        elif field == "mem_used_bytes":
            if isinstance(got, bool) or not isinstance(got, int) or got != want:
                raise AssertionError(f"{field}: expected {want!r}, got {got!r}")
        elif (isinstance(got, bool) or not isinstance(got, (int, float))
              or not math.isfinite(got)
              or not math.isclose(want, got, rel_tol=2e-5, abs_tol=1e-5)):
            raise AssertionError(f"{field}: expected {want!r}, got {got!r}")


def run(port: str, expected_build: str, samples: int, artifacts: Path,
        *, stress_readback: bool = False, history_stress: bool = False) -> dict:
    stress_readback = stress_readback or history_stress
    if not pause_path().exists():
        raise RuntimeError("Pause 349d before opening this serial connection")
    if not 4 <= samples <= 30:
        raise ValueError("samples must be between 4 and 30")
    artifacts.mkdir(parents=True, exist_ok=True)
    trace: list[dict] = []
    console: list[str] = []
    records: list[dict] = []
    stress_records: list[dict] = []
    network, sysinfo, clock = NetworkSource(), SysinfoSource(), ClockSource()

    def persist() -> None:
        (artifacts / "serial-trace.json").write_text(json.dumps(trace, indent=2) + "\n")
        (artifacts / "console.log").write_text("\n".join(console) + "\n")
        (artifacts / "samples.json").write_text(json.dumps(records, indent=2) + "\n")
        if stress_readback:
            (artifacts / "readback-stress.json").write_text(json.dumps(stress_records, indent=2) + "\n")

    try:
        with Link(open_port(port)) as link:
            accepted_boot_id = None

            def send(message: dict) -> None:
                trace.append({"at": time.monotonic(), "tx": message})
                link.send(message)

            def collect(duration: float) -> list[dict]:
                received = []
                for line in link.lines(duration):
                    data, message = proto.classify(line)
                    if message is not None:
                        received.append(message)
                        trace.append({"at": time.monotonic(), "rx": message})
                        if message.get("t") == "resync":
                            raise AssertionError(f"Firmware requested resync: {message}")
                        if (message.get("t") == "hello" and accepted_boot_id is not None
                                and message.get("boot_id") != accepted_boot_id):
                            raise AssertionError("Firmware reset during telemetry readback")
                    elif not data:
                        console.append(line)
                return received

            hello = None
            deadline = time.monotonic() + 5.0
            while hello is None and time.monotonic() < deadline:
                send(proto.hello())
                for message in collect(.35):
                    if message.get("t") == "hello":
                        hello = message
            if hello is None or not proto.grouped_ui_capable(hello):
                raise AssertionError(f"No grouped firmware hello: {hello}")
            if hello.get("build_sha") != expected_build:
                raise AssertionError(f"Wrong firmware: {hello.get('build_sha')!r}")
            accepted_boot_id = hello["boot_id"]
            if history_stress and not proto.notification_history_capable(hello):
                raise AssertionError("No notification-history capability")
            history_mode = False

            def query(expected: dict) -> dict:
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    send({"t": "cards_query"})
                    for message in collect(.12):
                        if message.get("t") != "cards_status" or message.get("view_pending"):
                            continue
                        parsed = proto.card_status(message)
                        if parsed is None or not isinstance(parsed.get("dashboard"), dict):
                            raise AssertionError("No valid firmware dashboard readback")
                        # An empty snapshot can be acknowledged before LVGL has
                        # first published its enabled deck and Home state.
                        if not parsed["deck"]["enabled"]:
                            continue
                        compare_dashboard(expected, parsed["dashboard"])
                        expected_group = "notifications" if history_mode else "home"
                        if (parsed["grouped"]["group"] != expected_group or parsed["deck"]["stale"]
                            or (history_mode and not parsed["grouped"].get("history"))):
                            raise AssertionError(f"Unexpected view: {parsed}")
                        return parsed
                raise AssertionError("No settled cards_status response")

            model = StateModel()
            model.set_zones([])
            epoch, offset = clock.read()
            model.set_clock(epoch, offset)
            initial = proto.dashboard_payload({**sysinfo.read(), **network.read()})
            model.set_dashboard(initial)
            for message in proto.card_sync_messages(model.card_snapshot(32, retained=True), 1,
                    include_dashboard=True, grouped_session=280928):
                send(message)
            query(initial)

            next_sample = time.monotonic() + 1.0
            for index in range(samples):
                time.sleep(max(0.0, next_sample - time.monotonic()))
                sampled_at = time.monotonic()
                payload = proto.dashboard_payload({**sysinfo.read(), **network.read()})
                send(proto.dashboard_message(payload))
                actual = query(payload)
                records.append({"index": index, "sampled_at": sampled_at,
                                "host": {key: payload.get(key) for key in FIELDS},
                                "device": actual["dashboard"]})
                persist()
                # Skip missed slots; real elapsed time is used by NetworkSource.
                next_sample += max(1, int((time.monotonic() - next_sample) // 1) + 1)

            if not any(row["host"]["rx_bytes_per_s"] is not None
                       and row["host"]["tx_bytes_per_s"] is not None for row in records):
                raise AssertionError("No valid physical-uplink rate sample on this host")
            intervals = [b["sampled_at"] - a["sampled_at"]
                         for a, b in zip(records, records[1:])]
            if any(interval < .95 for interval in intervals):
                raise AssertionError(f"Sampling ran faster than 1 Hz: {intervals}")
            link_stack_free_bytes = None
            if stress_readback:
                # Exercise the deepest readback shape and the floating-point
                # formatting path that overflowed Starship's 4 KiB link stack.
                for index in range(32):
                    model.retain_notification(proto.notify(10000 + index, "349 PROBE",
                        f"READBACK {index + 1:02}",
                        "We've / we’ve 東京 が → ✓ " + "content " * 100
                            if history_stress else "Controlled readback fixture", 1, 0,
                        int(time.time())))
                model.set_dashboard(payload)
                snapshot = model.card_snapshot(32, retained=True)
                for card in snapshot["notifs"]:
                    card["open"] = {"rev": 1, "state": "unavailable"}
                    if history_stress:
                        card["history"] = model.history_metadata(int(card["id"]))
                history_mode = history_stress
                for message in proto.card_sync_messages(snapshot, 2, include_dashboard=True,
                        grouped_session=280928, include_actions=True, include_history=history_mode):
                    send(message)
                full = query(payload)
                if full["count"] != 32 or len(full["actions"]["open"]) != 32:
                    raise AssertionError("Full-cache action readback fixture was not applied")
                rates = (
                    (34891.847784360885, 484271.88736454153),  # Original failing sample.
                    (0.0, 0.0), (1e-8, 1e-5), (0.123456789, 0.987654321),
                    (9.9999999, 99.9999999), (999.9999999, 9999.9999999),
                    (1234567.89123456, 987654321.123456),
                    (999999999.999999, 999999999999.9999),
                    (1e12, 1e12), (None, None),
                )
                for index, (rx, tx) in enumerate(rates):
                    payload = proto.dashboard_payload({"cpu": .99, "mem": .33,
                        "network": True, "rx_bytes_per_s": rx, "tx_bytes_per_s": tx})
                    send(proto.dashboard_message(payload))
                    actual = query(payload)
                    stress_records.append({"index": index,
                        "host": {key: payload[key] for key in FIELDS},
                        "device": actual["dashboard"], "cards": actual["count"]})
                # Read a health report emitted after the stress cases, so its
                # minimum-free watermark includes their maximum stack use.
                console_start = len(console)
                health_deadline = time.monotonic() + 12.0
                while time.monotonic() < health_deadline and link_stack_free_bytes is None:
                    send({"t": "ping", "ts": int(time.time())})
                    collect(.2)
                    for line in console[console_start:]:
                        match = re.search(r"link_stack_free_bytes=(\d+)", line)
                        if match:
                            link_stack_free_bytes = int(match.group(1))
                if link_stack_free_bytes is None or link_stack_free_bytes < 1024:
                    raise AssertionError(f"Insufficient measured link stack headroom: {link_stack_free_bytes}")
                if history_stress:
                    # Firmware must expire without a host close, and an
                    # ordinary same-revision sync must not restart its timer.
                    fixture = {**snapshot["notifs"][-1],
                               "history": {"rev": 100, "age_ms": 0, "remaining_ms": 3000}}
                    one = {**snapshot, "dashboard": payload, "notifs": [fixture], "overflow": 0}
                    started = time.monotonic()
                    for message in proto.card_sync_messages(one, 3, include_dashboard=True,
                            grouped_session=280928, include_actions=True, include_history=True):
                        send(message)
                    if query(payload)["count"] != 1:
                        raise AssertionError("History expiry fixture missing")
                    while time.monotonic() - started < 1.0:
                        send({"t": "ping"})
                        collect(.15)
                    fixture["history"]["remaining_ms"] = 10000
                    for message in proto.card_sync_messages(one, 4, include_dashboard=True,
                            grouped_session=280928, include_actions=True, include_history=True):
                        send(message)
                    while time.monotonic() - started < 3.3:
                        send({"t": "ping"})
                        collect(.15)
                    if query(payload)["count"] != 0:
                        raise AssertionError("Same-revision sync renewed the firmware deadline")
                    for message in proto.card_sync_messages(one, 5, include_dashboard=True,
                            grouped_session=280928, include_actions=True, include_history=True):
                        send(message)
                    if query(payload)["count"] != 0:
                        raise AssertionError("Expired revision resurrected after sync")
                    fixture["history"]["rev"] = 101
                    for message in proto.card_sync_messages(one, 6, include_dashboard=True,
                            grouped_session=280928, include_actions=True, include_history=True):
                        send(message)
                    if query(payload)["count"] != 1:
                        raise AssertionError("Genuine newer revision did not return")
                # Leave the cache empty; caller restores its negotiated mode.
                for message in proto.card_sync_messages(StateModel().card_snapshot(32, retained=True),
                        7, include_dashboard=True, grouped_session=280928,
                        include_history=history_mode):
                    send(message)
                collect(.2)
            result = {"passed": True, "hello": hello, "samples": len(records),
                      "sample_intervals_s": intervals,
                      "scope": "Real host sample → serializer → USB → production state readback; "
                               "this probe supplies its own cadence, not the daemon scheduler. "
                               "No visual, touch or display-performance claim."}
            if stress_readback:
                result.update(readback_stress_cases=len(stress_records), readback_cache_cards=32,
                              link_stack_free_bytes=link_stack_free_bytes)
            if history_stress:
                result.update(history_body_payload_bytes=511,
                              history_expiry_and_same_revision_sync=True,
                              history_expired_replay_and_newer_revision=True)
            (artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            return result
    finally:
        persist()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--expected-build-sha", required=True)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--stress-readback", action="store_true",
                        help="check full-cache numeric readback and link-stack headroom")
    parser.add_argument("--history-stress", action="store_true",
                        help="also check expanded history cache, expiry and stale sync replay")
    args = parser.parse_args()
    result = run(args.port, args.expected_build_sha, args.samples, args.artifacts,
                 stress_readback=args.stress_readback, history_stress=args.history_stress)
    print(json.dumps({key: value for key, value in result.items() if key != "hello"}, indent=2))
