# SwayNC selected-card Open

The optional `swaync` backend opens a retained card's original desktop default
action through Bryan's [SwayNC fork](https://github.com/byebyebryan/SwayNotificationCenter).
It requires the fork's `GetNotification(id)` and
`InvokeAction(id, revision, action_key)` methods on `org.erikreider.swaync.cc`,
plus `InvokeActionWithToken(id, revision, action_key, activation_token)` for Niri.
The original Niri activation fix alone does not provide those methods.

After installing a build containing the selected-card API, opt in with:

```toml
[notifications]
device_open = "swaync"
```

The default remains `off`; upstream SwayNC and Mako do not provide this API.
The `dms` backend remains available for existing installations. Selection never
falls back to another provider or the latest popup. The board needs the existing
`notification-actions-v1` capability; there is no new firmware protocol.

## Identity and lifetime

The existing notification monitor proves the original Notify reply's server
owner, PID, desktop ID, source version, and raw default-action metadata. The
adapter addresses that unique owner directly, checks the native snapshot against
those original fields, and binds its opaque per-Notify revision. At dispatch,
SwayNC atomically checks that revision and invokes that exact action. Replacements
after binding invalidate the revision even when the visible text is identical.
Known owner, source, or USB session changes invalidate the local binding.

Popup expiry leaves ordinary SwayNC notifications actionable while they remain
retained in the same daemon. The 349 still applies its independent ten-minute,
32-card history policy. Explicit closure, transient expiry, server restart, or
missing default actions leave Open unavailable. Body text, links, and arbitrary
alternative actions are never used as substitutes for a default action.

Local bindings are bounded to 32. The host retains the existing USB request
ledger and also reserves native activation requests before sending them; lost
replies remain unknown and are never automatically retried. Backend changes
route outstanding releases to the provider that issued the binding. Provider
status reports the selected backend separately from notification-source health.

Snapshot admission still compares the current server metadata: an identical
replacement arriving before the initial binding can be indistinguishable from
the monitored arrival until its monitor event is processed. This boundary also
existed in the DMS provider. Once bound, the native revision closes that race
for every later replacement. A dispatch acknowledgement does not establish
window focus; application and Niri behavior require separate acceptance.

On Niri, a physical device Open has no fresh GTK input serial. The adapter uses
the session's `NIRI_SOCKET` and Niri's supported IPC `spawn` action to give a
short-lived Python relay a compositor-issued activation token. The relay returns
it over a private one-shot Unix socket, validated by user identity and a request
nonce. It starts no window. SwayNC forwards that token to the exact original
application, which requests focus normally. No guessed window ID, title matching,
synthetic input, compositor patch, or focus-policy relaxation is involved.
The handoff is bounded to 1.5 seconds within the existing two-second activation
deadline; its socket/process resources are cleaned up on failure or cancellation.
Other compositors keep the existing token path. Automatic source/status polling
never launches the token relay.

## Validation and rollout

Host unit/regression tests run through the normal
[test command](../../docs/testing.md). For a native proof using the actual 349
notification monitor and action manager, run from the SwayNC fork checkout:

```sh
python3 tests/notification-actions.py --binary /path/to/build/src/swaync \
  --client-test uv run --project /path/to/esp32-349/projects/notification-panel/host \
  --frozen python /path/to/esp32-349/projects/notification-panel/tools/prove_swaync_open.py --kitty-focus
```

The disposable Niri/private-bus harness verifies that an older card remains
actionable after popup expiry, invokes its own action while a newer card stays
ready, and does not repeat a duplicate request. With `--kitty-focus`, two real
Kitty windows run inside the disposable compositor: a native OSC 9 alert expires,
a newer card arrives, and Open through the real 349 adapter must focus the
originating Kitty window without a fresh desktop click. This is native acceptance
with synthetic content; physical display/touch and deployed-desktop acceptance
remain separate.

Publication, advancing the pinned Arch package, installing/restarting SwayNC,
and applying/reloading the 349 configuration are separate rollout steps. Keep
the prior package and configuration for rollback. After deployment, observe
native Kitty focus and then the physical 349 Open button on an older card after
its popup expires, with a newer card present. Preserve the existing paired board,
other cards, and unrelated services throughout those checks.
