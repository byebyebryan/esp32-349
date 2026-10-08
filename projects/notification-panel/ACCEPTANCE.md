# 349-status v1 device acceptance

These are the dated prototype, cache and swipe trials. For the later
notification-only UI, host recovery, pairing and current implementation checkpoint,
start with [STATUS.md](STATUS.md). Paths and build identities in the trial
records describe the checkout layout used at the time.

## Short-gate closure — 2026-09-24–25 PDT

This run checks the reviewed v1 fixes on Starship's ESP32-S3 3.49 V2.
The results below are observations from this run, not inherited from the
2026-09-23 prototype checks in [PLAN.md](PLAN.md). The physical short gates
passed. The user chose to defer the optional 24-hour connected soak; no
long-duration stability claim follows from this run.

### Build and environment

| Item | Evidence |
|---|---|
| Reviewed source | `3cc419e` (gap fixes), then `9ddb3b6` (device idle logging) |
| Firmware binary SHA-256 | `6b5ac68df8c35373c3dd4460bdc253d5abad3fb64d9f228075b8f144202fd33c` |
| Firmware ELF SHA-256 | `b249b211443c65cc32df54f131c12986f846495017440e869092acab5ceceec0` |
| Live device identity | `hello.build=9ddb3b6`, `hello.build_sha=b249b2114` |
| Host service | User `349d.service`, final restart at 2026-09-24 21:40:45 PDT; active, linked, not paused |
| Board path | `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_28:84:85:92:C2:20-if00` |
| User config | Original config restored byte for byte after isolated tests and reloaded |

The host suite passed 76 tests on `3cc419e`. The final firmware-only commit
passed a clean firmware rebuild before flashing. The device's hello ELF hash
prefix matches that build. These are build and integration checks; the physical
observations follow.

### Completed short gates

| Gate | Observation |
|---|---|
| Static bar and stale state | On the final firmware, `BAR 東京 が 🛸` stayed visible for over 15 seconds. The user reported no stale host overlay or layout artifacts. |
| Font fallback | The user saw `東京` and `が` on both bar and card, with the unsupported emoji shown as a visible box. |
| Injected card and touch | An isolated card appeared and disappeared immediately when tapped. A separate 1,200 ms injected card appeared once and expired at about 1.22 s by host status. |
| Desktop mirror | An isolated `MIRROR A` notification replaced with `MIRROR B` under one desktop ID, then disappeared after desktop close. The user confirmed the board behavior. |
| Local dismiss and replace | In isolation, tapping `ISOLATED LOCAL A` hid the board card while its desktop notification remained. Replacing the same desktop ID with `ISOLATED LOCAL B` made it visible in both places. A prior crowded run was inconclusive and is not counted as a pass. |
| 20-card burst | Twenty unique desktop IDs were sent in 0.168 s. The user saw two board cards and `+18 more`, with the display responsive. The test IDs were closed afterward. |
| Bar width rejection | Reload rejected a 628 px preset for the 624 px bar, then accepted the restored valid config. |
| Replug | The by-id port returned at 21:38:22.121 PDT; daemon link returned 0.302 s later; matching device hello arrived 1.646 s after port return. The user saw the normal state and no stale overlay. |
| Service restarts | Three consecutive restarts each reached matching device hello and full sync in about 1.4 s; the final service stayed active and linked. |
| Device CPU idle | Final-firmware 10-second alive logs reported 92–98% idle on core 0 and 99–100% on core 1 during observed normal/static-bar windows. The first sample after boot is `na` by design. |

After a full USB power loss during replug, the RTC reported `oscillator
stopped` at boot and was set from the host during sync. Subsequent warm
service restarts reported `PCF85063 ok`. This installation now assumes that
host sleep also removes USB power. A powered-off board cannot show a host
asleep overlay or keep a visible clock running; the replug result exercises
the corresponding cold-start recovery path.

### Mirrored popup expiry follow-up — 2026-09-24 PDT

Real desktop popups could disappear while their board cards remained in the
mirror's count. On this host, DankMaterialShell hides a timed-out popup but
retains its notification-center entry without emitting `NotificationClosed`.
Before the fix, a controlled 1,200 ms popup raised the mirror count from 16
to 17; the count stayed at 17 after popup timeout and returned to 16 only
after explicit desktop close.

The host now expires mirrored board cards at the requested positive timeout,
or at the configured fallback for a server-default (`-1`) timeout. A zero
timeout remains persistent until explicit close. This does not remove the
desktop notification-center entry. The host suite passed 82 tests, including
a live D-Bus mirror test that observes a device `close` without first closing
the desktop notification. No firmware change was required.

`349d.service` restarted at 22:34:34 PDT and reconnected to firmware build
`9ddb3b6`. The fresh sync cleared the stale board state (`notifs: 0`). A live
`-1` timeout probe then raised the mirror count to 1 and returned it to 0
after the five-second fallback, before the probe's desktop ID was explicitly
closed for cleanup. The service remained linked with no warning or error in
its journal. This is a headless count/protocol check; a separate visual claim
about the physical display is not made here.

