# Display behavior and compatibility

[Project overview](../README.md) · [Setup](setup.md) · [Configuration](configuration.md)

This reference describes the current notification-only UI and the supported
older-peer layouts. Configure these behaviors in the
[configuration guide](configuration.md); recorded validation is in
[STATUS.md](../STATUS.md).

## Recent notifications

Firmware advertising `notification-history-v1` dedicates the right side to
recent notifications. Empty means `No recent notifications`, with the clock
remaining in the left rail. Swipe vertically between whole cards; horizontal
drags and body taps are inert. The 22 px title and 16 px body provide three
body lines; multiple cards have a next-card peek. A singleton keeps the same
140 px card height and button positions. App name and relative age identify
each card. A thin neutral footer bar between the position counter and × starts
full and empties left to right toward × as the card approaches its history
limit. It continues offline, resets on a genuine replacement, and has no
touch action; see the
[age-bar checkpoint](../design/notification-age-bar.md).
Open and the large × occupy a 64 px column on the right; captured
touches stay armed while the finger remains inside their padded target.

In this mode, `retention_s` defaults to 600 seconds (10 minutes) and accepts 1–86,400.
Genuine arrivals and replacements start a new retention interval. Desktop
popup timeout, viewing, sync and board reconnect do not extend it; the board
also expires cached text while disconnected. Explicit desktop close/dismiss
or × removes the record. The newest 32 records stay in daemon RAM; restarting
the daemon starts empty. New arrivals focus while idle, while manual browsing
keeps its selected ID. After 30 seconds without interaction, later arrivals
may take focus again. An archived card's Open button is disabled when its
desktop action is no longer valid. Bodies are bounded to 511 UTF-8 bytes with
an ellipsis; older peers keep the 159-byte projection. See the
[notification-only plan](../design/notification-history-plan.md) and
[acceptance record](../design/notification-history-acceptance.md).

Before the first device hello, notification cards and desktop associations
are bounded to the newest 32 arrivals or replacements. Once an older peer
selects legacy mode, its full active collection and overflow count remain
supported, with close and popup-expiry tracking preserved after history
eviction.

With `device_dismiss = "propagate"`, × removes the local card immediately and
queues the desktop close. Each attempt times out after half a second; queued
requests recheck the server and notification identity before dispatch, so a
replacement is not closed by a stale dismissal.

## Text and fonts

V1 uses Montserrat with a bundled Source Han Sans CJK fallback and a generated
punctuation/symbol subset for bar text. The generated notification fonts cover
English and the 3,500 common Simplified Chinese characters in the first level
of the 2013 通用规范汉字表, plus Chinese punctuation. Existing Latin and symbol
coverage includes typographic quotes (including `’`), arrows, math signs,
shapes, and dingbats;
see [font sources and licenses](../main/fonts/README.md). Latin accents become
base letters, while nondecomposing Latin-1 and Latin Extended-A letters have
glyphs. Glyphs outside the font set show a visible placeholder. The
[font coverage audit](../tools/check_font_coverage.py) checks the selected repertoire.
Kana, additional Han characters, Nerd Font Private Use icons and color emoji
are outside the notification font selection.

App names and titles use bounded raw prefixes of 124 and 252 code points
before normalization and 31/63-byte UTF-8 clipping. Source classification
treats app names and desktop-entry hints over 256 code points as unknown.
With `ignore_apps` configured, oversized app names are conservatively
suppressed so clipping cannot bypass an ignored-app filter. Display projection
leaves the original Open-action identity unchanged.

Notification bodies use an 8,192-code-point processing budget before parsing
or normalization; longer sources retain a prefix and a regular ellipsis.
Within that budget, bodies are converted on the host before clipping: supported
desktop markup becomes readable text, links retain their labels, and Kitty's
plain-text guards are removed. Markdown is interpreted only for identified
Codex terminal notifications; other terminal messages keep literal markers.
Available paragraphs survive as line breaks. The managed `codex-notify` helper
preserves paragraphs in Kitty's encoded body; Ghostty currently keeps its
compact single-line transport.

Peers advertising `notification-body-style-v1` also receive bounded bold and
italic ranges. Latin/punctuation use matching Montserrat variants; CJK and
other fallback glyphs keep regular typography. Body taps remain inert, and
links/code gain no extra controls or special styling. Other peers receive
plain text. See the [body formatting plan](../design/notification-body-plan.md),
[wire contract](../design/notification-body-protocol.md) and
[Starship acceptance](../design/notification-body-acceptance.md).

## Open and touch controls

