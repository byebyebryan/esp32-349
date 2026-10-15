# Config-driven rich notification checkpoint — 2026-10-15

The local and Snap SSH cases now pass exact device-cache admission and removal
readback. Physical rendering and touch were not observed. This follows the
[October 14 checkpoint](rich-notifications-acceptance-2026-10-14.md), whose dated
source tuple and earlier disconnected-board limits remain unchanged.

## Selected behavior

Bryan selected **unfocused-only alerts and original-terminal Open**. Ordinary
Codex launches retain native basic notifications. The config-driven rich desktop
route stays experimental until native client support can supply that binding;
always-notify delivery and opening a new terminal are not selected defaults.
No replacement launcher is required.

The current server-side completion callback has no attached-client binding.
Changing hook types does not provide one. Do not infer the originating terminal
from titles, timing, focus, process ancestry or the daemon's inherited environment.
The optional relay and explicit desktop route remain separate experiments.

## Installed tuple and preservation

| Component | Readback |
| --- | --- |
| Producer/inspector | codex-plus `7d05b07ffc82c76ba62bee52f9c8c0edf2f8894b`, [preview.1](https://github.com/byebyebryan/codex-plus/releases/tag/codex-notify-v0.1.0-preview.1); twelve manifest files and executable flags match on both hosts |
| Source archive | SHA-256 `8b1f2f315d11b6b8978eae415e3c937b4c83d93f7ddbdcf0e95a789525aca262` |
| Managed distribution | Chezmoi runtime pin `ee14acb`; helper selection and rollback/reselection were qualified on October 14; no deployment in this follow-up |
| 349 host | Existing `c1f24f6` implementation with `74b5920` diagnostics; no host runtime or firmware change |
| Board | `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_28:84:85:92:C2:20-if00`; boot ID `162624908` before and after both cases |
| Native owners | Starship PID `17438`, start ticks `114655`; Snap PID `62725`, start ticks `107740`; both still 0.162.1 |
| Native preferences | `notify=[]`, completion and approval TUI notifications, OSC 9; unchanged on both hosts |

The stable helper command selects the preview on each host, but no global
`~/.config/codex-plus/notify.toml` route is installed. Native configuration hashes
remain Starship `6360b8efb69fa3758c0ea2732f32d3d8f159882b334faac0b5d7491ab863b865`
and Snap `c07ddd8b29be9914a45e7f5c1272e3031821bbd5db7b0925625692ad1a0e2043`.
Both owned probe threads were archived and their probe processes exited.
No native owner, live service or board was restarted, and no firmware was flashed.

## Controlled native-to-device sequences

Each case used a fresh owned RPC thread on the existing native owner, with only
that thread's hook configuration overridden. The installed helper published to
an explicit private desktop destination, locally or through Snap-to-Starship
SSH. These are actual-owner/device checks, not installed-default normal-launch
acceptance. The stock TUI/user-config route was qualified separately against the
private native fixture recorded in the preceding checkpoint.

| Case | Desktop receipt | 349 card | Progression result |
| --- | --- | --- | --- |
| Starship local | Sender `Codex`, title `Codex @ starship`, separate body `OCT15_LOCAL_BODY_349_OK` | `100053` | Present after presentation expiry; absent after a newer interrupted turn |
| Snap SSH | Sender `Codex`, title `Codex @ 80H1VV3`, separate body `OCT15_SSH_BODY_349_OK` | `100054` | Present after presentation expiry; absent after a newer interrupted turn |

Each receipt carried the exact host/instance/thread/turn metadata. There was one
completion notification per case. After 31 seconds, the card's panel presentation
had expired while both rich host association and device history still retained
it. A separate native client then started and interrupted the next turn of the
same thread. Scoped descending native history established progression; both host
association removal and exact device-cache absence were verified afterward.
The board boot ID stayed the same throughout.

Private receipts and bounded harnesses are under
`~/.cache/codex-notify-acceptance-20261015` on their owning hosts. Raw callbacks,
model responses and desktop captures are not committed.

## Acceptance limits

- Bryan did not observe the tests. Serial/cache evidence does not establish
  readable rendering, visible removal, touch or physical Open behavior.
- DMS emitted no desktop expiry signal. The expiry evidence concerns the 349's
  presentation timer, not observed disappearance of a graphical desktop popup.
- The test desktop route configured no Open action. The existing DMS panel Open
  provider remains unavailable. Private action-command correlation from October
  14 does not establish graphical focus or original-terminal Open.
- The selected supported path keeps native basic alerts. Rich default activation,
  safe adoption across loaded threads and the required client binding remain
  future work. Do not disable native completion alerts while loaded threads can
  still retain `notify=[]`.
- Source reviews remain drafts. Canonical-checkout convergence and any later
  rich activation are separate release steps; this checkpoint changes docs only.