### Symbol fallback follow-up — 2026-09-24 PDT

The user reported that a curly apostrophe in a real notification appeared as
a box. An isolated card confirmed the distinction: ASCII `we've` rendered in
the title, while `we’ve` (U+2019) showed a box in the body. The host preserved
both characters; the firmware's Montserrat/CJK fonts lacked U+2019.

Generated 14/16 px LVGL symbol subsets now follow Montserrat and Source Han
Sans in the fallback chain. The first build (`build_sha=679281b56`) was
flashed and visually checked. Its two font objects used 56,476 and 70,300
bytes of text data, with no static writable data. Post-flash alive samples
showed 92–94% core 0 idle and 99–100% core 1 idle during this observation
window.

On that first build, the user confirmed that both apostrophe forms rendered in both card
text sizes, together with `東京`, an em dash, an arrow, and a check mark. The
unsupported `🛸` still appeared as a box. The host service remained linked.
The 90-second injected test card then expired, returning the host to
`notifs: 0` with the link still active.

A subsequent static audit found missing `×`, `÷`, and several European letters
that the host does not transliterate. Extending the generated font to Latin-1
and Latin Extended-A raised the repertoire to 2,602 code points at each size;
`tools/check_font_coverage.py` passes its two-size coverage gate. The build
flashed for this audit was `build=cf56cea-dirty`, `build_sha=844085c67`,
matching ELF SHA-256
`844085c67c4db83a018231b127dee362495e76e668ccd8425f2a9f5476923efa`.
The app image is `0x1329f0` bytes in an `0x800000`-byte partition, with 85%
free. The two current font objects use 68,089 and 84,573 bytes of text data
and no static writable data. The host linked after the second flash. A
60-second card exercised `×`, `÷`, `ø`, `ß`, `ł`, and `œ`; it expired and the
host returned to `notifs: 0`. The user missed that card, so it was resent for
two minutes. On the second card, the user visually confirmed all six glyphs
and `東京`. Alive samples on the current build showed 93–94% core 0 idle and
99–100% core 1 idle. This remains a targeted check, not certification of
every glyph or script. The firmware image predates the commit of its source.

A read-only audit of the desktop shell's retained history examined only code
points after the host's normalization and byte limits; it did not print or
retain message contents. Its 33 non-test `ghostty` entries used 72 distinct
code points, all covered by the current font. No Chrome, Google Calendar, or
Slack entries were retained in that sample, so their coverage is unmeasured.

### Snap second-board bring-up smoke — 2026-09-25 PDT

Snap's clone was at `36b0dfc`. Its second board uses USB serial ID
`28:84:85:92:C4:3C`; the factory bootloader/partition and app images were
backed up privately under
`~/.local/share/esp32/backups/snap-c43c-factory-2026-09-25/` before flashing.
The user confirmed the normal bar on the first flash, and `349d` was enabled,
active, and linked.

Initial mirrored and direct notification probes entered the host state but
showed no board card, even after a full sync. After diagnostic reflashes and
reconnects, the user saw a direct card and a desktop notification mirrored on
the board. The temporary device logs and stack change were removed; a direct
card remained visible with the original firmware settings. The last flashed
build reported `36b0dfc-dirty` because Snap still had two setup/service edits
in its checkout; no diagnostic firmware edits remain. The cause of the initial
no-card state was not established. This is a second-board smoke check, not a
repeat of all v1 short gates or a long-duration stability check.

### Snap display-freeze follow-up — 2026-09-25 PDT

Later, Snap's clock and cards stopped updating while `349d` remained linked,
its revision continued advancing, and the board continued printing its
ten-second `alive, host=yes` line. The recent device log contained no frame
profiling lines. Source review found a concrete deadlock risk in the shared
display driver: it allows two panel transfers in flight but used a binary
completion semaphore, so two callbacks arriving before a wait could collapse
into one signal and leave the LVGL task waiting indefinitely. A task backtrace
was not captured, so this remains the likely cause rather than a proven
runtime stack trace.

The driver now uses a counting semaphore for the two outstanding transfers,
checks panel draw errors, and restarts after a logged two-second DMA completion
timeout instead of waiting forever. Both local `349-status` and `349-hello`
firmware builds and Snap's `349-status` build passed. Snap's board was flashed
and reconnected with `hello.build_sha=c4abf317e` (`8647e99-dirty`). The user
confirmed that its clock and notifications update again; subsequent device
logs showed frame flushes and normal `alive` lines. This is a short recovery
check, not evidence of long-duration stability.

### Snap active-card cache gate — 2026-09-25 PDT

