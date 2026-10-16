# Read-only Codex card inspector

The optional inspector belongs to the 349 notification-panel integration. It
answers whether a retained completion card has been superseded by a newer turn
in the exact same native thread. It uses Python 3.11+ and the standard library;
Codex Plus, Agent Observer, desktop libraries and a resident source service are
unnecessary. It never publishes notifications, attaches a TUI, starts a daemon,
loads/resumes a thread or starts a turn.

Native Codex completion/approval notifications remain selected. Those basic
terminal notifications do not contain exact thread/turn IDs and retain generic
349 behavior. The inspector only helps cards already carrying supported rich
metadata. Notification formatting and future producers are separate work.

## Usage and distribution

From `projects/notification-panel/host`:

```sh
scripts/349-codex-inspect identity --socket /absolute/native.sock
scripts/349-codex-inspect inspect --socket /absolute/native.sock < request.json
```

The installed host package also provides `349-codex-inspect`. A remote source
needs only the standalone script and four standard-library modules:

- `scripts/349-codex-inspect`
- `src/status349/__init__.py`
- `src/status349/codex_inspect.py`
- `src/status349/codex_native.py`
- `src/status349/rich_protocol.py`

Preserve their relative layout and include the repository license. Chezmoi pins
this bounded subset of an immutable esp32-349 source archive and selects the
script with `~/.local/bin/349-codex-inspect`. No host dependency installation is
needed on a remote Codex source. Validate the extracted subset with isolated
Python (`python3 -I -S -B scripts/349-codex-inspect identity ...`).

`--socket` takes precedence. Otherwise the canonical absolute `CODEX_HOME` (or
`~/.codex`) supplies `app-server-control/app-server-control.sock`. This tool does
not read Codex Plus environment overrides. Use explicit sockets in 349d routes
so a desktop process cannot select the wrong native home. Follow the
[rich-card configuration guide](rich-notifications.md#configuration).

## Stable contract

The command reads one bounded JSON request from stdin:

```json
{"v":1,"nonce":"0123456789abcdef0123456789abcdef","cards":[]}
```

Each card has exactly `v`, `provider`, `host`, `instance`, `thread`, `turn`, `kind`
and `body_format`. Version is integer 1, provider is `codex`, kind is `completion`,
body format is `plain` or `markdown`, and both native IDs are canonical UUIDs.
Host/instance labels are bounded to 63/128 UTF-8 bytes without controls.

The reply preserves the nonce and identifies its own source:

```json
{"v":1,"nonce":"0123456789abcdef0123456789abcdef","host":"host","instance":"socket-hash","results":{}}
```

`results` maps event IDs to `current`, `superseded` or `unknown`. An event ID is
SHA-256 of the compact JSON array `[provider,host,instance,thread,turn,kind]`.
The source instance remains the first 24 SHA-256 hexadecimal characters of the
absolute configured socket path, with the source's actual hostname. It does not
use daemon PIDs or a display label. Keep existing socket spellings and identities
when migrating routes. The historical metadata category prefix
`x-codex-plus.notification.v1.` is retained for compatibility; its name does not
imply a Codex Plus dependency.

At most 32 cards and 32 descending history rows per distinct requested thread
are read under a six-second native deadline. Only `initialize`, `initialized`
and `thread/turns/list` are emitted. Native listener ownership is checked before
connection and after reads. A newer row ahead of the original completed turn
proves supersession; opaque UUID values never establish ordering. Missing,
malformed, foreign-source, unavailable or changed-owner evidence stays unknown.
Supersession does not prove the user read a notification.

## Retirement and migration

On 2026-10-15, notification delivery/hooks and the explicit-remote TUI relay were
retired from codex-plus. The retained inspector was extracted from its
`notification_inspect.py` and Unix transport; the existing 349 metadata module
provides the unchanged protocol. Native transport and notification preferences
remain independent of this tool.

Install and verify the new inspector first, then replace only the executable in
each configured local/SSH `rich_sources` command, preserving `inspect`, socket,
host and instance. Review and back up the config before `349ctl reload`. A config
reload needs no native daemon or 349d restart. Old immutable helper releases may
remain for ongoing relays and rollback; retire their shell selector and publisher
entry points. Do not interrupt existing sessions to remove those artifacts.

The [host tests](testing.md) include real private Unix/WebSocket reads and a
dependency-free extracted CLI. Historical device/physical observations retain
their dated scope and do not qualify a new notification producer.