The dedicated **Open** button (box with an outgoing arrow) beside × invokes a
live desktop notification's default action. It requires a DMS bridge
and is disabled by default; body taps remain inert. See the
[notification action design and plan](../design/notification-actions-plan.md).
In notification-history mode, Open sits above × in a full-height right column.
Both controls tolerate small finger movements and brighten while pressed;
see [the button refinement](../design/button-controls-refinement.md).
The shared neutral gray and muted sage palette is documented in the
[current color hierarchy](../design/color-hierarchy-pass.md),
[rail clock accent pass](../design/rail-clock-accent-pass.md),
[number/unit refinement](../design/rail-number-unit-pass.md), and
[original neutral theme acceptance](../design/neutral-theme.md).
Snap's short Open/× and [card-motion](../design/card-motion.md) checks passed.
Real Ghostty default dispatch/focus passed on Snap with a local Niri compatibility
setting; see the [acceptance record](../design/notification-actions-acceptance.md)
for desktop configuration and application-compatibility limits.

`349ctl status` reports notification-server identity under
`notification_actions.source`, separately from DMS plugin health under
`notification_actions.provider`. When DMS replaces its notification server,
the daemon archives old card identities and discovers the new owner/PID.
Fresh notifications can regain Open automatically; archived cards keep their
text with Open disabled. Identity lookup failures keep Open unavailable while
the daemon retries, without interrupting mirroring or invoking actions.

## Telemetry

All dashboard layouts keep local HH:MM and fixed CPU, MEM, UP, and DN rows.
CPU shows average current host frequency and usage; MEM shows used memory
and usage. Details and percentages occupy separate fixed columns, both right
aligned to keep unit letters fixed. The percentage column reserves `99%` plus
one space; `100%` uses the existing 14 px font on the same baseline to fit.
`3.6G` on CPU means GHz; `8.4G` on MEM means GiB. Memory usage is
`MemTotal - MemAvailable`, with the amount and percentage from the same read.
Values otherwise use 16 px text. UP/DN show physical-uplink transmit/receive
traffic in binary B/s, KiB/s, MiB/s, or GiB/s (1,024 per step), with a space
before the unit. The rail has no battery row. Volume and Bluetooth changes
appear briefly. Bluetooth probing is optional
and failure leaves that reading unavailable. Details and acceptance scope are
in the [grouped direction](../design/grouped-ui-plan.md) and
[wire contract](../design/grouped-ui-protocol.md). The previous horizontal deck
remains the fallback for older hosts. Its recorded swipe trials measured
about 16–20 updates/s; the user reports clean, responsive motion. The initial
25 updates/s tuning target was not reached. Exact build and trial scope are
recorded in [ACCEPTANCE.md](../ACCEPTANCE.md).

Rail telemetry uses a shared one-second sampling cadence, separate from
notification/action wakeups. Slow source commands run in the background and
leave notification, touch and control processing responsive. The next sample
is due one tick after collection finishes, so a slow source cannot cause
back-to-back refreshes. Full sync reuses the latest coherent sample. CPU
retains its three-second smoothing; traffic
uses a short two-second counter window and actual monotonic elapsed time.
Each active physical interface named by IPv4/IPv6 default routes is counted
once. Ethernet and Wi-Fi can both contribute; their virtual VPN/container
interfaces are not added again. This measures interface traffic, including
local-network traffic, rather than Internet reachability or a speed test.
New/unreadable/reset counters show `--` until a valid baseline is available;
`0 B/s` means a measured zero. Link loss and stale host readings have separate
footer messages. The [rail details refinement](../design/telemetry-rail-details.md)
records current geometry, units and validation. See the earlier
[rail refinement plan](../design/telemetry-rail-plan.md)
and [rail acceptance record](../design/telemetry-rail-acceptance.md).
`349ctl status` and the optional dashboard in `349ctl device-cards` expose
bounded host/firmware telemetry for diagnosis. Older peers remain supported
and may lack the optional frequency, used-memory or rate/readback fields;
unavailable readings show `--` independently of the percentages.
The existing ten-second health log includes minimum-free LVGL and link-task
stack space. The [board telemetry probe](../tools/check_telemetry_board.py)
compares real host samples with USB readback; `--stress-readback` additionally
checks varied numeric responses with 32 cached cards and measured link-stack
headroom. `--history-stress` also checks the expanded text cache and controlled
device expiry, same-revision replay, and replacement renewal. It requires a
paused daemon and resets the board; the caller resumes
the daemon afterward. Probe cadence and controlled stress values are separate
from normal daemon timing and physical traffic measurements.

CPU and MEM have thin neutral utilization bars beneath their existing readings.
Each uses a 32 px block with separate space for its bar and surrounding gaps.
They fill from the left, use the same sampled percentages, and retain a dimmed
level when readings are stale. Unavailable usage leaves an empty track with
`--` in the percentage column. See the [usage-bar refinement](../design/telemetry-rail-usage-bars.md)
for native captures and the firmware candidate's validation boundary.

