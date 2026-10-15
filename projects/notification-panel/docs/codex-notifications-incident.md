# Codex completion notification investigation

Date: 2026-10-14. Initial investigation followed by the authorized execution loop.
Status: basic native TUI recovery selected on Starship and Snap; rich client
routing and physical acceptance remain separate.

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

Fresh native evidence distinguishes two execution paths. An embedded CLI probe
invoked a supplied callback under its own TUI process with a controlling PTY.
It was not a loaded thread in the original shared daemon. A shared/remote TUI
probe completed on each host and emitted native OSC 9, but did not invoke the
supplied external callback. A separately created owned shared-daemon thread also
completed without invoking its configured callback. Thus detached `/dev/tty`
failure is real but does not, alone, establish the reported ordinary causal path.
The tested shared/remote path lacks the expected external-callback delivery;
native TUI completion notifications provide the demonstrated recovery route.

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

## References

- [Official notification configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications): external `notify` and native TUI notification settings are separate paths.
- [Official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference): notification event filtering, method and focus condition.