The capability-gated cache runs on Snap's ESP32-S3 board while the desktop
mirror is temporarily disabled to isolate injected cards. The current app
image on that board was verified after flashing; its device hello reports
`build_sha=2e83e441b`, and the image on Snap has SHA-256
`f3b712fb9b4d696d2685f8bc825c09974aeec57dc2172f73b166390bea119aba`.
The preceding app image was backed up at
`projects/349-status/build/pre-card-cache-app.bin` on Snap before the first
cache flash. These observations were collected before the host and firmware
worktree changes were committed. Both Starship and Snap host suites pass
**105 tests**, and both firmware builds pass.

The first cache firmware reported the exact newest 20 IDs with no overflow,
then the newest 32 of 33 active IDs with overflow one. After a middle card
expired, the host refilled the cache with the formerly omitted older ID; the
device readback matched the host-selected 32 IDs and overflow returned to
zero. Four repeated full transfers with 32 cards committed without a new
parse error, and later expiry removed them all. This was a controlled host
reconnect and reload exercise, not a physical USB replug.

Review then found that an incremental arrival could exceed a configured
`cache_limit` below the physical 32-card capacity, and that a bad transfer
could request redundant resyncs for its queued tail. The final build carries
an effective limit in `sync_begin`, enforces that limit for deltas, and
discards the tail of a failed transaction. A per-boot hello ID avoids the
duplicate full sync observed during the controlled reconnect. On the final
board build, `cache_limit=8` with nine arrivals produced the expected newest
eight IDs and overflow one; `cache_limit=0` with one arrival produced zero
cached IDs and overflow one. A fresh 20-card set matched all 20 ordered IDs
before the physical replug check. The final build's observed minimum heap
after that burst was 75,835 bytes internal and 7,923,996 bytes PSRAM; alive
logs showed 91–93% core 0 idle and 99–100% core 1 idle in this short window.

For the physical replug, the monitor observed link down at 14:39:00 PDT and
link up at 14:39:04. The board's next full transfer committed 20 cards, and
readback returned the same 20 ordered IDs (`100010`–`100029`) with overflow
zero. The user saw the cards and bar return; no stale overlay was reported. The
cards disappeared afterward because their three-minute injected expiry elapsed;
the host then reported zero active cards. A separate two-minute card was
locally dismissed by tapping: the user saw it hide while the bar stayed, and
host status still showed one active notification with its ID in the device
cache. These are short physical and visual checks, not a long soak.

With the host's serial link paused, a direct board probe committed one card,
sent an out-of-order chunk plus the remaining tail of a second transfer, and
queried the device. It emitted exactly one resync request and kept the prior
committed ID. A subsequent valid transfer committed two expected IDs. The
normal host service then restarted and linked to the same firmware; the
original Snap config was restored byte for byte, notification mirroring is
active, and host/device active-card counts returned to zero.

The current two-card visual layout is unchanged. This gate validates cache
state and transport behavior; the side-peek UI is the next separate goal.

### Starship cache rollout — 2026-09-25 PDT

The reviewed display fix, cache implementation, and UI design checkpoint were
committed and pushed through `3062174`. Starship's connected board uses USB
serial ID `28:84:85:92:C2:20`; its preceding app was backed up under
`~/.local/share/esp32/backups/starship-c220-pre-cache-2026-09-25/` before flashing.
The verified new image has SHA-256
`70a12724246edbc9cd9802497bbf5105270adb14c993fbe5c2d567c2ffaaa4da`;
the device reports `hello.build=3062174`, `hello.build_sha=b5f30e365`,
and `card-sync-v1`. The host service restarted at 19:51:13 PDT, resumed,
and linked with a 32-card device capacity. Its config was unchanged, including
the ten-second normal popup fallback. The 105 host tests passed before rollout.

A 90-second `STARSHIP CACHE CHECK` card was read back as ID `100000`.
The user, physically at Starship, confirmed the normal bar and card with
`東京`, the arrow, and check mark. Device logs showed regular frame flushes,
93% core 0 idle and 99% core 1 idle in this short window. The test card later
expired and device readback returned to zero cards and zero overflow. This is rollout
smoke evidence; the broader cache/recovery gates above were exercised on Snap.
Further UI visual and touch checks now use Starship's board.

### Deferred checks and limits

- The 24-hour connected soak was stopped at the user's request because its
  expected value did not justify continuing it now. No soak pass is claimed.
- The recorder cannot see pixels, so unattended operation would not prove
  the absence of every brief stale card or false overlay.

Actual host suspend/wake was not exercised. The assumption that USB power is
removed during sleep has not been measured on this host, and the manual
replug timing cannot prove host-specific wake timing. If this host retains USB
power during sleep, the powered-board overlay, RTC, and resume behavior need
a separate test before making claims about them.

### Soak recorder readiness

