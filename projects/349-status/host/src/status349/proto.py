"""349-status wire protocol: line-delimited JSON behind a magic prefix.

The device echoes console output on the same port, so every data line carries
the prefix and everything else is ignored.
"""

from __future__ import annotations

import json
import math
import unicodedata

PREFIX = "@349 "
PROTO_VERSION = 1
LINE_MAX = 8192
CARD_SYNC_CAPABILITY = "card-sync-v1"
DASHBOARD_CAPABILITY = "dashboard-v1"
GROUPED_UI_CAPABILITY = "grouped-ui-v1"
NOTIFICATION_ACTIONS_CAPABILITY = "notification-actions-v1"
NOTIFICATION_HISTORY_CAPABILITY = "notification-history-v1"
CARD_CHUNK_MAX = 2048
IDENTITY_MAX = 0x7FFFFFFF
DASHBOARD_RATE_MAX_BPS = 1_000_000_000_000
NOTIFICATION_BODY_HISTORY_BYTES = 511
NOTIFICATION_BODY_LEGACY_BYTES = 159


def display_text(value: str) -> str:
    """Keep Unicode for the device font fallback and simplify Latin accents."""
    chars: list[str] = []
    for char in unicodedata.normalize("NFC", value):
        if char in "\r\n\t":
            char = " "
        elif unicodedata.name(char, "").startswith("LATIN"):
            base = unicodedata.normalize("NFKD", char)
            if base and " " <= base[0] <= "~":
                char = base[0]
        chars.append(char)
    return "".join(chars)


def clip_utf8(value: str, max_bytes: int) -> str:
    """Fit a device string buffer without splitting a UTF-8 code point."""
    return value.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


def clip_utf8_ellipsis(value: str, max_bytes: int) -> str:
    """Fit a device string buffer and mark clipped text with a Unicode ellipsis."""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    ellipsis = "…"
    ellipsis_bytes = len(ellipsis.encode("utf-8"))
    if max_bytes < ellipsis_bytes:
        return clip_utf8(value, max_bytes)
    return encoded[: max_bytes - ellipsis_bytes].decode("utf-8", "ignore") + ellipsis


