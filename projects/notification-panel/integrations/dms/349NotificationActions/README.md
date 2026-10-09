# 349 Notification Actions provider

This DMS daemon plugin exposes a small Quickshell IPC bridge for the current
`default` action of a retained desktop notification. It invokes the action on
the exact live wrapper, notification, and action objects that were bound. It
does not focus a window or claim that an application handled the action.

Run the shell commands below from anywhere in the `esp32-349` checkout.

## Install and scoped reload

Install this directory as
`~/.config/DankMaterialShell/plugins/status349NotificationActions`, then scan
and enable only this plugin:

```sh
plugin_root="$HOME/.config/DankMaterialShell/plugins"
plugin_dest="$plugin_root/status349NotificationActions"
project_root="$(git rev-parse --show-toplevel)/projects/notification-panel"
mkdir -p "$plugin_root"
if [ -e "$plugin_dest" ] || [ -L "$plugin_dest" ]; then
  printf '%s\n' "Plugin destination already exists; inspect it before replacing: $plugin_dest" >&2
  exit 1
fi
cp -a "$project_root/integrations/dms/349NotificationActions" "$plugin_dest"
dms ipc call plugin-scan scan
dms ipc call plugins enable status349NotificationActions
dms ipc call plugins status status349NotificationActions
dms ipc call 349-notification-actions status
```

Plugin discovery is asynchronous on this DMS build. If the first enable says
`NOT_FOUND`, poll `plugin-scan list` until the ID appears, then retry the scoped
enable. To reload changed files, use
`dms ipc call plugins reload status349NotificationActions`; do not restart the
whole shell. `status()` should return v1, the Quickshell PID and epoch, null
session/boot before the first bind, and an empty binding list initially.

## IPC contract

All methods return one JSON object encoded as a string. Pass one JSON-string
argument to `bind`, `activate`, and `release`; `status` takes no arguments.

- `status()` returns `{v,epoch,pid,session,boot_id,bindings}`. Each binding is
  `{id,rev,token,live}`. It checks at most 32 existing object references; a
  revoked token can remain listed with `live:false` while its exact live
  object remains eligible for a safe rebind.
- `bind(payload)` accepts v1 fields `session`, nonzero `boot_id`, `id`, `rev`,
  positive `source_version`, `desktop_id`, and raw `expected` strings
  `app`, `summary`, `body`, `default_label`. It returns `ready`, `unavailable`,
  or `stale`, with an opaque token only when ready. It requires one unique
  tracked notification with that desktop ID and exact raw identity, and one
  action whose identifier is exactly `default` and whose text equals the
  expected label. It never chooses the first action as a fallback.
- `activate(payload)` accepts v1 `epoch`, scope, `id`, `rev`, `token`, and a
  positive `request` number. It validates and invokes synchronously once.
  Results are `dispatched`, `unavailable`, `stale`, `failed`, or `unknown`.
  A 64-result cache and request high-water fence prevent duplicate dispatch;
  uncertain invocation results are cached as `unknown` and are not retried.
- `release(payload)` accepts scope, `id`, `rev`, `token`, and boolean `final`.
  `final:false` revokes the token but retains its exact object lineage;
  `final:true` removes that lineage and fences released local IDs. Delayed
  releases cannot affect a newer token.

Requests larger than 8192 UTF-8 bytes, unsupported versions, extra fields,
ambiguous defaults, duplicate tracked desktop IDs, and invalid identities fail
closed. Bindings and retained lineages are bounded at 32.

The installed API does not expose a per-Notify generation for identical
in-place replacements. The provider therefore guarantees dispatch against the
currently tracked exact object/action and rechecks its raw fields, tracked
state, action label, and reference immediately before invocation. It cannot
prove an invisible same-object Notify boundary that leaves all exposed
properties and action references identical.

## Controlled proof producer

After the plugin is enabled, run this only when a controlled test notification
is approved. It sends one test notification and records the assigned D-Bus ID,
`ActionInvoked`, and `NotificationClosed` events. The first action is `reply`
and the second is the `default` action, so the proof also checks action
selection. Interactive commands are `replace`, `replace-identical`, `new`,
`close`, and `quit`.

```sh
project_root="$(git rev-parse --show-toplevel)/projects/notification-panel"
rtk uv run --project "$project_root/host" --frozen \
  python "$project_root/integrations/dms/349NotificationActions/tools/controlled_notification.py" \
  --log-file /tmp/349-notification-actions.jsonl
```

The producer does not activate the action. A proof controller must bind the
reported ID with the live raw identity, activate only through the bridge, and
confirm an `ActionInvoked` event with action key `default`; no app-window or
focus behavior is implied.

The [live bridge proof](../../../tools/README.md#desktop-open-proof) exercises
binding/replacement/release, duplicate dispatch and a scoped provider reload
with owned fixtures. It is opt-in and separate from the offline provider tests.