The read-only [soak recorder](tools/soak_recorder.py) uses the daemon's Unix
socket, systemd properties, and a journal cursor. It records sanitized device
CPU heartbeats and fails the run on link loss, service or device reset, an
unexpected hello/resync, host suspension, or a collection gap. Its output is
private append-only JSONL. It does not expose notification text. A 12-second
and a 6-second live dry run passed with matching firmware and no failures;
a deliberately wrong build was rejected at baseline. A separate six-second
transient user-unit run exited successfully. These dry runs do not count
toward the 24-hour window.
The recorder also samples at the deadline; a six-second check with a two-second
interval recorded samples at 2, 4, and 6 seconds before reporting success.

If a future connected soak is useful, launch it with a unique UTC run ID in
both the unit and output names:

```sh
rtk systemd-run --user --unit=349-status-soak-RUN_ID \
  --property=Restart=no --property=RuntimeMaxSec=25h \
  /usr/bin/python3 /home/bryan/code/esp32/projects/349-status/tools/soak_recorder.py \
  --duration 86400 --interval 15 \
  --output /home/bryan/.local/state/349-status/soaks/RUN_ID.jsonl \
  --expected-build 9ddb3b6 --expected-sha-prefix b249b2114 \
  --firmware-elf /home/bryan/code/esp32/projects/349-status/build/349-status.elf \
  --device-path /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_28:84:85:92:C2:20-if00
```

The recorder cannot see pixels. A future soak would still need a human visual
check at its start and end for stale cards or false overlays.

### Deferred soak attempt

The user confirmed a normal bar with no stale card or false overlay at the
start. Transient user unit `349-status-soak-20260925T051016Z.service` began
recording at **2026-09-25 05:10:31.797 UTC** (2026-09-24 22:10:31.797 PDT)
to `/home/bryan/.local/state/349-status/soaks/20260925T051016Z.jsonl`.
Its baseline records host service PID `741952`, `hello.build=9ddb3b6`,
`hello.build_sha=b249b2114`, and the matching full ELF SHA-256 above. The
recorder unit started as PID `763720`. At the user's request it stopped at
**2026-09-25 05:14:43.795 UTC**, after 252.019 s and 16 samples. Its final
record is `outcome=interrupted`, with no sampled failures. The normal daemon
remained active and linked. This attempt is not a 24-hour soak pass.

No changes were pushed during this historical soak attempt.

## Starship side-peek UI checkpoint — 2026-09-25 PDT

The UI goal runs on Starship's USB board `28:84:85:92:C2:20`; Snap was not
updated in this goal. The hardware checks below used an uncommitted tree
based on `7aec129`.
The previous cache app (`3062174`) and original Starship config were backed
up under `~/.local/share/esp32/backups/starship-c220-pre-ui-2026-09-25/`.
Desktop mirroring was temporarily disabled for the physical checks so real
agent notifications did not mix with test cards. The original configuration
was restored byte for byte after the tests.

| Build identity | Value |
|---|---|
| Device descriptor | `hello.build=7aec129-dirty`, `build_sha=f758f77b0` |
| Firmware binary SHA-256 | `0c2d5b1f8b9fa41cc2fce3cfc310f1f2e8e3c75f6595b823b545eb94793cae4a` |
| Firmware ELF SHA-256 | `f758f77b0a12615512b7e75fba72505cbda082beaa69df53de65b3b39bcbf79c` |
| App size | `0x215560` bytes in an `0x800000` partition; 74% free |
| Host service start | 2026-09-25 20:57:49 PDT, active and linked |

The firmware build, native focus-policy and dashboard-parser tests, and font
coverage gate pass. Both 20/22 px fonts retain exactly the 2,602-codepoint
legacy repertoire; the 80 px clock has only its 12 selected glyphs. Generating
the assets twice produced identical hashes. The final integrated host suite
passed **118 tests**. A direct-reader regression verifies that stopping after
the first yielded line cannot strand another complete buffered line; this
fix is separate from the firmware console/frame serialization fix below.

### Firmware readback checks

A direct serial probe ran while the daemon was paused. These are state and
protocol observations, not pixel or touch observations:

| Check | Readback |
|---|---|
| First card | Dashboard enabled, one reachable card and the correct focused ID |
| Critical followed immediately by normal | Three cached cards; critical ID 802 stayed focused |
| Foreground close | Focus selected the surviving next ID 801, without dropping unrelated ID 803 |
| Critical introduced by full sync | New critical ID 804 took focus over the retained normal card |
| Identical full sync | Focus remained ID 804 |
| Malformed staged dashboard | `sync_begin_invalid` resync; committed IDs 801/804/805 and focus were retained |
| Snapshot without dashboard | Legacy UI selected; a subsequent dashboard snapshot restored the deck |
| Stale/recovery | Deck marked stale after 10.6 s without host messages, then cleared after a ping |
| Bounded overflow | Exactly 32 cached/reachable IDs, focus on newest ID 11032, separate overflow one |
| Zero cache | Zero reachable cards, null focus, and overflow five; cleanup returned both counts to zero |

