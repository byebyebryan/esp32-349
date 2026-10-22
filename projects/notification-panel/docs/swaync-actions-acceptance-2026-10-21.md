# SwayNC historical Open checkpoint — October 21, 2026

The initial checkpoint covers source and isolated native validation. The
Starship follow-up below records the later deployment and user-confirmed Open
focus result. The [integration guide](../integrations/swaync/README.md) defines
behavior and rollout.

## Source and artifacts

- SwayNC fork: `c7ac549615a773e4207618fa67eeff1ccd8f1760`, extending
  `1a63f7b` and the existing `7f1b612` Niri activation fix.
- The original `src/xdg_activation.vala` remains byte-identical to `7f1b612`:
  SHA256 `c8e03e5fe3e7b317b641490a388c36a997ec16a8d54347f57fe3fd0eb8af79d7`.
- Native binary rebuilt from the committed fork:
  SHA256 `920740976152a5f4be24fd6a2497af55b62c8b795edc6cc9cedcea355560dd25`.
- 349 host changes extend `717e378b7479fc576de4843cdf43cc4987cb1566`.
  A wheel built from this change includes the exact adapter, token relay,
  configuration, daemon, and notification-source bytes:
  SHA256 `916238bbe43704193abc45db1524ddfe9d21c316a489c265ff41e2aab17e5046`.

Build tools were extracted into an isolated cache from existing package
archives; no live package installation or desktop service restart was needed.
Local logs and checksums are under
`~/.cache/swaync-retained-actions-20261021/`.

## Automated and native evidence

The host/tooling suite passed **437 tests**, with the three desktop-bus tests
intentionally skipped by clearing `DBUS_SESSION_BUS_ADDRESS`. Private
dbus-broker regressions were required. Documentation links, source/media hashes,
and both repositories' whitespace checks passed.

The native SwayNC harness used a private D-Bus session with service activation
disabled, and a disposable Niri compositor nested inside Xvfb. It passed older
retained-card actions with a newer popup present, exact/missing actions,
identical and synchronous replacement, explicit closure, transient expiry,
resident behavior, external token forwarding/bounds, and ID reuse after daemon
restart. Stale revisions never invoked another notification.

The real 349 monitor and action manager passed against that native daemon:
an older expired card dispatched its own default action, a newer card remained
ready, and an identical request did not emit a second action.

A real-Kitty probe first exposed a separate Niri focus boundary: the original
GDK-generated token was delivered and Kitty requested activation, but no fresh
GTK input serial existed and focus stayed on the other window. The implemented
handoff uses Niri's existing IPC spawn token and SwayNC's checked
`InvokeActionWithToken` method. No Niri patch or direct window-focus command was
introduced.

The final proof used the rebuilt fork and a separate virtual environment with
the **noneditable host wheel** installed. Two real Kitty windows ran inside the
private compositor. A native OSC 9 notification expired, a newer card arrived,
and the real 349 adapter focused the original Kitty window without a fresh
desktop click. The newer card remained ready. The relay ran from the installed
wheel's Python environment, proving its packaging and import path as well as
the native action/focus path.

## Remaining acceptance at the initial checkpoint

No source was published, no Arch package pin was advanced, and no live
configuration, notification daemon, 349 service, firmware, or paired board was
changed for this checkpoint. Open remains disabled in live configuration.

Publish the fork/349 commits, advance and build the exact pinned Arch package,
and perform a scoped reviewed deployment before claiming deployed acceptance.
Then observe native local/SSH Kitty focus and the physical 349 Open control on
an older card after popup expiry with a newer card present. Existing rendering,
touch, pairing, and rollback boundaries remain in force.

## Starship deployment and physical focus follow-up

Later on October 21, the source was published and deployed through chezmoi.
The first deployed package (`swaync-bryan 0.12.6-2`) failed physical targeting:
Bryan reported that the test card focused existing Codex TUI windows instead of
the intended new terminal. That deployment was not accepted. Device Open was
briefly disabled while a private native test reproduced the failure.

Surviving Kitty processes retained old notification-ID mappings after SwayNC
restarted. Broadcast action/token signals could activate those stale mappings
when the new daemon reused a numeric ID. The correction captures each Notify's
bus-supplied sender and directs its token and action only to that unique client
connection. Identical replacements capture their current sender. The original
Niri input-serial helper remains byte-identical.

The corrected selection deployed on Starship is:

- SwayNC action source `1c1456869f648ee64ff273d68de2dee127e66e66`.
- Arch recipe `3c81532f1faeedc6e7b04d34bc52761a347d1477`, package `0.12.6-3`.
- 349 runtime/test source `541e7985fe3beb64e781859d1a8aba6dc567ec48`, installed
  noneditable from the managed archive; configuration selects `device_open = "swaync"`.
- Paired board `28:84:85:92:C2:20`, firmware `0.2.0`, build `b9046f4`,
  SHA `8f5ae24ba`, boot ID `39098759`; no firmware flash occurred.

The host suite passed 440 tests after the notifier-context regression fix, with
three live-desktop tests intentionally skipped and the private broker required.
The corrected installed fork and host passed the private native gate, including
old/new Kitty clients kept alive across a notifier restart, numeric ID reuse,
recipient isolation, identical replacement and exact new-window focus. Retained
older-card selection, newer-card preservation and duplicate suppression also
passed in the real 349 adapter. Published archives, installed modules and pairing
were checked for parity; repeat scoped apply preserved service PIDs.

After the fresh `349_HISTORY_OPEN_V3B` / `349_NEWER_REFERENCE_V3B` test, Bryan
confirmed: "it's focusing the right windows now". This establishes physical
349 Open to the intended Starship terminal windows for the corrected build.
Newer-card preservation remains covered by the native checks; the user's reply
specifically confirms focus. Existing Kitty, Codex and unrelated service
processes were preserved. Snap and Carbon were outside this rollout.

The dated [managed rollout record](https://github.com/byebyebryan/dotfiles/blob/main/docs/swaync-349-rollout-2026-10-21.md)
retains the initial failure, corrective pins and recovery paths. Local validation
receipts are in `~/.cache/swaync-recipient-fix-20261021/`.
