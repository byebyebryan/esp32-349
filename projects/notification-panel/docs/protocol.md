# Wire protocol

[Project overview](../README.md) · [Architecture](architecture.md) · [Behavior](behavior.md)

The version-1 protocol shares USB Serial/JTAG with firmware logs. Each data
line is UTF-8 JSON prefixed by `@349 ` and terminated by a newline. Other
lines are console output. The hard encoded-line limit is 8,192 bytes, including
prefix, JSON escaping and newline. Firmware rejects more than 16 nested
object/array levels, counting the root, before recursive JSON parsing;
delimiters in strings do not add depth.

Encoding/negotiation lives in [host proto.py](../host/src/status349/proto.py);
firmware validation lives in [proto.c](../main/proto.c) and
[state.c](../main/state.c). The tables below cover current extensions;
[behavior](behavior.md#older-firmware-and-rendering) describes fallback layouts.

## Handshake and capabilities

A device hello requires integer `proto:1`, string `fw` and a string capability
list containing `link` and `bar`. JSON booleans do not count as integers.
Older compatible peers may omit boot identity. Discovery sends a fresh
positive 31-bit ping challenge and requires its matching post-send pong
within two seconds. Probe writes are bounded to 0.5 seconds, with limits of
128 lines and 64 KiB input. Active sessions ping every four seconds and
disconnect after 12 seconds without a matching pong. Logs do not prove
liveness. Verification adopts the same handle, without a board reset.

| Capability | Requirement and effect |
| --- | --- |
| `card-sync-v1` | Valid nonnegative `cache_cards`; enables chunked snapshots, with current firmware capacity 32 |
| `dashboard-v1` | Card sync; typed dashboard telemetry |
| `grouped-ui-v1` | Cache capacity and dashboard; session-scoped retained cards/presentation |
| `notification-history-v1` | Complete grouped stack; notification-only layout and independent age limits |
| `notification-body-style-v1` | History stack; byte-indexed body styles |
| `notification-actions-v1` | Grouped stack and nonzero unsigned 32-bit `boot_id`; optional Open |
| `backlight-v1` | Independent display policy control |
| `backlight-buttons-v1` | Physical brightness/Power controls and readback |
| `backlight-boost-v1` | Backlight control with duration and ephemeral arrival boost |

Unsupported fields are projected out for each peer. Generic bar/media and
legacy active-card sync remain supported. Actions additionally require host
configuration; advertising the capability does not enable desktop dispatch.

## Atomic card snapshots

The daemon serializes the selected newest cards oldest-to-newest:

| Frame | Fields |
| --- | --- |
| `sync_begin` | `tx`, host `rev`, `bar`, `clock`, `media`, `limit`, `count`, `overflow`; negotiated `dashboard`, `grouped`, `actions` |
| `sync_cards` | Matching `tx`, contiguous zero-based `start`, nonempty `notifs` array |
| `sync_commit` | Matching `tx` |

`tx` is a nonnegative 31-bit integer; count/limit are 0–32 with count ≤ limit.
Grouped metadata is `{"session":S}`; history adds `"history":true`.
`S` is a positive 31-bit random identity for the daemon lifetime. Enabled
actions add exactly `{"enabled":true}`. Grouped sync requires a valid
dashboard; history cards require history metadata, and action-enabled cards
require Open metadata. `overflow` counts retained cards outside the device
cache, not cards evicted from host history.

Chunks target 2,048 encoded bytes. A valid single card may exceed that soft
target while staying under the hard line limit. Firmware uses separate
committed/staging PSRAM buffers. Duplicate IDs, invalid metadata, mismatched
transaction/index/count or more than five seconds between accepted transaction
steps abort the staged transfer. Committed state stays intact. One failed
transfer requests one resync and discards its remaining tail. A reconnect's
initial sync follows the same atomic path. Display-control replay and the
entire transaction cannot interleave with incremental model updates.

Live `notify`/`close` frames carry the grouped session; mismatched sessions
are ignored. Local IDs are positive 31-bit integers, independent of desktop
notification IDs. Genuine replacements move to newest. A `notify` with
`cached:false` updates the retained total and removes that ID locally without
inserting a card or evicting unrelated cards. A live
`notify` may carry `boost:true`; the host strips that event marker from
retained state and snapshots. See [backlight](backlight.md#notification-boost).

## Retention, attention and input

Each history card includes a `history` object with exactly these fields:

```json
{"history":{"rev":1,"age_ms":0,"remaining_ms":600000}}
```

Revision and remaining time are positive 31-bit integers; age is nonnegative
31-bit. The host deducts time spent queued before transmission. Firmware
establishes monotonic arrival/deadline values, then allows same-revision
replay only to shorten a deadline. A higher revision can renew it. Expired
revision tombstones and local hidden IDs prevent ordinary replay from
resurrecting expired/dismissed cards. Offline expiry removes text and actions.

`present` carries `session`, increasing positive 31-bit `generation`, local
`id`, `remaining_ms` and `urgency` (0–2). Remaining time is `-1` for persistent,
`0` for ended, or positive up to 2³¹−1. Queue time is deducted. Same-generation
replay cannot enlarge a deadline; older generations cannot focus or end a
newer presentation. A snapshot is not an arrival. Normal arrivals coalesce
for 300 ms; critical attention bypasses coalescing, then waits for an active
gesture to settle. Popup timeout and retained-history lifetime are separate.

Device `input` messages use `action` plus correlation fields. `browse`
carries `session`, `generation` and `group`; `browse` with `manual:false` lets
later arrivals regain focus after 30 seconds without interaction. The host's
expected generation starts at zero on a new link/boot and advances only after
successful presentation transmission. Same-boot resync preserves ordinary
presentation identity; the host counter does not reset on board reboot.
Dismiss input adds `id`: firmware hides it immediately, the host removes it
and echoes `close`. Optional desktop propagation revalidates the current
desktop association before dispatch. A genuine replacement can restore a
locally hidden card with a renewed revision.

## Text and dashboard fields

App/title fields fit 31/63 UTF-8 bytes. History body fits 511 bytes; legacy
projection fits 159. Clipping never splits a code point and truncated bodies
end in a regular ellipsis. Host conversion and fonts are described in
[behavior](behavior.md#text-and-fonts).

Optional `body_runs` holds at most 16 ordered, nonoverlapping, nonempty ranges.
Each range has exactly integer `start`, `end`, `style`: offsets are UTF-8 byte
boundaries, with exclusive end and styles 1 bold, 2 italic, 3 both. Gaps are
regular. Invalid/excess ranges fall back to plain text without discarding a
valid card; plain replacements clear earlier styles. Styling changes neither
retention nor Open identity. The device does no markup parsing.

| Dashboard field | Value or null when unavailable |
| --- | --- |
| `cpu`, `mem` | Fractions 0–1 |
| `cpu_freq_mhz` | Finite number 0–100,000 |
| `mem_used_bytes` | Integral bytes 0–2⁵⁰ |
| `rx_bytes_per_s`, `tx_bytes_per_s` | Finite rates 0–10¹² |
| `network` | Boolean |
| `battery` | `{level:0–1,charging:boolean-or-null}` |
| `volume` | `{level:0–1,mute:boolean-or-null}` |
| `bluetooth` | Integer 0–999 |

Optional detail fields can be absent on older peers. Missing frequency or
memory amount does not suppress a valid percentage. Rates are raw bytes/s;
firmware formats binary units and promotes rounded unit boundaries. The
dashboard carries no interface names or desktop action identities.

## Open requests and results

Action-enabled cards include `open:{rev:R,state:"ready"|"unavailable"}` with
positive 31-bit revision. Eligibility/binding changes advance it; unchanged
reconciliation does not. `card_action` carries `session`, `id`, `open` and
updates existing metadata without adding a card, renewing it or changing focus.

Activation input is `action:"activate"` plus `session`, unsigned nonzero
32-bit `boot_id`, `id`, `open_rev` and positive 31-bit `request`. All identities
must match the current card/binding/boot; presentation generation is not an
activation gate. The correlated `action_result` repeats those fields and uses
`status`: `dispatched`, `unavailable`, `stale`, `failed` or `unknown`.
`dispatched` acknowledges invocation, not focus. `unknown` may already have
invoked; neither host nor board automatically retries it.

The board permits one pending activation, times out at three seconds, applies
a 500 ms terminal-result cooldown and shows two seconds of failure feedback.
Duplicate/high-water handling and desktop lifetime boundaries are documented
in [architecture](architecture.md#desktop-open-identity). Desktop popup expiry
alone does not invalidate an otherwise live default action; server/plugin
restart, explicit close/removal or lost identity does.

## Readback and display control

`cards_query` returns `cards_status` with cache count, ordered IDs, capacity,
overflow and optional dashboard, deck/grouped, actions and backlight sections.
The UI publishes asynchronously on its timer, so a query immediately after a
cache mutation can precede the new view. Contradictory cache/view state emits
cache-only `view_pending:true` and omits deck/grouped fields; query again for
a coherent view. Readback establishes state, not panel pixels or touch.

The independent `display` frame, button counters, boost fields and dark-state
precedence are specified in [backlight](backlight.md#configuration-and-protocol).
Diagnostic hello/readback requests do not renew the disconnect timeout.