The first probe captured a real console/frame collision: a display log was
split by a framed resync reply, which the strict host classifier could not
recognize. The final firmware joins stdout's stdio lock around framed USB
writes. The retry received the resync as a complete prefixed line and passed
all the checks above. Unchanged label text and colors are also retained,
avoiding allocations and forced redraws on every 100 ms UI tick.

### Completed physical checks

| Check | User observation |
|---|---|
| Idle layout | Quarter-width CPU/MEM/NET rail and large clock/date look right; no absent battery row or stale overlay |
| Active card typography | Larger title/body are readable; both apostrophes, 東京, が, arrow, and check mark render |
| Body tap | Card stays; the compact clock remains in the rail |
| Explicit dismiss | Tapping × hides the card immediately and restores the large idle clock |
| Local dismiss readback | Host/device cache still held ID 100000 while deck reachable count became zero; the action did not close the host card |
| Three-card navigation | The user confirmed C → B → A → C through the side peek, with correct layout and no accidental dismiss |
| 33-card burst | 33 cards injected in 0.203 s; user saw `BURST 33`, `1 / 32`, and separate `+1 uncached`. One peek advanced to `BURST 32` and `2 / 32`; display stayed responsive |

During the isolated windows, ten-second alive samples showed 94–97% core 0
idle and 99–100% core 1 idle. Observed minimum heap after the burst was
73,735 bytes internal and 7,923,996 bytes PSRAM. These are short observations,
not sustained load or soak results.

At 21:03:22 PDT, the normal service restarted after restoring the original
configuration (SHA-256
`61dacdef4cdfca91efe08658285379905b16c9237960879f413152c77e678cdf`).
The service was active and linked to `f758f77b0`, mirroring was enabled again,
and readback showed zero test/cached/overflow cards with the dashboard enabled
and stale false.

### Review and source commits

The implementation was reviewed and committed locally after the hardware
checks: transport fixes in `f9c4c96`, generated fonts in `97c4a70`, and the
UI/telemetry feature in `149f52e`. Review found and fixed a touch race: a
pending arrival could change focus immediately before a peek tap, causing
navigation to skip the previewed card. Manual navigation now cancels the
pending arrival before its timer can change focus.

The reviewed source passes all 118 host tests, the native focus-policy and
dashboard-parser checks, the font coverage audit, and the firmware build
(`0x215570` app bytes; 74% free). The font generator also trims trailing blank
lines without changing glyph data. These final review changes have not been
flashed; Starship's accepted board image remains the one identified above.
This review/commit step did not push or update Snap.

This checkpoint has no soak or host-suspend gate. It does not claim a Snap
deployment or long-duration stability.

## Swipe implementation checkpoint — 2026-09-25

**Historical automated checkpoint, before the physical trials below.** At
this point the user was away from Starship's board, so physical swipe tuning
and performance acceptance were pending. The previous section's tap-deck
observations did not establish acceptance of this swipe firmware.

### Build and deployment

At this checkpoint, the uncommitted implementation was based on `b5e9e6d`.
Luna implemented the bounded input policy, circular ID lookup/selection,
and native LVGL fixture;
the primary integrated the three card views, pointer ownership, animations,
deferred focus, cancellation, and deployment checks.

| Identity | Value |
|---|---|
| Board | Starship, USB serial `28:84:85:92:C2:20` |
| Device descriptor | `hello.build=b5e9e6d-dirty`, `build_sha=7e59d83af` |
| Firmware binary SHA-256 | `b1bca1ce00d2c62c90228b2d761f1c985b90f12258dc56046b5cd60f38606f44` |
| Firmware ELF SHA-256 | `7e59d83af17c2f93a2e65414c6c12bba7e138f77bd17ce6d010d7e1a4f3f2a0c` |
| App size | `0x216700` bytes in the `0x800000` partition; 74% free |
| Host service | Existing service, main PID `2119025`, started 21:03:22 PDT |
| Normal link restored | 22:41:00 PDT, same firmware identity |

Before flashing, the old app was read from the board and its SHA-256 matched
the accepted `f758f77b0` image above. Old/new binaries, the new ELF, original
config, and synthetic readback evidence are saved under
`~/.local/share/esp32/backups/starship-c220-pre-swipe-2026-09-25/`.
Only Starship's application partition was flashed. Snap was not updated.

### Software checks

- Native deck and input policy checks pass with `-std=c11 -Wall -Wextra -Werror`.
  They cover thresholds, both directions, stale motion samples, flick reversal,
  button/rail ownership, ID zero, removals, release latching, and generations.
- The production `ui_deck.c` runs in a native LVGL pointer fixture; gestures
  enter through `lv_indev` read callbacks, rather than calls to the UI event
  handler or direct focus mutations. Debug and Release runs pass, with
  assertions retained in both.
