# Optional rich Codex cards

The panel remains a generic notification center. Versioned Codex metadata can
add a source host label and retire an older completion card after the same native
thread starts a newer turn. Agent Observer and firmware changes are unnecessary.

The [read-only Codex inspector](codex-inspector.md) belongs to this integration.
Notification delivery and the `--remote` relay are retired from codex-plus;
native Codex completion/approval alerts are the selected route. Native alerts
carry no exact thread/turn metadata and therefore use generic retention. The
optional rich consumer remains available for correctly identified metadata.
Unknown versions and malformed categories retain normal formatting and retention.
Private metadata is never sent to firmware. Raw desktop action identities remain
independent of display labels.

## Configuration

Supported metadata enables rich text/host presentation. Lifecycle inspection is
disabled by default. Configure trusted routes under the existing `[notifications]`
table in `~/.config/349d/config.toml`:

```toml
rich_sources = [
  { host = "LOCAL_HOST_FROM_IDENTITY", instance = "LOCAL_INSTANCE_FROM_IDENTITY", command = ["/home/bryan/.local/bin/349-codex-inspect", "inspect", "--socket", "/home/bryan/.codex/app-server-control/app-server-control.sock"] },
  { host = "REMOTE_HOST_FROM_IDENTITY", instance = "REMOTE_DEFAULT_INSTANCE", command = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "snap", "/home/bryan/.local/bin/349-codex-inspect", "inspect", "--socket", "/home/bryan/.codex/app-server-control/app-server-control.sock"] },
  { host = "REMOTE_HOST_FROM_IDENTITY", instance = "REMOTE_WORK_INSTANCE", command = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "snap", "/home/bryan/.local/bin/349-codex-inspect", "inspect", "--socket", "/home/bryan/.codex-work/app-server-control/app-server-control.sock"] },
]
```

Run `349-codex-inspect identity --socket /absolute/path/to/the/native/socket` on each
source, using the same socket as its `inspect` command. Copy both returned fields;
an SSH alias such as `snap` need not equal the returned hostname. Each native home
needs its own route and instance, even when homes share a hostname. The example
above uses Snap's default OSS home and dedicated `.codex-work` home. Launch
wrappers select `CODEX_HOME` for a TUI; a separate 349d process must not infer that
selection from its own environment or inspect another home's history.

Chezmoi pins the standalone inspector independently of Codex Plus. Install the
new inspector before changing configured commands; preserve each route's exact
host, instance and socket. The v1 protocol and source/event identities are
unchanged. Reload a reviewed config with `349ctl reload`; no native daemon or
349d restart is needed to change inspector commands. Leave previous immutable
helper releases in place for ongoing relays and rollback.

Native notification click-to-focus belongs to the originating terminal. Panel
Open separately needs the DMS action bridge. No notification publisher, config
hook, TUI relay or native transport override is selected by this integration.

At most four unique source routes are allowed, each with an absolute executable
and bounded argv. Routes are trusted local configuration, never incoming metadata.
Unconfigured hosts/instances are displayed without lifecycle queries. Empty
`rich_sources = []` disables queries while preserving generic notifications.

## Lifecycle and failure behavior

Configured rich first arrivals enter a bounded 32-card admission queue. The
rich worker wakes to inspect them before sending display or attention output.
Proved stale completions are suppressed; uncertain results keep normal retention.
Generic/unconfigured notifications bypass this queue. Replacements recheck source
version and pending identity before delivery, so a late result cannot publish an
older replacement. Source inspection runs outside the model lock.

Every five seconds, 349d also groups outstanding matching retained cards and sends
bounded stdin requests to each helper. Queries have an eight-second deadline,
8 KiB stdout limit and fresh nonce; cancellation kills the process group. No shell
evaluates notification contents. There is no full-fleet subscription or resident
source observer.

Native descending history must contain the original completed turn behind a newer
turn. Different IDs alone do not prove order. The newer turn may already have
completed. Unknown, unavailable, malformed, mismatched and stale replies leave
the card with its original expiry. Desktop popup expiry does not prevent queries
for retained panel history.

A proved superseded card is removed locally through the existing close protocol.
No desktop dismissal is propagated. Superseded does not mean user-read ack.
Replies recheck the exact retained card, source version and configured route under
the model lock. A newer queued replacement cannot be forgotten by an old reply.

Repeated episodes do not refresh history, presentation or backlight boost.
Replay protection is RAM-only: at most 256 episodes for their original retention
period; it resets with 349d. Associations follow the bounded retained card set.
Expiry, eviction and manual removal stop further queries for that card.

`349ctl status` reports source count, tracked cards, a bounded local-card/event
mapping, pending arrivals, queries and superseded count under `rich_notifications`.
Helper outages remain ordinary uncertainty. Generic
retention, wire negotiation and firmware remain unchanged.

## Validation and operation

Run the host suite in [testing](testing.md). Rich tests cover invalid categories,
fallback, unknown sources, stale replies, queued replacements, duplicate episodes
and bounded subprocess replies. Generic/history/action/pairing/telemetry tests
remain required.

Retain exact host config and previous source before selection. Rollback disables
routes, restores prior host source and restarts only 349d. The helper and native
Codex config have independent selectors; preserve unrelated settings and native
sessions. Physical card/touch acceptance is separate from serial readback.

The [execution plan](rich-notifications-plan.md) records scope and release gates.
The [terminal-launcher checkpoint](notification-launcher-acceptance-2026-10-15.md)
records the activated admission queue and daily local/SSH device lifecycle checks.
The [earlier config-route checkpoint](rich-notifications-acceptance-2026-10-15.md)
retains its dated scope. The later user-observed physical smoke check confirms
readability, navigation/reference dismissal and visible newer-turn retirement on
the paired board. That result does not imply physical acceptance of other boards
or every later launcher/environment change.
