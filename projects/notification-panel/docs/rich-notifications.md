# Notification enrichment is parked

As of 2026-10-16, Codex notification enrichment and thread-aware handling are
parked. The inspector, metadata decoder, rich-arrival worker, replay associations
and native-history routes are retired. The panel mirrors desktop notifications
generically; normal retention, manual dismissal and existing desktop action
identities remain independent of agent state.

Remove `notifications.rich_sources` before selecting the generic-only host source.
It is no longer a supported configuration key. Chezmoi retires the separately
pinned inspector and its command selector. Old immutable helper/inspector releases
may remain for ongoing processes and rollback; they are not an active feature.
Native Codex alerts retain their attached-terminal route.

Current work is [native terminal formatting](https://github.com/byebyebryan/dotfiles/blob/main/docs/kitty-notifications.md)
in Kitty/chezmoi. It changes title/body layout, without creating a Codex daemon
connection, attaching thread metadata or guessing an identity from text/timing.

The [October 15 record](https://github.com/byebyebryan/esp32-349/blob/131cdb15f65cb5e00ee05963a8d1e559155f8814/projects/notification-panel/docs/rich-notifications.md)
retains the earlier implementation, source identities and validation. Existing
[acceptance records](notification-launcher-acceptance-2026-10-15.md) retain their
dated evidence and physical observations. Reopen enrichment only when a producer
can supply exact identity while preserving native TUI/focus behavior.