- The fixture checks slow navigation, flick/cancel, peek tap and drag, body
  tap, explicit dismiss, movement starting on ×, vertical/diagonal input,
  rail ownership, normal-only arrival discard, deferred critical arrival,
  stable text during replacement and reordered sync, source/destination removal
  while held and settling, stale/legacy cancellation, and 0/1/2/32-card states
  with separate overflow. These cache changes are simulated in the fixture;
  they do not exercise the physical touch controller or USB transport.
- RGB565 framebuffer captures at rest and during movement were inspected.
  The rail stays fixed and cards clip within the deck viewport. The fixture
  uses the real title/body font assets; it does not reproduce panel timing.
- Dashboard-parser checks, the unchanged font coverage audit, the firmware
  build, and the integrated host suite (**118 tests**) pass.

Reproduction commands are in the [test guide](docs/testing.md). The fixture uses three
reusable slots; movement updates positions without resizing cards or rebinding
their text. The cache/lifetime protocol and host code are unchanged.

### Board readback checks

The daemon was paused while the direct synthetic probe owned the serial port;
desktop mirroring settings were not changed. The probe verified the new ELF
hash prefix before testing. These are state/protocol checks, not touch or
pixel-motion observations:

| Check | Result |
|---|---|
| Initial sync | One cached/reachable card, ID 801 focused, dashboard enabled |
| Critical then normal arrival | ID 802 remained focused with three reachable cards |
| Foreground close | Surviving successor ID 801 selected; unrelated ID 803 retained |
| Critical introduced by full sync | ID 804 took focus; identical sync preserved it |
| Malformed staged dashboard | `sync_begin_invalid` resync; committed IDs/focus retained |
| Legacy host snapshot | Deck disabled; dashboard snapshot restored it |
| Stale/recovery | Stale after 10.6 seconds without host updates, cleared after ping |
| Cache bound | 32 reachable cards, newest ID 11032 focused, separate overflow one |
| Empty cache | Null focus and zero reachable cards, with overflow five reported separately |
| Cleanup | Synthetic cards and overflow cleared before daemon resume |

The first probe queried before the cold-start UI timer had published the
committed cache. It saw one cached card with the deck still disabled. A
follow-up poll observed the deck enabled by about 452 ms after sync; the
final probe used a bounded two-second readiness wait and passed. This wait
is not a measurement of finger feedback or panel animation speed.

One ten-second health sample during the direct probe reported minimum free
internal heap **73,823 bytes**, PSRAM **7,923,996 bytes**, and LVGL task stack
headroom **1,744 bytes**. CPU idle percentages were not yet available in that
first sample. These values cover this short readback run, with no physical
swipe load; they do not establish the active-animation memory or FPS gate.

After cleanup, `349d` resumed and linked to `7e59d83af`. Readback showed the
dashboard enabled and stale false, with a real mirrored card rather than an
injected test ID. The config remained byte-identical (SHA-256
`61dacdef4cdfca91efe08658285379905b16c9237960879f413152c77e678cdf`).

### Remaining exit gate

When the user is back at Starship, run the isolated physical trials in the
[swipe plan](design/swipe-deck-plan.md): slow drags, flick/cancel, wrap,
twenty mixed attempts with at least 19 successful navigations and no accidental
dismissals, hit targets, arrival/removal while touching, and a bounded
30-second animation/latency/heap/stack sample. Existing display debug logs
report frame period and transfer time; the health log now includes LVGL stack
headroom. No panel FPS or touch-reliability claim is made yet.

The goal remained open at this checkpoint. No commit, push, soak,
host-suspend test, or Snap rollout was performed.

## Swipe physical follow-up — 2026-09-25

The short physical behavior checks passed with the user at Starship's board.
The measured 25 updates/s target was **not reached**; the user reported that
the last builds felt responsive enough, so further FPS tuning stopped. This
accepts the observed usability at the recorded speed, without claiming that
the original numerical target passed.

### Renderer accepted before cleanup

| Identity | Value |
|---|---|
| Board | Starship, USB serial `28:84:85:92:C2:20` |
| Device descriptor | `hello.build=b5e9e6d-dirty`, `build_sha=c79c9e399` |
| Firmware binary SHA-256 | `e5f702b3eca20c0ee22cb6008b9ca192ed2123cec751a2d7ee5786c0f5ab9059` |
| Firmware ELF SHA-256 | `c79c9e399492df76ae5214af37a26ed011a6951bc6471cbc548556a45607d8b8` |
| App size | `0x216660` bytes in the `0x800000` partition; 74% free |

The full PSRAM LVGL rendering buffer was replaced by a 30,720-byte, 24-row
internal-RAM buffer in PARTIAL mode. Each rendered rectangle is transposed
and byte-swapped into the existing PSRAM shadow. Only the last strip sends
one complete frame through the existing two DMA staging buffers. There are
still three reusable card objects, with no cached card images or extra
framebuffer layer. LVGL drawing runs synchronously under the existing display
lock (`LV_OS_NONE`); the app does not depend on LVGL's internal thread APIs.