def encode(obj: dict) -> bytes:
    line = (PREFIX + json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    if len(line) > LINE_MAX:
        raise ValueError(f"protocol line is {len(line)} bytes; device limit is {LINE_MAX}")
    return line


def classify(line: str) -> tuple[bool, dict | None]:
    """Split a line into (is_data, message); message is None if malformed."""
    if not line.startswith(PREFIX):
        return False, None
    try:
        obj = json.loads(line[len(PREFIX):])
    except json.JSONDecodeError:
        return True, None
    return True, obj if isinstance(obj, dict) else None


def hello() -> dict:
    return {"t": "hello"}


def card_sync_capacity(message: dict) -> int | None:
    """Return the advertised cache size when the device supports card sync."""
    capabilities = message.get("cap", [])
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    if not isinstance(capabilities, list) or CARD_SYNC_CAPABILITY not in capabilities:
        return None
    capacity = message.get("cache_cards")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
        return None
    return capacity


def dashboard_capable(message: dict) -> bool:
    """Dashboard telemetry is only valid on the chunked sync protocol."""
    capabilities = message.get("cap", [])
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    return (
        isinstance(capabilities, list)
        and CARD_SYNC_CAPABILITY in capabilities
        and DASHBOARD_CAPABILITY in capabilities
    )


def grouped_ui_capable(message: dict) -> bool:
    """Grouped retention requires the complete cache/dashboard capability set."""
    capabilities = message.get("cap", [])
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    return (
        card_sync_capacity(message) is not None
        and dashboard_capable(message)
        and isinstance(capabilities, list)
        and GROUPED_UI_CAPABILITY in capabilities
    )


def notification_history_capable(message: dict) -> bool:
    """History requires the complete grouped cache/dashboard capability set."""
    capabilities = message.get("cap", [])
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    return (
        grouped_ui_capable(message)
        and isinstance(capabilities, list)
        and NOTIFICATION_HISTORY_CAPABILITY in capabilities
    )


def _device_boot_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 0xFFFFFFFF:
        return None
    return value


def notification_actions_capable(message: dict) -> bool:
    """Actions require grouped cache, a supported capability, and a real boot ID."""
    capabilities = message.get("cap", [])
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    return (
        grouped_ui_capable(message)
        and isinstance(capabilities, list)
        and NOTIFICATION_ACTIONS_CAPABILITY in capabilities
        and _device_boot_id(message.get("boot_id")) is not None
    )


def _dashboard_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _dashboard_ratio(value: object) -> float | None:
    numeric = _dashboard_number(value)
    if numeric is None:
        return None
    return round(max(0.0, min(1.0, numeric)) * 100) / 100


def _dashboard_rate(value: object) -> float | None:
    numeric = _dashboard_number(value)
    if numeric is None or not 0 <= numeric <= DASHBOARD_RATE_MAX_BPS:
        return None
    return numeric


def dashboard_payload(value: object) -> dict:
    """Return only the bounded, identifier-free dashboard wire fields."""
    source = value if isinstance(value, dict) else {}

    def level_object(key: str, flag: str) -> dict | None:
        candidate = source.get(key)
        if not isinstance(candidate, dict):
            return None
        level = _dashboard_ratio(candidate.get("level"))
        if level is None:
            return None
        state = candidate.get(flag)
        return {"level": level, flag: state if isinstance(state, bool) else None}

    network = source.get("network")
    bluetooth = source.get("bluetooth")
    if isinstance(bluetooth, bool) or not isinstance(bluetooth, int) or not 0 <= bluetooth <= 999:
        bluetooth = None

    return {
        "cpu": _dashboard_ratio(source.get("cpu")),
        "mem": _dashboard_ratio(source.get("mem")),
        "network": network if isinstance(network, bool) else None,
        "rx_bytes_per_s": _dashboard_rate(source.get("rx_bytes_per_s")),
        "tx_bytes_per_s": _dashboard_rate(source.get("tx_bytes_per_s")),
        "battery": level_object("battery", "charging"),
        "volume": level_object("volume", "mute"),
        "bluetooth": bluetooth,
    }


def dashboard_message(value: object) -> dict:
    return {"t": "dashboard", **dashboard_payload(value)}


def card_status(message: dict) -> dict | None:
    """Extract a well-formed firmware cache readback response."""
    values = [message.get(name) for name in ("count", "overflow", "capacity")]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        return None
    ids = message.get("ids")
    if not isinstance(ids, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in ids):
        return None
    count, _overflow, capacity = values
    if len(ids) != count or count > capacity or len(set(ids)) != count:
        return None
    result = {"count": count, "overflow": values[1], "ids": list(ids), "capacity": capacity}
    if "dashboard" in message:
        dashboard = _dashboard_status(message["dashboard"])
        if dashboard is None:
            return None
        result["dashboard"] = dashboard
    if "view_pending" in message:
        pending = message["view_pending"]
        if not isinstance(pending, bool) or (pending and ("deck" in message or "grouped" in message)):
            return None
        result["view_pending"] = pending

    if "deck" in message:
        deck = message["deck"]
        deck_fields = {"enabled", "reachable", "position", "focus_id", "next_id", "stale"}
        if not isinstance(deck, dict) or not deck_fields.issubset(deck):
            return None
        enabled = deck.get("enabled")
        reachable = deck.get("reachable")
        position = deck.get("position")
        focus_id = deck.get("focus_id")
        next_id = deck.get("next_id")
        stale = deck.get("stale")
        if (
            not isinstance(enabled, bool)
            or isinstance(reachable, bool)
            or not isinstance(reachable, int)
            or not 0 <= reachable <= capacity
            or isinstance(position, bool)
            or not isinstance(position, int)
            or not 0 <= position <= reachable
            or (reachable == 0 and position != 0)
            or (reachable > 0 and position == 0)
            or (focus_id is not None and (isinstance(focus_id, bool) or not isinstance(focus_id, int)))
            or (next_id is not None and (isinstance(next_id, bool) or not isinstance(next_id, int)))
            or (reachable <= 1 and next_id is not None)
            or not isinstance(stale, bool)
        ):
            return None
        result["deck"] = {
            "enabled": enabled,
            "reachable": reachable,
            "position": position,
            "focus_id": focus_id,
            "next_id": next_id,
            "stale": stale,
        }

    if "grouped" in message:
        grouped = message["grouped"]
        fields = {"enabled", "session", "group", "manual", "generation", "present_id", "remaining_ms"}
        if not isinstance(grouped, dict) or not fields.issubset(grouped):
            return None
        enabled = grouped.get("enabled")
        session = grouped.get("session")
        group = grouped.get("group")
        manual = grouped.get("manual")
        generation = grouped.get("generation")
        present_id = grouped.get("present_id")
        remaining_ms = grouped.get("remaining_ms")
        if (
            not isinstance(enabled, bool)
            or isinstance(session, bool)
            or not isinstance(session, int)
            or (enabled and not 1 <= session <= IDENTITY_MAX)
            or (not enabled and session != 0)
            or not isinstance(group, str)
            or group not in {"home", "notifications"}
            or not isinstance(manual, bool)
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or not 0 <= generation <= IDENTITY_MAX
            or (
                present_id is not None
                and (
                    isinstance(present_id, bool)
                    or not isinstance(present_id, int)
                    or not 1 <= present_id <= IDENTITY_MAX
                    or present_id not in ids
                )
            )
            or isinstance(remaining_ms, bool)
            or not isinstance(remaining_ms, int)
            or not -1 <= remaining_ms <= IDENTITY_MAX
            or (manual and group != "notifications")
            or (present_id is None and remaining_ms != 0)
            or (present_id is not None and manual)
            or (not enabled and (group != "home" or manual or generation != 0 or present_id is not None))
        ):
            return None
        result["grouped"] = {
            "enabled": enabled,
            "session": session,
            "group": group,
            "manual": manual,
            "generation": generation,
            "present_id": present_id,
            "remaining_ms": remaining_ms,
        }
        if "history" in grouped:
            history = grouped["history"]
            if not isinstance(history, bool) or (history and not enabled):
                return None
            result["grouped"]["history"] = history

    if "actions" in message:
        actions = message["actions"]
        if not isinstance(actions, dict) or not {"enabled", "open", "pending"}.issubset(actions):
            return None
        enabled = actions.get("enabled")
        opened = actions.get("open")
        pending = actions.get("pending")
        if not isinstance(enabled, bool) or not isinstance(opened, list) or len(opened) > capacity:
            return None
        parsed_open = []
        seen: set[int] = set()
        for item in opened:
            if not isinstance(item, dict):
                return None
            local_id, revision, state = item.get("id"), item.get("rev"), item.get("state")
            if (
                isinstance(local_id, bool) or not isinstance(local_id, int)
                or not 1 <= local_id <= IDENTITY_MAX or local_id not in ids or local_id in seen
                or isinstance(revision, bool) or not isinstance(revision, int)
                or not 1 <= revision <= IDENTITY_MAX
                or not isinstance(state, str) or state not in {"ready", "unavailable"}
            ):
                return None
            seen.add(local_id)
            parsed_open.append({"id": local_id, "rev": revision, "state": state})
        parsed_pending = None
        if pending is not None:
            if not isinstance(pending, dict):
                return None
            local_id, revision, request = (
                pending.get("id"), pending.get("open_rev"), pending.get("request")
            )
            if (
                isinstance(local_id, bool) or not isinstance(local_id, int)
                or not 1 <= local_id <= IDENTITY_MAX
                or isinstance(revision, bool) or not isinstance(revision, int)
                or not 1 <= revision <= IDENTITY_MAX
                or isinstance(request, bool) or not isinstance(request, int)
                or not 1 <= request <= IDENTITY_MAX
            ):
                return None
            parsed_pending = {"id": local_id, "open_rev": revision, "request": request}
        if not enabled and (parsed_open or parsed_pending is not None):
            return None
        result["actions"] = {"enabled": enabled, "open": parsed_open, "pending": parsed_pending}

    return result


def _dashboard_status_ratio(value: object) -> float | None:
    numeric = _dashboard_number(value)
    if numeric is None or not 0 <= numeric <= 1:
        return None
    return numeric


def _dashboard_status(value: object) -> dict | None:
    """Validate device readback without clamping or quantizing its readings."""
    fields = ("cpu", "mem", "network", "rx_bytes_per_s", "tx_bytes_per_s")
    if not isinstance(value, dict) or not all(field in value for field in fields):
        return None
    network = value["network"]
    if network is not None and not isinstance(network, bool):
        return None
    result = {"network": network}
    for field, parse in (
        ("cpu", _dashboard_status_ratio),
        ("mem", _dashboard_status_ratio),
        ("rx_bytes_per_s", _dashboard_rate),
        ("tx_bytes_per_s", _dashboard_rate),
    ):
        parsed = parse(value[field])
        if parsed is None and value[field] is not None:
            return None
        result[field] = parsed
    return result


def card_sync_messages(
    snapshot: dict,
    tx: int,
    *,
    include_dashboard: bool = False,
    grouped_session: int | None = None,
    include_actions: bool = False,
    include_history: bool = False,
) -> list[dict]:
    """Build a bounded begin/cards/commit transfer for a card-cache snapshot."""
    if include_history and grouped_session is None:
        raise ValueError("notification history requires grouped sync")
    if include_history and not include_dashboard:
        raise ValueError("notification history requires dashboard sync")
    cards = snapshot["notifs"]
    begin = {
        "t": "sync_begin",
        "tx": int(tx),
        "rev": int(snapshot["rev"]),
        "bar": snapshot["bar"],
        "clock": snapshot["clock"],
        "media": snapshot["media"],
        "limit": int(snapshot["limit"]),
        "count": len(cards),
        "overflow": int(snapshot["overflow"]),
    }
    if include_dashboard:
        begin["dashboard"] = dashboard_payload(snapshot.get("dashboard"))
    if include_actions:
        if grouped_session is None:
            raise ValueError("notification actions require grouped sync")
        begin["actions"] = {"enabled": True}
    if grouped_session is not None:
        if isinstance(grouped_session, bool) or not isinstance(grouped_session, int) or not 1 <= grouped_session <= IDENTITY_MAX:
            raise ValueError("grouped session must be a positive 31-bit integer")
        begin["grouped"] = {"session": grouped_session}
        if include_history:
            begin["grouped"]["history"] = True
    encode(begin)

    messages = [begin]
    batch: list[dict] = []
    start = 0

    def finish_batch() -> None:
        nonlocal batch, start
        if not batch:
            return
        message = {"t": "sync_cards", "tx": int(tx), "start": start, "notifs": batch}
        # encode() measures the actual prefixed UTF-8 line, including JSON
        # escaping. The singleton fallback below handles a card that exceeds
        # the soft chunk target while still respecting the hard line limit.
        encode(message)
        messages.append(message)
        start += len(batch)
        batch = []

    for source_card in cards:
        card = dict(source_card)
        if "body" in card and isinstance(card["body"], str):
            body_limit = NOTIFICATION_BODY_HISTORY_BYTES if include_history else NOTIFICATION_BODY_LEGACY_BYTES
            card["body"] = clip_utf8_ellipsis(display_text(card["body"]), body_limit)
        if include_history:
            history = card.get("history")
            if not isinstance(history, dict) or set(history) != {"rev", "age_ms", "remaining_ms"}:
                raise ValueError("history sync card requires a valid history object")
            revision, age_ms, remaining_ms = (history.get(key) for key in ("rev", "age_ms", "remaining_ms"))
            if (
                isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= IDENTITY_MAX
                or isinstance(age_ms, bool) or not isinstance(age_ms, int) or not 0 <= age_ms <= IDENTITY_MAX
                or isinstance(remaining_ms, bool) or not isinstance(remaining_ms, int)
                or not 1 <= remaining_ms <= IDENTITY_MAX
            ):
                raise ValueError("history sync card requires a valid history object")
        else:
            card.pop("history", None)
        if include_actions:
            opened = card.get("open") if isinstance(card, dict) else None
            if (
                not isinstance(opened, dict)
                or set(opened) != {"rev", "state"}
                or isinstance(opened.get("rev"), bool)
                or not isinstance(opened.get("rev"), int)
                or not 1 <= opened["rev"] <= IDENTITY_MAX
                or not isinstance(opened.get("state"), str)
                or opened["state"] not in {"ready", "unavailable"}
            ):
                raise ValueError("action sync card requires a valid open object")
        candidate = {"t": "sync_cards", "tx": int(tx), "start": start, "notifs": [*batch, card]}
        try:
            encoded_size = len(encode(candidate))
        except ValueError:
            encoded_size = LINE_MAX + 1

        if encoded_size <= CARD_CHUNK_MAX:
            batch.append(card)
            continue

        if batch:
            finish_batch()
            candidate = {"t": "sync_cards", "tx": int(tx), "start": start, "notifs": [card]}
            encoded_size = len(encode(candidate))

        if encoded_size > CARD_CHUNK_MAX:
            # Maximum notification strings normally stay below 2 KiB even
            # after escaping, but allow an unusually large valid card as its
            # own frame up to the device's hard limit.
            if encoded_size > LINE_MAX:
                raise ValueError("notification card exceeds the device line limit")
            messages.append(candidate)
            start += 1
        else:
            batch.append(card)

    finish_batch()
    commit = {"t": "sync_commit", "tx": int(tx)}
    encode(commit)
    messages.append(commit)
    return messages


def card_action(session: int, nid: int, revision: int, state: str) -> dict:
    """Build a session-scoped action-availability delta."""
    for name, value in (("session", session), ("id", nid), ("revision", revision)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= IDENTITY_MAX:
            raise ValueError(f"{name} must be a positive 31-bit integer")
    if not isinstance(state, str) or state not in {"ready", "unavailable"}:
        raise ValueError("action state must be ready or unavailable")
    return {"t": "card_action", "session": session, "id": nid, "open": {"rev": revision, "state": state}}


def action_result(
    session: int,
    boot_id: int,
    nid: int,
    revision: int,
    request: int,
    status: str,
) -> dict:
    """Build a fully correlated terminal action response."""
    for name, value in (("session", session), ("id", nid), ("open_rev", revision), ("request", request)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= IDENTITY_MAX:
            raise ValueError(f"{name} must be a positive 31-bit integer")
    if _device_boot_id(boot_id) is None:
        raise ValueError("boot_id must be a nonzero uint32")
    if not isinstance(status, str) or status not in {
        "dispatched", "unavailable", "stale", "failed", "unknown"
    }:
        raise ValueError("invalid action result status")
    return {
        "t": "action_result", "session": session, "boot_id": boot_id,
        "id": nid, "open_rev": revision, "request": request, "status": status,
    }


def bar(zones: list[dict], rev: int) -> dict:
    return {"t": "bar", "rev": rev, "zones": zones}


def clock(epoch: int, offset: int) -> dict:
    return {"t": "clock", "epoch": int(epoch), "offset": int(offset)}


def media(state: str, title: str, artist: str, album: str, pos: float, length: float) -> dict:
    return {
        "t": "media",
        "state": state,
        "title": title,
        "artist": artist,
        "album": album,
        "pos": round(float(pos), 3),
        "len": round(float(length), 3),
    }


def notify(
    nid: int,
    app: str,
    summary: str,
    body: str,
    urgency: int,
    expire: int,
    ts: int,
    total: int | None = None,
    cached: bool | None = None,
    session: int | None = None,
    body_max_bytes: int = NOTIFICATION_BODY_HISTORY_BYTES,
) -> dict:
    message = {
        "t": "notify",
        "id": int(nid),
        "app": clip_utf8(display_text(app), 31),
        "summary": clip_utf8(display_text(summary), 63),
        "body": clip_utf8_ellipsis(display_text(body), body_max_bytes),
        "urgency": int(urgency),
        "expire": int(expire),
        "ts": int(ts),
    }
    if total is not None:
        message["total"] = int(total)
    if cached is not None:
        message["cached"] = bool(cached)
    if session is not None:
        message["session"] = int(session)
    return message


def close(nid: int, total: int | None = None, *, session: int | None = None) -> dict:
    message = {"t": "close", "id": int(nid)}
    if total is not None:
        message["total"] = int(total)
    if session is not None:
        message["session"] = int(session)
    return message


def present(session: int, generation: int, nid: int, remaining_ms: int, urgency: int) -> dict:
    """Build a generation-scoped grouped attention update."""
    for name, value, lower in (
        ("session", session, 1),
        ("generation", generation, 1),
        ("id", nid, 1),
        ("remaining_ms", remaining_ms, -1),
        ("urgency", urgency, 0),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        maximum = 2 if name == "urgency" else IDENTITY_MAX
        if value < lower or value > maximum:
            raise ValueError(f"{name} is outside the protocol range")
    return {
        "t": "present",
        "session": session,
        "generation": generation,
        "id": nid,
        "remaining_ms": remaining_ms,
        "urgency": urgency,
    }
