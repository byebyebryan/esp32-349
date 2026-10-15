# Terminal-associated notification checkpoint — 2026-10-15

The qualified daily launcher and live 349 admission queue pass controlled local
and Snap SSH device-lifecycle checks. Desktop Open is independently qualified in
session and Command Center views. Physical panel rendering and touch remain
unobserved; serial/cache evidence does not establish those properties.

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
was not performed. The producer [launcher plan](https://github.com/byebyebryan/codex-plus/blob/feat/notifications/docs/notification-launcher-plan.md)
records native scope, failure handling, version pairs and measured CPU/memory cost.

## Remaining acceptance boundaries

- Physical readability, visible retirement and touch were not observed. An
  observation request for LOCALPANEL/SSHPANEL remains separate from automated
  receipts; no physical pass is inferred from missing feedback.
- The DMS panel Open provider remains unavailable and separate from desktop
  terminal Open. No panel Open repair was attempted.
- Foreground suppression and tmux delivery retain their earlier dated
  qualification; they were not repeated in this activation checkpoint.
- Native configuration remains the basic fallback. Unsupported versions/options,
  arbitrary custom loaded-thread hooks and mid-session observation recovery
  retain the producer guide's limits.

Private evidence is under `~/.cache/codex-notify-launcher-20261015`, including
`349d-rollout/activation-1`, `local-desktop-26` and `ssh-desktop-16`. Raw native
responses, terminal/desktop captures and runtime state are not committed.