The strict rectangle-conversion test checks full-frame conversion, padded
source stride, interior/edge rectangles, untouched pixels, and canaries.
At this stage the actual LVGL pointer fixture also used the 24-row PARTIAL
buffer. Its Debug and Release checks passed, with assertions retained; at-rest and mid-drag
RGB565 captures are pixel-identical to the earlier full-buffer renderer
(ImageMagick absolute-error count zero for both). A final read-only review
found no actionable stride, rotation, flush-order, or gesture-state defect.

### Motion measurements and observed usability

The original swipe image `7e59d83af` passed slow left/right/cancel checks and
20/20 mixed navigations with no accidental dismissals. Profiling then showed
that the initial frame-rate target could not be met. The diagnostic image
`e7f3f7e39` separated draw, conversion, and transfer time. A tiled full-shadow
experiment `5c0ceadec` stayed clean but did not improve speed and was rejected.
The internal-strip image `041e96aea` again passed 20/20 mixed navigations with
no accidental dismissals. The final synchronous-draw image `c79c9e399` passed
the user's repeated continuous-motion and swipe/flick observation: clean
layout and responsive motion. The twenty-attempt counts belong to the
earlier images; no twenty-attempt count is claimed for `c79c9e399`.

One uninterrupted 30-frame batch on `c79c9e399` reported a mean period of
**60.757 ms (16.46 updates/s)** and a minimum period of **51.313 ms**. Its
mean drawing, shadow-conversion, and panel-transfer times were respectively
30.495, 5.747, and 13.964 ms. The fastest sampled individual interval in the
motion/hit-target/count session was 48.889 ms; it is not a sustained-rate
measurement.
Windows that include pauses or idle time are excluded from the active-rate
claim. The requested 30-second finger-motion trials were user-operated,
rather than a timed autonomous benchmark.

The same active batch measured **51.977 ms mean / 60.921 ms maximum** from
a processed position change to completed panel DMA. The largest value in
the subsequent hit-target samples was **66.354 ms**. This metric excludes
touch-controller polling and does not measure physical contact-to-photon
latency. The user separately observed responsive finger-following motion.

The final-build touch/count session reported minimum internal heap
**47,203 bytes**, PSRAM **8,145,184 bytes**, and LVGL stack headroom
**2,476 bytes**. Quiet ten-second samples returned to 96–97% idle on core 0
and 99–100% on core 1. These are short-run resource observations, not soak
or long-duration stability evidence.

### Physical behavior results

| Check on `c79c9e399` | Result |
|---|---|
| Body tap; peek tap | No body action; peek advances one card |
| Drag starting on × | No navigation or dismiss |
| Vertical/diagonal drag; rail-start drag crossing into card | No action |
| One card | Full-width card, no navigation/peek; × restores large idle clock/date |
| Two cards | Both directions select the other card; counts alternate correctly |
| 32 cached plus one uncached | Correct primary/peek, `1 / 32`, separate `+1 uncached`; swipe advances within cache |
| Arrivals/replacement/reordered full sync while held | Copied text/position stayed stable; critical took focus after release; swipes continued normally |
| Destination removed while held | Returned to source; removed ID stayed gone; browsing continued between survivors |
| Source removed while held | Survivor took over cleanly; release and × worked; no resurrection |
| Final state/protocol probe | Identity, priority, close fallback, full sync, invalid-stage recovery, legacy/dashboard mode, stale/recovery, bounds/overflow, and cleanup passed |
| Normal mirror restoration | Same daemon resumed; final firmware identity matched; zero synthetic cards/overflow and stale false |

Synthetic serial evidence and the final binary/ELF are retained under
`~/.local/share/esp32/backups/starship-c220-pre-swipe-2026-09-25/`.
The final-build sessions are `physical-20260925-231426.jsonl` (motion, hit
targets, counts) and `physical-20260925-231904.jsonl` (held-update checks).
The removal checks inject the host's `close` message, which is also how the
host communicates expiry. An initial probe changed only `notify.expire` and
did not remove the destination: firmware intentionally has no independent
notification lifetime timer. The corrected probe sent `close`; the physical
result and readback above concern that host-driven removal path. No actual
desktop-server timeout trial is claimed here; lifetime behavior remains
covered by the unchanged host suite.

Desktop mirroring configuration has not been changed. No commit, push, soak,
host-suspend test, or Snap rollout is part of this follow-up.

### Final cleanup and restoration

The final direct protocol probe verified `c79c9e399` and repeated all checks
in the earlier board-readback table, including stale/recovery and legacy
compatibility. It ended with zero cached cards and overflow. Its results are
archived as `349-swipe-final-c79c9e399-board-probe-results.json` alongside the
console log. Those results establish protocol/state behavior, not touch input.

