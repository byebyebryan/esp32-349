# Codex completion notification investigation

Date: 2026-10-14. Initial investigation followed by the authorized execution loop.
Status: basic native TUI recovery selected on Starship and Snap; config-driven
completion hooks confirmed in a subsequent isolated study. Rich delivery routing
is reopened; physical acceptance remains separate.

This investigation retains its dated configuration and recovery checkpoints.
The [launcher checkpoint](notification-launcher-acceptance-2026-10-15.md) records
the subsequent terminal-associated daily rollout and current acceptance limits.

[Rich notification plan](rich-notifications-plan.md) · [Testing](testing.md)

## Report and current configuration

Codex completion notifications are missing from both the Starship desktop and
the 349, for Starship-local sessions and Snap sessions through SSH. Claude
notifications still work. This places the reported failure before desktop
mirroring, rather than solely in 349 presentation.

Both hosts currently run Codex CLI 0.162.1 with `daemon_auto_start = true`,
`notify = ["/home/bryan/.local/bin/codex-notify"]`, and native TUI notifications
restricted to `["approval-requested"]` using `osc9`. The installed helpers are
executable and byte-identical, with SHA256
`8a18780c3d2f1783c77813d51f875487159cf066892a7804ebfc8f155a33991e`.
The source helper's fourteen isolated regression checks pass.

## Terminal-context mismatch

The ordinary shared app-server daemon on each host has no controlling terminal
(`/proc` reports `tty_nr = 0`). Its standard input/output are `/dev/null`.
Neither daemon carries `TMUX`/`TMUX_PANE`. Terminal clients have their own PTYs;
that client terminal is not the daemon's controlling terminal.

The helper checks inherited Kitty indicators and chooses `/dev/tty` for direct
delivery, or a configured originating tmux pane when available. In the sampled
daemon environments it chooses `/dev/tty`. Opening that device from a detached
process fails with errno 6 (`ENXIO`, "No such device or address"). The helper
intentionally catches the error and exits successfully with no diagnostic.

A controlled reproduction on each host constructed a valid 178-byte Kitty OSC
notification packet, invoked the installed helper in a new detached session with
`TERM=xterm-kitty`, and observed exit zero with zero output. An independent
detached `/dev/tty` open confirmed `ENXIO`. It sent no desktop notification and
made no provider request or configuration change.

The installed CLI and Starship daemon binaries have SHA256
`628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1`.
Agent Observer's dated 2026-10-09 isolated native callback study records the same
binary and matching completion thread/turn callbacks on both hosts. That proves
callback support in that study; it does not establish current graphical delivery
or trace an ordinary failing invocation.

## Diagnosis and recovery direction

The detached helper is incompatible with daemon-context terminal delivery.
Combined with approval-only native TUI notifications, this strongly explains the
reported completion-alert loss. Existing source tests exercise an attached
controlling terminal and do not cover this ordinary detached topology.
No fresh ordinary completion hook was traced during this investigation, so the
causal diagnosis remains qualified rather than a claimed end-to-end repair.

A basic recovery candidate is to enable native TUI `agent-turn-complete`
notifications alongside approvals. Native TUI delivery uses the attached client,
making it suitable for a local terminal or SSH route. This must be checked with
focus filtering, desktop arrival and 349 mirroring before fleet selection.

The richer helper needs explicit thread-to-attached-client routing or a supported
client-side notification path. A shared daemon's inherited terminal environment
cannot establish the originating terminal. Provider, client and notification
identity must stay distinct, including multiple viewers and remote sessions.
Choose one completion publication path per covered context to avoid duplicates.

Current settings and helper deployment remain unchanged. No ordinary thread,
daemon, host service or firmware was restarted or modified. The only repository
changes are the design plan, this investigation note and the project guide link.

The paragraph above records the initial read-only investigation checkpoint.
The following execution findings supersede its current-deployment statement.

## Execution-loop correction and recovery

An embedded CLI probe invoked a supplied callback under its own TUI process with
a controlling PTY. It was not a loaded thread in the original shared daemon.
Shared/remote TUI probes completed and emitted native OSC 9, but the requested
capture files were absent. A separate owned-thread probe also had no capture.
The execution loop interpreted these results as missing callback dispatch and
selected native TUI completion notifications as the demonstrated recovery route.
That callback-dispatch interpretation was not established: the later isolated
config study below shows that the remote TUI override did not reach the server.
The older owned-thread probe likewise does not outweigh the new positive proof.

Starship's desktop admitted the controlled native alert. A second probe ran on
Snap over SSH in an owned Starship Kitty window; after restoring prior focus,
one native alert reached the desktop and the 349 admitted new notification
cards. Desktop payload metadata and device readback prove this transport path,
not physical panel readability or touch acceptance.

The canonical chezmoi candidate selects `notify = []` and
`tui.notifications = ["agent-turn-complete", "approval-requested"]` on Snap and
Starship. Other hosts retain their earlier helper policy. Full source checks pass.
Candidate `8d778f00fc125cf135e48e4c440af00cec18f87f` supplies a guarded projection
for only these two keys, because a normal full-config dry-run would also remove
unrelated app-owned state. Its four boundary checks pass.

