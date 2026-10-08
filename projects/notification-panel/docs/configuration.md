# Configuration

[Project overview](../README.md)

For installation, pairing and CLI commands, see the [setup guide](setup.md).
For retention, touch controls and displayed readings, see the
[behavior reference](behavior.md).

`~/.config/349d/config.toml` (or `349d --config FILE`):

```toml
[link]
# port = "/dev/serial/by-id/..."   # optional override; default: saved pairing

[daemon]
tick_s = 1.0
sync_interval_s = 60.0

[notifications]
mode = "mirror"            # mirror | off (consume is not implemented)
device_dismiss = "local"   # local | propagate
device_open = "off"        # off | dms; opt in after installing the DMS action plugin
max_visible = 3                 # legacy firmware snapshot cap
cache_limit = 32                # newest retained cards; active cards on older firmware
retention_s = 600                # history mode: 10-minute age limit from arrival/replacement
popup_timeout_ms = 10000          # fallback when an app requests server default (-1)
critical_popup_timeout_ms = 0     # 0 keeps critical cards until closed
ignore_apps = ["KeePassXC", "Bitwarden", "1Password"]

[bar]
# Horizontal zones for the legacy UI; the dashboard rail is typed.
preset = [
  { id = "clock", kind = "clock", w = 80, format = "%H:%M" },
  { id = "spacer", kind = "spacer", w = 0 },
  { id = "cpu", kind = "text", w = 60 },
  { id = "mem", kind = "progress", w = 70 },
  { id = "vol", kind = "progress", w = 70 },
  { id = "batt", kind = "text", w = 60 },
]
```

`max_visible` is limited to 0–8 for older firmware. `cache_limit` is limited
to 0–32 for firmware advertising `card-sync-v1`; its default is 32. A value
of zero keeps no cards on the device while still reporting their retained
count (active count for older firmware).
The bar preset can contain at most eight zones. The configured widths must
fit the 624 px content area including 8 px gaps; a nonspacer with `w = 0`
uses 60 px and a spacer uses flex space.
Invalid configuration is rejected rather than silently dropping zones or
sending a frame the device cannot accept. Reload after editing with
`349ctl reload` (or `systemctl --user reload 349d`).
