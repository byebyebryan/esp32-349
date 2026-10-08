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

[display]
brightness_percent = 50
disconnect_timeout_s = 300
follow_host_screen = true
notification_boost_s = 30   # 0 disables temporary 100% brightness on arrivals

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

Display brightness accepts integer 1–100; the daemon-loss timeout accepts
integer 1–86,400 seconds. Defaults are 50%, 300 seconds and screen following on.
An existing explicit brightness setting continues to take precedence over the
default. With screen following enabled, the backlight is off
when all known host monitors are off and on when any is on. Unknown monitor
state preserves the board's previous screen decision. These settings require
firmware advertising `backlight-v1`; older firmware keeps its existing
brightness behavior. See [the backlight policy](../design/backlight-policy.md)
for timeout, reconnect and screen-state details.

On button-capable firmware, the brightness button chooses 25/50/75/100% in RAM.
Routine daemon syncs and reconnects preserve that choice. Changing
`brightness_percent` replaces the local selection; reloading the same value
does not. The Power button's manual-off latch survives either kind of reload.
RESET/power loss clears both local choices, restoring the default and then the
configured host level on reconnect. Power-on returns to automatic screen and
timeout following; it does not force a dark host's bar to light up.

`notification_boost_s` accepts integer 0–86,400 seconds. With boost-capable
firmware, each fresh accepted notification raises brightness to 100% for this
duration, then restores the selected level. Replays do not trigger it. Off
decisions and Brightness input cancel it; arrivals while dark do not queue a
later boost. Changing the duration cancels an active boost. This setting defaults
to 30 seconds and can be disabled with 0.