`349d` resumed at **23:26:25 PDT** and reported the matching device identity
at **23:26:27 PDT**. The service remained active with main PID **2119025**,
started at 21:03:22 PDT. Readback after restoration showed the dashboard
enabled, stale false, zero cards, and zero overflow; no synthetic test ID
remained. Configuration SHA-256 remained
`61dacdef4cdfca91efe08658285379905b16c9237960879f413152c77e678cdf`.
The mirror remains enabled and normal telemetry/keepalive updates resumed.
No new desktop-to-board visual trial is claimed after this final resume.

This closes the swipe short-gate loop at the observed speed, with the unmet
25 updates/s target recorded above. Source changes were still uncommitted
at the end of this gate.

## Renderer cleanup before commit — 2026-09-25

The user asked to remove rendering experiments that did not produce a notable
responsiveness improvement. Luna implemented the bounded cleanup; the primary
reviewed the integration and owns the firmware/board checks.

- Restored the full PSRAM LVGL DIRECT buffer and the original 16,384-pixel
  threshold for rebuilding the full rotated shadow.
- Kept the extracted, tested rectangle conversion for small updates. DIRECT
  passes a pointer offset into the full framebuffer with a 640-pixel stride.
- Kept synchronous drawing (`LV_OS_NONE`), three reusable views, text/style
  change detection, the existing DMA pipeline/counting semaphores/timeouts,
  basic frame-period/transfer diagnostics, and heap/stack health reporting.
- Removed the 30,720-byte internal drawing buffer and temporary draw,
  conversion, and input-feedback profiling, including the UI's profiling API
  dependency and its native stub. Tiled conversion had already been discarded.
- Gesture policy, geometry, fonts, cache/lifetime behavior, and host code were
  not changed. There is no new performance-improvement claim.

The strict C11 conversion test passes, including a new DIRECT-style rectangle
whose source starts inside a full framebuffer and retains full-frame stride.
The native pointer fixture now matches production DIRECT semantics. Debug and
Release pass with assertions retained; at-rest and mid-drag images each have
absolute-error count zero against the accepted reference. The firmware build
passes. The unchanged host suite was not rerun for this renderer-only cleanup.

| Identity | Value |
|---|---|
| Board | Starship, USB serial `28:84:85:92:C2:20` |
| Built descriptor | `b5e9e6d-dirty`, ELF prefix `cc004e27a` |
| Firmware binary SHA-256 | `32cf7e42de9443837f5cbfbded8ecd215c290aa2e4047dd1bafddf56b0aa7df7` |
| Firmware ELF SHA-256 | `cc004e27a697fd75fb32c387cccf43e507adbcad058ff02255d7032776ed6b7f` |
| App size | `0x216310` bytes in the `0x800000` partition; 74% free |

The board reported the matching `cc004e27a` identity and three cached/reachable
cards with the dashboard enabled and stale false. The isolated idle/readback
run reported minimum internal heap **83,011 bytes**, PSRAM **7,923,996 bytes**,
and LVGL stack headroom **2,668 bytes**. There was no observed physical motion
in this run; these values are not active-swipe or performance measurements.
Serial evidence is `physical-20260925-234128.jsonl` in the backup directory.

The user missed the first isolated check after its cards were cleared for
normal mirroring. The cards were resent and retained through the response.
On `cc004e27a`, the user then confirmed **clean layout, equally responsive
motion, and working controls** after the requested drags/flicks, peek tap,
and local dismiss. This is the bounded post-cleanup regression check, not a
repeat of the earlier twenty-attempt acceptance trial or a new FPS target.
The retest is recorded in `physical-20260925-234818.jsonl`; its short resource
sample reported minimum internal heap **82,559 bytes**, PSRAM **7,923,996
bytes**, and LVGL stack headroom **2,668 bytes**.

Test cards were cleared after the user's pass and normal mirroring was resumed.
The same daemon resumed at **23:49:35 PDT**, reported `cc004e27a` at
**23:49:37 PDT**, and returned zero cards/overflow with stale false. The config
remained byte-identical with SHA-256
`61dacdef4cdfca91efe08658285379905b16c9237960879f413152c77e678cdf`.
The earlier detailed motion timing samples identify their own builds and are
not measurements of this cleanup image. No commit or push was performed
during the cleanup gate.

## Swipe review and source commits — 2026-09-25

After the cleanup's physical check, the changes were reviewed and split into
two source commits, followed by this design/acceptance documentation update:

| Commit | Scope |
|---|---|
| `f17ad67` | Drag-and-snap navigation, captured-ID gesture policy, three reusable views, and native policy/LVGL pointer tests |
| `c88eeb9` | Synchronous LVGL drawing, extracted/tested shadow conversion, and stack-headroom reporting |

The commit review reran strict native deck/input/conversion checks and the
LVGL fixture in Debug and Release; all passed. Staged diffs passed whitespace
checks. The firmware build and physical results above belong to their recorded
images; this commit step did not rebuild or reflash the board. Starship remains
on the accepted `cc004e27a` image with normal mirroring restored. No push or
Snap rollout was performed in this step.
