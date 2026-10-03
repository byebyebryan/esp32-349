# Persistent USB pairing acceptance

2026-10-02. Host implementation: `95420ed38c105d0883eb4bda5ca7bdead9a6be2b`.
The [implementation plan](usb-discovery-plan.md) defines the selection,
persistence, cancellation and reconnect contract. No firmware was flashed.

## Automated gate

The complete `host/tests` and `tools/tests` suite passed on Python 3.11 and
3.14: **339 passed, 3 skipped** on each. These runs unset
`DBUS_SESSION_BUS_ADDRESS`, matching CI; the three skips are the optional live
desktop notification checks. All ten JavaScript action-provider tests passed.
[CI for the implementation revision](https://github.com/byebyebryan/esp32-349/actions/runs/37089739963)
passed both Python jobs and the provider job.

Coverage includes filtered discovery, busy ports and alias deduplication,
strict hello validation and fresh pong challenges, atomic state persistence,
failed replacement, exact-serial resolution, corrupt records, pause/reload
cancellation, stale operations, same-handle adoption, buffered input, bounded
writes, watchdog expiry and real PTY unplug cleanup. An additional regression
starts with an unfinished application frame in the peer's line buffer and
requires the first discovery attempt to succeed.

## Migration and live reconnects

Snap and Starship initially had explicit `[link].port` pins. On each host,
the running daemon was paused and a nonresetting production probe recorded
the board's boot ID. After loading the new daemon, `349ctl pair` verified the
active pinned session and saved its USB identity. Only then was the port line
removed, with a backup and parsed-TOML comparison proving that all other
settings were preserved.

Both daemons then restarted with no configured port, resolved their saved
serial and connected to the expected 349. Pairing records survived the
restart unchanged. Pause/resume retained a test notification, and device
readback confirmed its cached ID. Repeating `349ctl pair` returned the saved
identity without another setup scan.

| Host | Saved USB serial | Existing firmware | Boot ID across host reconnects |
| --- | --- | --- | --- |
| Snap | `28:84:85:92:C4:3C` | `d6e782b-dirty`, SHA `9188da1dc` | `3884394811` |
| Starship | `28:84:85:92:C2:20` | `a80e0b6`, SHA `1275d47ee` | `3276465380` |

The state files are `~/.local/state/349d/device.json`, schema version 1,
VID/PID `303a:1001`. Config backups and raw acceptance reports remain ignored
local artifacts under `.cache/pairing-validation/` on each host.

## Mixed-device setup and framing recovery

An isolated setup operation used the production daemon pairing path with
Starship's real RLCD ordered before its 349. Only candidate order, state-file
location and the pause guard were isolated; USB I/O and protocol validation
used production code. The RLCD was rejected after 2.02 seconds. The 349 then
verified in 0.12 seconds, its same open handle was adopted, and the live
pairing record remained unchanged. The live daemon resumed with its previous
boot ID. Subsequent read-only RLCD output reported 50 fps and zero missed
frames.

Some migration reconnects initially needed a second handshake attempt. A
physical Snap reproduction left an unfinished application frame before
closing the serial handle: the next hello was joined to that fragment and
timed out after 2.05 seconds. The following attempt succeeded. Prefixing the
discovery hello with a newline ended the fragment; both subsequent attempts
succeeded in 0.12–0.13 seconds with the same boot ID. No board reset was used.

After loading `95420ed`, Snap's daemon restart returned a verified link in
0.25 seconds. Three consecutive pause/resume cycles each recovered in about
0.15 seconds, preserving boot ID and saved state. Timings include host-side
open/metadata work and status polling; the earlier prototype's roughly 2 ms
hello/pong timing measured just the protocol exchange.

## Physical gates

Starship's 349-only unplug/replug, RLCD-only failed replacement and USB hub
power-cycle observations are pending the user-operated sequence. Their
watcher preserves the live record and reports each inventory/link transition.
Host reboot and startup while a board is still booting have not been observed;
daemon restart and simulated cold-start tests do not establish those physical
gates.
