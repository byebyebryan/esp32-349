# Native notification formatting checkpoint - 2026-10-16

Native Codex/Kitty delivery is selected. Notification enrichment and agent-state
handling are parked. The 349 mirrors desktop notifications generically; starting
a newer agent turn no longer performs any card inspection or dismissal.

## Source and installation

- esp32-349 `7d70c74c6fdf5c02c1df67b7a1126091ead000a0` removes the inspector,
  metadata decoder, rich-arrival worker and thread/card replay associations.
- Dotfiles `7fc448f77647726dc60944f91be1b0787503c9a1` retires the inspector
  external/selector and installs the generic Kitty notification formatter.
- Codex Plus `e2ed6fa42d03c8bef0f4cd876debccaa5ad481d9` records that both
  enrichment and inspection are parked; monitoring/reports/workers are separate.

At this initial checkpoint, the installed formatter on Starship and Snap had SHA-256
`c303167309ee81d87f8c0c9dfbccb00891bad9232d78cf04f2cc88a90ae93694`.
It keeps Kitty as sender, uses “Terminal notification” as the title and moves
the existing preview text into the body. Identified/structured notifications,
existing bodies and explicit action/report protocols keep their own layout.
It does not infer an application, source host, thread or turn.

The three live inspector routes were removed from the backed-up 349d config;
the retired strict config key is absent. One scoped 349d restart selected the
generic-only code (PID 69987 to 3549424). The same paired board remains connected
at serial `28:84:85:92:C2:20`, boot `3592654970`, with firmware `0.2.0`, build
`b9046f4`, SHA `8f5ae24ba`. No firmware was flashed. Native config/auth bytes,
Codex daemon identities and other component selectors match the protected
pre-cutover snapshots. Existing Kitty processes/native sessions were not stopped.

## Validation

The host/tooling suite passes 420 tests with three live desktop tests skipped.
It retains generic history, action, replacement, body-formatting and pairing
coverage, with regressions proving obsolete metadata has no special behavior and
an old source revision cannot overwrite a generic card replacement. All six
[349 CI jobs](https://github.com/byebyebryan/esp32-349/actions/runs/38952176794)
pass at the selected host source.

The full dotfiles validator passes. Its formatter check runs through Kitty
0.49.2's actual OSC parser and notification manager, testing body/title layout,
originating-channel focus binding, identified Claude/OSC 99 messages, existing
bodies, report-only actions and unfocused-only suppression.

A newly owned Kitty process emitted local and Snap-over-SSH OSC 9 probes. The
desktop notification calls were captured with these exact fields:

| Desktop ID | Sender | Title | Body |
| --- | --- | --- | --- |
| 831 | kitty | Terminal notification | LOCAL_NATIVE_BODY_OK - readable body from Starship |
| 832 | kitty | Terminal notification | SSH_NATIVE_BODY_OK - readable body from Snap |

Both calls retain Kitty's default action. Device cache readback changed from
five to seven IDs during the two-probe sequence; it does not establish visible
text rendering. The owned desktop IDs were closed and the test Kitty stopped.
No Codex/model turn was created. Private fixture/rollback receipts are under
`~/.cache/native-notification-format-20261016` on the relevant host.

## Acceptance limits

At the initial checkpoint, physical body readability was pending user observation.
A new graphical click
test was not observed; originating-channel activation passed through the native
notification manager and the desktop action field is preserved. Historical
panel/touch and native TUI click checks retain their dated scope.

Kitty loads this script on process startup. Existing Kitty processes keep their
old handler, and a config reload does not replace it. Use a new Kitty process to
pick up formatting; a new OS window inside an old process keeps that process's
handler. The change fixes native preview layout, not full-response delivery,
agent labels or lifecycle handling. See the
[formatter guide](https://github.com/byebyebryan/dotfiles/blob/main/docs/kitty-notifications.md).

## Codex title follow-up and user confirmation

Dotfiles `31de898` changes the display title to “Codex”, keeping Kitty as sender
and the existing native preview in the body. It is installed on Starship and
Snap with SHA-256
`1a8dba4021b8394c0ba4a85b4b524499169aa648293d668e579503bb955b290f`.
The Kitty parser/notification-manager check passes on both hosts, and each
installed file matches its managed source.

A follow-up Starship OSC 9 probe captured desktop ID `841`, sender `kitty`,
title `Codex`, body `STARSHIP_NATIVE_CHECK - preview body`, and the default
action. The board cache gained ID `100015` during the probe. The exact owned
desktop notice was closed and the test Kitty stopped; no model turn was created.

Bryan initially saw no completion alert while Kitty remained focused. This is
the selected unfocused-only native policy. He subsequently confirmed that the
notification was visible and working. No new touch or graphical click test was
reported. Existing Kitty processes and Codex daemons were left running.