## Connection states

With `backlight-v1` firmware, the backlight turns off after five minutes without
host traffic, including when the daemon stops while USB remains powered. A
newly powered board uses the same grace period. The timeout starts at the last
host message, rather than five additional minutes after the stale indicator.

While connected, `349d` follows Linux monitor power: any on keeps the backlight
on; all known off turns it off. This follows screen power separately from
session lock. The monitor worker samples once per second independently of
telemetry. Missing or unreadable state preserves the prior screen decision and
is visible in `349ctl status`.

Backlight-off leaves the ESP32, LCD controller and USB receiver running. The
clock, retained notifications and expiry continue. Reconnect replays the
current host screen decision; notifications and touch do not wake a dark bar.
Pointer input is cancelled while dark, and a held finger must be released
before a new press is accepted after wake. Configure normal brightness and the
timeout in [configuration](configuration.md). `349ctl device-cards` includes
applied/target backlight readback; queries do not extend the timeout. See
[the design](../design/backlight-policy.md) and
[dated validation](../design/backlight-acceptance.md).

The physical brightness button cycles 25 → 50 → 75 → 100%, with 50% as the
default. The Power button toggles manual backlight off; pressing it again
returns to automatic screen/timeout following. Brightness can be selected while
dark. Ordinary host syncs and reconnects preserve these local choices; changing
the configured brightness replaces the selection but leaves manual off set.
RESET restarts the board and clears local choices, then the daemon reapplies
its configured level. Buttons act once on release and do not repeat while held.
The board has no ambient-light sensor. See the
[button validation checkpoint](../design/backlight-buttons-acceptance.md).

The daemon sends a ping every four seconds even when the bar does not change.
While the board stays powered, it shows `host asleep` when USB activity stops
and `host disconnected` when USB is active but host messages stop for ten
seconds. If the host cuts USB power during sleep, the board turns off instead;
a full sync restores the display when it powers up and reconnects.
While illuminated, the dashboard keeps the clock visible and marks readings stale.
History mode keeps the cached right-side cards browsable, disables Open, and
shows the connection message in the rail footer; older grouped mode uses the
right content area for that message.

## Older firmware and rendering

`349ctl notify` uses a five-second test presentation by default. Mirrored
notifications follow a positive app timeout; for the server-default timeout (`-1`),
normal/low cards use `popup_timeout_ms` and
critical cards use `critical_popup_timeout_ms`; an app timeout of `0` keeps
automatic presentation until explicit close or the history age limit. With
`notification-history-v1`, presentation timeout leaves the card selected.
With only `grouped-ui-v1`, timeout
returns to Home and retains the notification for browsing; desktop expiry
also retains it. Explicit desktop close or × removes the record. Older peers
keep the active-card behavior, where timeout removes the board card.

Grouped firmware and the host retain the newest 32 records in daemon memory.
A smaller configured device cache leaves older retained records uncached;
evicted records are not included in the overflow count. A chunked full sync restores the cache after a
reconnect; new cards, replacements, and closes remain incremental. An overflow
count describes retained cards that are not cached and cannot be browsed.
See the [cache implementation plan](../design/card-cache-plan.md). Use
`349ctl device-cards` to read the device's ordered cached IDs and overflow
count without resetting the USB link.

Older grouped firmware uses a persistent 160 px telemetry rail
and a 480 px content area. Swipe horizontally between Home (large clock/date)
and Notifications; swipe vertically within Notifications to snap one whole
card. Both directions have bounds. Empty Notifications remains reachable and
shows a clear empty state. Short drags return to the original view,
and deliberate flicks advance one card. A multi-card view provides two body
lines and a next-title peek; a singleton expands to three body lines. Tapping
the bottom peek advances. Deliberate navigation cancels automatic return;
normal arrivals do not interrupt manual browsing, while critical attention
waits for the gesture to settle. Tap × to dismiss
locally; tapping the body does nothing. The position count includes only locally visible,
cached cards; `+N uncached` is a separate count, not a navigation target.

Rendering uses a full PSRAM buffer in DIRECT mode and the existing rotated
PSRAM shadow, then sends one complete panel frame. LVGL drawing runs
synchronously inside the display lock. The small internal-buffer experiment
was reverted: faster conversion was largely offset by slower drawing, while
using scarce internal RAM. Basic debug timing reports frame period and
transfer time; the health log includes heap and LVGL stack headroom.

The grouped UI has passed automated host, production parser/state, real LVGL
replay, Starship serial and short physical gates on firmware `be39d5e49`;
the enlarged close control passed a focused follow-up on `604fd70da`;
see [acceptance evidence](../design/grouped-ui-acceptance.md) and
[the execution plan](../design/grouped-ui-execution.md).