The two-key update has been applied on both hosts with private backups, preimage
guards, semantic preservation of every other configuration value and exact
readback. Starship's resulting configuration SHA256 is
`6360b8efb69fa3758c0ea2732f32d3d8f159882b334faac0b5d7491ab863b865`;
Snap's is `c07ddd8b29be9914a45e7f5c1272e3031821bbd5db7b0925625692ad1a0e2043`.
Fresh TUI clients read the corrected settings. Existing native daemons, original
sessions, 349 host service and firmware were not restarted. Existing TUIs retain
their loaded preferences until their next normal launch.

The helper ownership move is independently recorded in codex-plus candidate
`6b1dbb6`, with all fourteen relocated behavioral checks and the producer inventory
checks passing. Its managed artifact selection has not changed. Rich delivery
will be qualified against the actual client event path rather than treating a
daemon's inherited terminal environment as an origin.

## Config-first correction after launcher review

Bryan questioned making `codex-notify run` part of ordinary launching and asked
that ongoing work and daemons remain untouched. The preferred integration is
again a normal Codex launch with a configured generic notification helper. The
relay launcher remains an opt-in candidate, not a required deployment choice.

A new isolated study used installed Codex 0.162.1, a private `CODEX_HOME`, an
owned Unix app-server, a private PTY and a loopback Responses fixture. No real
credentials or model provider were used. The matching upstream source revision
was `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`. Each configured hook wrote only
bounded event/context metadata into its own capture file.

| Controlled case | Observed callback |
| --- | --- |
| Server user config selects A; fresh RPC thread completes | A |
| Direct RPC `thread/start` explicitly selects B | B |
| Remote stock TUI uses `-c notify=B` against server config A | A, not B |
| User config changes from A to C; already loaded thread completes | A |
| Same running server creates a new thread after the config change | C |
| Original private thread unloads, then resumes and completes | C |

All six callbacks ran under the same private server PID with no controlling TTY.
A marker present only in the TUI environment did not reach its callback; the
server marker did. The TUI's `config_request_overrides_from_config` forwards a
restricted set of settings that excludes `notify`. Direct RPC config overrides
are a different path and did work. Consequently, no capture from the earlier
remote `-c notify=...` test was not evidence that configured hooks had stopped.

Config-driven completion hooks therefore work in the tested shared topology.
New threads read changed server-side user config without a daemon restart.
Already loaded threads preserve their hook setting; the owned unload/resume
case read the new setting. This is a bounded version/topology result, not a
reason to unload an ongoing user thread or a promise about every client/version.

The remaining demonstrated defect is delivery context: the configured helper's
`/dev/tty` route cannot work from a detached daemon. The payload supplies exact
thread/turn identity, client name, cwd and assistant message, but no originating
TTY or Kitty-window binding. A client name is not a unique client instance.
Fix event production and desktop routing as separate concerns:

- Keep the helper invoked by `notify` and add rich metadata there; its source
  remains codex-plus and its pinned distribution remains chezmoi.
- Evaluate an explicit desktop delivery destination for the config-driven helper,
  including Snap-to-Starship transport. This could avoid a launcher but must
  separately qualify focus filtering, Open, duplicate handling and failure policy.
- Preserving Open/focus to the originating terminal requires an exact client
  binding. Do not derive it from the daemon environment, recent activity, titles
  or response-text matching. The tested relay supplies a binding, but is only one
  candidate for doing so.
- Keep native basic alerts as the installed recovery while routing is unresolved.
  Do not enable a second completion publisher without defining duplicate and
  fallback behavior. The existing 349 metadata/inspection path can be reused by
  a different producer transport.

The private study cleaned up its owned server and fixture. Checks confirmed the
ordinary Starship daemon identity and native config hash were unchanged. No live
launcher, service, firmware or ordinary session was changed. Evidence is private
under `~/.cache/codex-notify-config-study-20261014`; prompts, terminal captures and
raw assistant bodies are not committed. This corrects callback conclusions in
the earlier checkpoint; it does not qualify a replacement rich delivery route.

## Config-driven desktop candidate execution

The next authorized goal loop implemented `codex-notify hook` and a configured
desktop receiver without selecting them as live defaults. Private stock-TUI and
native desktop-bus qualification pass, including exact metadata/body, duplicate
handling and a trusted exact-thread action. Actual owned-thread hook settings
also reached the Starship desktop locally and from Snap over SSH. Both source
cards retired in 349d after exact newer interrupted turns. Local card `100021`
reached the board, but USB disconnected before final deletion and SSH device
readback. See the detailed [acceptance follow-up](rich-notifications-acceptance-2026-10-14.md#config-driven-desktop-candidate-follow-up).

This route deliberately offers always-notify and optional new-terminal resume;
it cannot preserve originating-terminal focus without additional client binding.
The behavior choice remains pending. Loaded threads and fresh TUIs can read
different generations of settings, so an immediate two-key mode switch could
lose alerts on a still-loaded `notify=[]` thread. Current basic recovery, native
owners, live configuration and launchers remain untouched. No Observer dependency,
new resident notification service or firmware change was introduced.

## References

- [Official notification configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications): external `notify` and native TUI notification settings are separate paths.
- [Official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference): notification event filtering, method and focus condition.
