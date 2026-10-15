# Rich notification candidate acceptance — 2026-10-14

This is a dated installed-candidate checkpoint. Physical panel/touch acceptance
remains pending. The [guide](rich-notifications.md) describes usage and failure
behavior; the [plan](rich-notifications-plan.md) records the agreed scope.
Subsequent launcher review reopened config-driven routing; the correction below
does not invalidate the opt-in relay's recorded native/device sequences.

## Selected tuple

| Component | Candidate/evidence |
| --- | --- |
| Codex native runtime | 0.162.1; original Starship daemon PID 17438 and Snap PID 62725 preserved |
| Standalone producer/inspector | codex-plus `fa4633084a1adbdcb7882ac35e0b5a953b3536db`; [review](https://github.com/byebyebryan/codex-plus/pull/4) |
| Fetched source archive | SHA-256 `c438eaacafe9aa0bd73fc22690110d2f577a2fc12a27c6e7094aa61a14ef6172`; ten installed files match on each host |
| Managed selection | chezmoi `6639d07`, independent notification external and helper symlink; [review](https://github.com/byebyebryan/dotfiles/pull/4) |
| Notification-only recovery | Guarded two-key updater `8d778f0`; native completion/approval alerts selected on Starship and Snap |
| 349 host | Implementation `c1f24f6`, bounded status diagnostics `74b5920`; [review](https://github.com/byebyebryan/esp32-349/pull/1) |
| Starship 349 board | Stable USB serial `28:84:85:92:C2:20`; device boot ID `226636151` unchanged across host selection/rollback |
| Firmware | Unchanged; existing notification/history/close wire used. No flash or firmware binary readback in this loop |

The source reviews are drafts, not merged main releases. Live helper selection
was applied from the exact isolated chezmoi candidate, preserving concurrent
canonical checkouts. Snap received an exact source archive in private staging.
Broad future application from the older canonical chezmoi checkout would select
its older configuration/helper until this candidate is incorporated there.

## Verified behavior

- **Basic recovery:** fresh stock shared/remote TUIs, with installed defaults and
  a deliberate focus-away before a separate-client turn, produced native desktop
  alerts locally and over Snap SSH. Earlier direct desktop/349 serial admission
  also passed. The recovery choice used an inference of shared/remote callback
  non-publication that the later config study corrected; basic native delivery
  itself remains demonstrated.
- **Local rich loop:** one desktop notification per completed turn carried the
  exact host/instance/thread/turn identity. Card `100011` remained after popup
  expiry (`notifs=0`, retained history present); a separate client started T2,
  T1 retired, and readback contained newer card `100012`.
- **SSH rich loop:** Snap's source hostname is `80H1VV3`. Card `100015` remained
  after popup expiry, then T2 started through a separate Snap client. Readback
  removed `100015`, retained new `100016`, and preserved the local thread's card.
- **Tmux:** a dedicated owned server with `allow-passthrough on` delivered exact
  rich metadata through its originating Kitty client. Its server was cleaned up.
- **Post-rollback selection:** fresh installed local and SSH rich runs again
  reached desktop and exact board-card readback, IDs `100000` and `100001` in the
  new 349d session, with the same board boot ID.
- **Cost:** one local outstanding-card inspection measured 25,780 KiB peak RSS,
  0.1107 CPU seconds and 0.1174 wall seconds. It exits between five-second polls;
  no query process is launched without matching outstanding cards. This is one
  measured sample, not a worst-case fleet benchmark.

## Source checks and rollback

The host suite passes 428 tests, including malformed/future metadata fallback,
source scope, native uncertainty, duplicates without renewal, stale replies,
queued newer replacements, bounded stdout and subprocess cancellation/reaping.
The producer passes 22 notification tests and five inventory-validator tests;
repository inventory checks pass. Chezmoi's full checks, pin checks and four
guarded-update tests pass. Documentation links and both media manifests verify;
firmware inputs were unchanged, so no firmware build or media regeneration was
required.

On both hosts, the old helper bytes were restored and verified, the prior two
notification values were projected without changing other settings, then the
candidate helper and native preferences were reselected with exact readback.
Final native-config SHA-256 values are:

- Starship: `6360b8efb69fa3758c0ea2732f32d3d8f159882b334faac0b5d7491ab863b865`
- Snap: `c07ddd8b29be9914a45e7f5c1272e3031821bbd5db7b0925625692ad1a0e2043`

The 349 host/config rollback ran prior source `7ac6dfb` from an isolated checkout,
verified a generic card on the same board, then removed the temporary unit drop-in
and reselected `74b5920` with both rich routes. Config was restored byte-for-byte.
RLCD dashboard, Observer and Mesh service PIDs/start identities did not change.
Only 349d was restarted. Original Codex daemons and user sessions were preserved.
Backups and raw private diagnostics remain under
`~/.cache/codex-notification-loop-20261014` on their owning hosts.

## Explicit remaining gates

- Physical rendering, readability, touch and observed card dismissal require
  Bryan's confirmation; serial IDs do not establish those outcomes.
- The pre-existing DMS Open provider reports unavailable. Native terminal focus
  action metadata remains present, but panel Open is not accepted by this loop.
- Rich mode is opt-in through `codex-notify run`. Ordinary launches retain basic
  native alerts. Embedded/no-daemon, fork, noninteractive commands and alternate
  remote routing are outside this rich adapter's qualified scope.
- Concurrent-viewer UI behavior, graphical focused-suppression/Open activation,
  native approval interaction and additional native versions were not exercised
  end to end. Packet/config preservation and deterministic suppression checks are
  narrower evidence. There is no global desktop single-viewer arbitration;
  349 episode replay protection is bounded and RAM-only.
- Draft review/merge and subsequent canonical-checkout convergence remain release
  workflow steps. The installed candidate is pinned and reversible independently.

The test monitor initially disconnected after its first observed D-Bus method
call; that harness was corrected to consume monitored calls without replying.
Only completed runs with exact native and board identities support the sequences
above. Raw callback bodies and terminal captures are not committed.

## Subsequent config-first correction

Bryan prefers ordinary launching over requiring `codex-notify run`. An isolated
study with the same installed native version demonstrated configured completion
hooks under a detached shared app-server, including a stock remote TUI. Its
`-c notify=...` override was not forwarded to the server, explaining why the
earlier remote capture test did not establish missing hook dispatch. New threads
read changed server-side user config without a restart; a loaded thread retained
its hook until the tested unload/resume boundary.

All callbacks inherited server context and had no controlling terminal. The
helper still needs an explicit delivery route; the successful relay is optional
evidence, not acceptance of a launcher change. Config-first local/SSH delivery,
focus/Open and duplicate/fallback behavior remain unqualified. See the full
[incident correction](codex-notifications-incident.md#config-first-correction-after-launcher-review).
Only private owned test processes were started/stopped; ordinary daemon identity
and config hash were unchanged. No installed source or preferences were updated.

## Config-driven desktop candidate follow-up

The second goal loop implements an opt-in codex-plus `hook` / `desktop` route.
The source candidate is `7d05b07ffc82c76ba62bee52f9c8c0edf2f8894b`; it has not
replaced the independently pinned installed helper in the selected tuple above.
It keeps normal launching and publishes a bounded rich envelope to a configured
desktop, locally or over SSH. It requires explicit always-notify behavior. Open,
when configured for an exact source, resumes the native thread in a new terminal;
it does not claim to find or focus its original viewer. Those semantics remain a
pending choice, so global native settings, managed routes and helper pins were
not changed by this checkpoint.

| Evidence | Accepted scope |
| --- | --- |
| Private stock 0.162.1 TUI, private server/user config, loopback model and private notification bus | Config hook reaches native `notify-send`; metadata/body survive the 349 parser; duplicate admission does not republish; default action passes the exact UUID to a trusted configured command |
| Internal title-generation completion | Excluded by positive read-only native user-thread classification; absence of an SQLite child record is insufficient |
| Actual Starship shared owner, fresh owned RPC hook configuration | Desktop arrival, exact rich association and board card `100021` admitted before disconnect |
| Actual Snap shared owner, explicit SSH desktop destination | Desktop arrival and exact rich host card `100022`; board was already disconnected |
| Scoped later turns | Both earlier host cards retired after their panel presentation expired with history retained; native descending history established exact newer interrupted turns |
| Preservation | Original Codex owner identities and config hashes unchanged on both hosts; all owned live test threads archived; no service, firmware or launcher change performed |

The actual-owner tests configured only owned RPC threads; they are not installed
default/normal-launch acceptance. The stock-TUI config path was proved separately
against the private fixture. The initial live harness incorrectly required a DMS
desktop-close signal; it was corrected to distinguish panel timer expiry from
graphical popup observation. The later progress checks continued only those same
owned archived test threads, then re-archived them. No unrelated thread was used.

The board USB link dropped before final local deletion readback and before SSH
board acceptance. Host retirement does not establish that the disconnected board
removed a card. Device deletion/readback and physical rendering/touch remain
pending. DMS popup expiry, graphical Open and focused suppression are unaccepted.
No installed default change or restarted owner was used to work around these
gaps. Private evidence is under `~/.cache/codex-notify-config-loop-20261014`.

Native adoption also needs care: a newly opened TUI may read approval-only alerts
while its loaded thread still retains `notify=[]`, causing missing completions.
Keep basic recovery until the behavior and safe transition are selected; do not
restart or unload ongoing work to force adoption.
