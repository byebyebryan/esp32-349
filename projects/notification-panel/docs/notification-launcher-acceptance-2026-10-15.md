# Terminal-associated notification checkpoint — 2026-10-15

Current selection update, 2026-10-15: the launcher is retired. Its explicit
`--remote` target disables native empty-prompt Left navigation to Command Center.
The checks below did not cover that shortcut and do not establish transparent
native behavior. Codex Plus delivery/hooks are removed; the retained
[349 inspector](codex-inspector.md) supplies only read-only lifecycle queries.
Native notifications remain selected. The dated rendering/touch and exact-card
retirement observations below retain their original scope.

The qualified daily launcher and live 349 admission queue pass controlled local
and Snap SSH device-lifecycle checks. Desktop Open is independently qualified in
session and Command Center views. The subsequent user-observed physical smoke
check below confirms readable content, touch navigation/dismissal and visible
newer-turn retirement on the paired 349.

This follows the [config-route checkpoint](rich-notifications-acceptance-2026-10-15.md).
Its historical source tuple and limitations remain unchanged. The panel stays a
generic notification center; optional Codex metadata supplies host context and
native-history supersession. Agent Observer is not required.

## Installed tuple and activation

| Component | Qualified state |
| --- | --- |
| Helper | codex-plus `8d1fea8e5f25bb71e0ef18ff424a73d7578ac8c0`, [preview 5](https://github.com/byebyebryan/codex-plus/releases/tag/codex-notify-v0.1.0-preview.5), fourteen installed files and executable flags verified on both hosts |
| Helper archive | SHA-256 `01ac786f61b57372f1c8417483ca71b1f1a30cb17a9f30e7c6c1cd2801e307a6` |
| Daily entry | Fresh interactive user Zsh shells on Starship/Snap select `codex-notify launch`; agent/noninteractive and unsupported invocations retain native dispatch |
| Native runtime | `/usr/bin/codex` 0.162.1; managed servers 0.163.0. Starship PID 4056885/start ticks 19234777; Snap PID 1734193/start ticks 19231353, unchanged during these checks |
| 349 host | Admission behavior from `a46ccc8`; all 63 host files match the archived reviewed `deca138` host tree and the deployed `8691c6e` checkout |
| Host activation | Only Starship `349d.service` restarted, PID 775545 to 69987. `rich_notifications.pending` is present and returns zero after checks |
| Board | `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_28:84:85:92:C2:20-if00`; boot ID 162624908 unchanged |

Exact config, unit and pairing-file backups match the live files. Pairing remains
`28:84:85:92:C2:20`; its process-local operation nonce changed with the service
restart. Both native daemon identities, helper/worker/report selectors, native
configuration, shell files and all other sampled service identities were
unchanged. The independently running RLCD service was preserved. No firmware was
flashed and no native Codex daemon or other service was restarted. The new 349d
produced no warning-priority journal entries during these checks.

## Daily local and SSH lifecycle

Actual fresh-shell `codex` launches used owned disposable native threads and
temporary loopback Responses providers, without external model calls or
credentials. Each TUI entered Command Center before its completion was released.

| Source | Body label | Desktop receipt | Device card | Result |
| --- | --- | --- | --- | --- |
| Starship | LOCALPANEL | 632 | 100005 | Retained after 31 seconds, then exact removal on a newer turn |
| Snap SSH | SSHPANEL | 634 | 100007 | Retained after 31 seconds, then exact removal on a newer turn |

Both cases delivered one Codex notification with host title, separate body and
exact native episode metadata. Their newer turn was started by the separate
owned RPC client. Closing the TUI left that turn in progress; it completed after
the private provider was released. Owned threads were archived and test-folder
trust entries removed with restoration of each original native configuration
hash. Original native PID/start ticks remained unchanged.

## Live admission and generic regression

Delayed notifications used exact old episodes from owned local/SSH history.
Both entered the live pending queue, caused one scoped native inspection and
incremented supersession once. Desktop IDs 637 and 638 produced no newly observed
device card across 16 and 28 device-status samples respectively. These are live
queue/counter/cache observations, not a serial packet trace. The complementary
candidate/installed-inspector tests establish zero fake-wire packets for proved
stale arrivals.

A generic notification, desktop ID 639/device card 100012, was admitted without
native inspection or rich association. Closing only that owned desktop ID removed
its exact device card. Delayed test desktop notifications were also closed by
their exact returned IDs. Other notification IDs were not dismissed.

## Desktop actions, distribution and rollback

Preview 5 daily desktop Open passed all four local/SSH and session/Command Center
cases. Desktop IDs 611/613/614/615 each emitted exact action 1 and focused their
originating Kitty window. DMS may expand body text on a card click; the visible
Open button is the qualified activation gesture. It does not select a Command
Center thread or inactive tmux pane.

Both hosts passed exact helper apply/rollback/reselection and scoped two-file
shell apply/rollback/reselection. Distribution keeps its independent revision and
checksum pin; existing sessions retain their original route. Canonical source
convergence preserved unrelated Observer/RLCD changes, with no broad home apply.

The 349 rollback archive contains prior reviewed host source `32c8897`, with a
prepared PYTHONPATH unit override and verified isolated imports. Exact config,
unit and pairing backups remain private. This is a prepared rollback target,
not a dump of the old process's loaded Python objects; a live rollback rehearsal
was not performed. The producer [launcher plan](https://github.com/byebyebryan/codex-plus/blob/main/docs/notification-launcher-plan.md)
records native scope, failure handling, version pairs and measured CPU/memory cost.

## Cwd correction follow-up — 2026-10-15

The activation tuple above records preview 5. The subsequent preview 6 release,
codex-plus `e77601b593f85e8fa004add1e1f54ccb97423f6b`, fixes fresh interactive
launches losing their caller directory when the launcher introduces `--remote`.
Its independent archive SHA-256 is
`7fdec339dcea7dc0d8925b1b5ee74cd4cc84a648662e20e0a842b73ca89eaad3`.
Both installed selectors now use this release; fourteen-file readback and
preview-5 rollback/reselection passed on Starship and Snap.

Fresh sessions receive the absolute effective local directory. Explicit
`-C`/`--cd` forms agree with preflight configuration selection. Resume without
an explicit override retains native saved-directory policy; fork stays native.
Fifty-four notification tests pass. Real native fixtures reproduced the old
null-cwd/home-directory regression and verified fresh, explicit and saved-resume
cases. Installed automatic launches with no cwd option or prompt then sent
`/home/bryan/code/esp32-349` on Starship and `/home/bryan/code/mesh-plus` on Snap;
both native thread-start responses and independent thread-read results matched.
No model turn started, the owned empty threads were archived, and protected
configuration, service and native-owner identities remained unchanged.

This follow-up changed no notification packet, 349 host service or firmware.
The earlier desktop Open and live card results remain their dated preview-5
evidence; physical acceptance was unobserved at this cwd checkpoint. Private cwd receipts are in
`shared-cwd-installed-6/result.json` on each host under the evidence root below.

## Observed physical smoke check — 2026-10-15

Bryan observed the paired V2 board after its independent USB reconnect. Its boot
ID was 1394715752, distinct from the activation checkpoint above; stable USB
identity and pairing remained `28:84:85:92:C2:20`. The latest device hello reports
firmware 0.2.0, build `b9046f4`, SHA `8f5ae24ba`. No firmware or service change
was needed for this check.

An owned native thread used a loopback Responses provider. The test published
a local desktop card with the installed preview-6 utility's v1 identity metadata,
title `Codex @ starship`, label `PHYSICAL_PANEL_CHECK` and a separate readable
body paragraph. A generic `TOUCH_REFERENCE` card supplied the navigation target.
This checks the common desktop-to-panel UI/lifecycle path; the installed native
TUI/relay local and Snap SSH paths retain their separate qualification above.

Bryan explicitly confirmed readable text, vertical swiping between the cards
and dismissal of only the reference with ×. The Codex card, device ID 100057,
remained present before the next owned turn. After that turn started, native
inspection retired the exact card; Bryan confirmed it visibly disappeared and
other cards remained. This physical result comes from those two user reports,
corroborated by cache readback, rather than being inferred from serial output.

Cleanup closed only owned desktop IDs 683/684 and archived the disposable native
thread. Native PID/start ticks and configuration hashes remained unchanged.
Both loopback requests omitted authorization; no external model was called.
The stimulus runner records protocol/cache effects, while
`manual-panel-3/user-observations.json` records the explicit physical pass.

## Upstream/main reconciliation — 2026-10-15

The producer and distribution follow-ups are integrated in their respective main
branches: codex-plus `efc386c` and dotfiles `222c72a` at this check. Preview 7,
source `84ca9beea7ec3200342f74cc35f69678e444a961`, adds interactive `codex agents`
support; its published archive SHA-256 is
`d562616d429cfb24bfd388d270db9df6090c95f745e16648eba488c180bd57f9`.
The installed helper remains preview 6 on Starship and is preview 7 on Snap.
Every file hash and executable flag in each published fourteen-file manifest
matched its installed release. Updating a source pin and applying it to a host
are separate checkpoints; this reconciliation performed no live apply.

Both releases retain the same v1 metadata encoding and event identity. Six
producer/consumer combinations (two releases across three source identities)
round-tripped through the current 349 decoder with identical event IDs. The
consumer needs no code or firmware change for the agents/environment follow-up.

The active 349d already reports three configured sources: Starship, Snap default
OSS in `.codex`, and Snap work in `.codex-work`. The dedicated work route uses its
explicit socket and a distinct native instance. Each exact configured inspector
command returned a valid scoped `unknown` result for a fresh nonexistent episode.
These read-only checks exercise metadata, nonce/reply validation, routing and
uncertainty without creating native threads or model turns. They do not replace
the earlier positive lifecycle, desktop focus or physical observations.

The 349 feature history is published directly through main with this guide
refresh. Its 63 host files are unchanged from the validated feature head
`36d6c28`; no daemon restart, configuration reload or firmware flash accompanies
publication. The source refactor, independent native-home migration and device
reconnects retain their own dated records. The physical smoke check above remains
specific to its original board boot and stimulus.

## Remaining acceptance boundaries

- The physical smoke check covers this paired board/firmware and the owned
  desktop-to-panel stimulus above. Earlier LOCALPANEL/SSHPANEL receipt tests
  remain automated checks; broader hardware and native/UI contexts are not
  implied by this physical pass.
- The DMS panel Open provider remains unavailable and separate from desktop
  terminal Open. No panel Open repair was attempted.
- Foreground suppression and tmux delivery retain their earlier dated
  qualification; they were not repeated in this activation checkpoint.
- Native configuration remains the basic fallback. Unsupported versions/options,
  arbitrary custom loaded-thread hooks and mid-session observation recovery
  retain the producer guide's limits.

Private evidence is under `~/.cache/codex-notify-launcher-20261015`, including
`349d-rollout/activation-1`, `local-desktop-26`, `ssh-desktop-16` and
`manual-panel-3`. Raw native
responses, terminal/desktop captures and runtime state are not committed.
