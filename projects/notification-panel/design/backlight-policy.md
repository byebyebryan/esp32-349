# Backlight policy

Implemented candidate: **2026-10-08**. Deployment and physical observations
are recorded in [backlight acceptance](backlight-acceptance.md) and the subsequent
[button/default-brightness checkpoint](backlight-buttons-acceptance.md).
The subsequent boost deployment is recorded in
[notification-boost acceptance](backlight-boost-acceptance.md).

## Behavior

Turn off the V2 LED backlight rather than sleeping the ESP32 or LCD controller.
USB reception, clock advancement, rendering and notification expiry continue.
The shared driver gates BL_EN at zero and uses its existing PWM for 1–100%.
Normal brightness defaults to 50%; a comfortable lower setting is a separate
physical preference, not a measured lifetime guarantee.

V2 feeds GPIO42 PWM through R52 (10 kΩ), C73 (100 nF) and R49 (39 kΩ) into
the AP3032 feedback network with R48 (5.1 kΩ). This is filtered analog dimming,
as described in the [AP3032 datasheet, section 5.2](https://www.diodes.com/datasheet/download/AP3032.pdf).
Using the schematic values, nominal 200 mV feedback and 3.3 V GPIO, the inferred
LED-current cutoff is about 64.29% high duty. A full-range inverted mapping
therefore made the old 25% step dark. The corrected mapping uses that usable
range: 25/50/75/100% select duty counts 123/82/41/0 out of 256 at 50 kHz.
Percentages describe nominal fractions of full LED current, not measured lux
or a calibrated perceptual scale; component tolerances affect actual output.
The corrected 50% setting can look brighter than the earlier 50% mapping.

The firmware starts with a 300-second grace period. Recognized host traffic
renews that deadline; USB presence alone does not. After 300 seconds without
traffic it turns the backlight off, including when a powered host stops
`349d`. Existing ten-second stale indicators remain visible until then.
Diagnostic `hello` and `cards_query` requests do not renew the deadline.

A known host screen-off state takes effect without that grace period.
Heartbeats, notifications and syncs do not override it. After a timeout,
managed firmware waits for fresh display control before lighting up, so a
reconnect probe cannot wake it ahead of the host's current screen-state replay.
An older host that never sends display control can wake it with normal traffic.

Darkness cancels pointer input. A finger held across wake must be released for
three clear controller samples before a new press is accepted. Touch and new
notifications do not override screen following or the disconnect timeout.

## Physical controls

The brightness/BOOT button (GPIO0) cycles the selected level through
25 → 50 → 75 → 100 → 25%. Starting at the default 50%, the first click selects
75%. A custom level advances to the next higher step. Selection also works
while dark, without waking the bar.

The Power button (GPIO16/SYS_OUT) toggles a manual-off latch. Turning it back
on returns to automatic mode: the bar remains dark while host screens are off,
while awaiting host control or after the disconnect timeout. Local buttons
do not renew host liveness. The firmware keeps the processor and USB running
and does not operate the battery power latch.

Clicks occur on release after 30 ms of debounce; polling runs every 20 ms.
Holding a button does not repeat. A button held at startup must be released
before its first usable click. RESET is wired directly to CHIP_PU and retains
its hardware restart function. BOOT held during reset retains the ROM download
function. The [official V2 schematic](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-3.49-V2/blob/1c157e6e8e68b89fd4dc400f46bf1724cb64a57e/schematic/ESP32-S3-Touch-LCD-3.49%20V2.pdf)
shows these inputs and no ambient-light sensor; brightness is manual.

Selected brightness and manual-off live in RAM. Ordinary syncs, heartbeats,
screen transitions and daemon reconnects preserve both. Changing the host's
configured brightness replaces the selection but preserves manual-off;
replaying the same configured brightness does not replace a local choice.
Reset or power loss clears the choices and starts at 50%, then applies any
different configured host brightness when the daemon reconnects. No button
press writes flash or edits daemon configuration.

## Notification boost

A fresh live notification, including a genuine desktop replacement, boosts the
backlight to 100% for 30 seconds by default. Each accepted arrival restarts the
timer. Expiry restores the selected brightness, without changing that selection
or the host baseline. Routine sync, cached-card replay, reconnect and presentation
replay never start or extend a boost. A busy stream of arrivals can keep it at
100% until 30 seconds after the last arrival.

Manual off, host screen off, disconnect timeout and awaiting host control take
precedence. Going dark cancels the timer; arrivals while dark do not queue a
boost for wake. Brightness clicks cancel the timer and cycle from the selected
level, so a boost from 50% followed by a click selects 75%. Changing the host
baseline or boost duration also cancels an active boost. Same-value replay
preserves its original deadline. Notifications without a usable cached card or
whose attention has already expired do not trigger it.

`display.notification_boost_s` accepts integer 0–86,400 seconds, defaults to 30,
and disables the feature at zero. Firmware advertises `backlight-boost-v1` in
addition to `backlight-v1`. Only a capable peer receives `boost_s` in display
control and the ephemeral `boost:true` field on live `notify` frames. The marker
is stripped from retained host state and never appears in a full sync. Firmware
honors it only after accepting a live card, rather than for `present` or cached
snapshot commands. Readback adds configured `boost_s`, `boost_remaining_ms` and
reason `notification_boost`; selected `brightness` remains unchanged.

## Host screen state

`349d` reads Linux DRM connectors once per second in a separate worker. It
ignores disconnected connectors and writeback devices. Any connected connector
with `enabled` and DPMS `On` means on. If every readable connected connector is
off or disabled, the aggregate is off. Missing, unreadable or unrecognized
state is unknown unless another connector is known on.

The kernel files are read without opening a graphical-session socket or
changing monitor configuration. Actual off/on transition verification is
required on each deployed host. This follows the operating system's display
power, not session lock alone or an undetectable physical monitor power switch.
No connected monitors is unknown. Unknown preserves the board's previous
screen decision; a newly booted board starts on. Source uncertainty is exposed
in `349ctl status`, rather than silently treated as off.

## Configuration and protocol

`[display]` accepts `brightness_percent` (integer 1–100),
`disconnect_timeout_s` (integer 1–86,400) and `follow_host_screen` (boolean).
Defaults are 50, 300 and true. Reload sends the updated policy without changing
pairing. Settings live in daemon configuration and board RAM, without repeated
flash writes.

Firmware advertises the independent `backlight-v1` capability. Only capable
peers receive this command:

```json
{"t":"display","on":false,"brightness":50,"disconnect_s":300}
```

`on` is boolean or null; null retains the previous screen state. All three
fields are required and validated atomically. Invalid settings leave the old
policy intact and request resync with `display_invalid`. The daemon sends
changes and replays control before each full sync under the same wire lock.
Older firmware receives no display-control frames.

`cards_status.backlight` reports the applied percentage, policy target,
selected `brightness`, configured `host_brightness`/timeout, remembered screen
state, `manual_off`, debounced button levels/click counters, and reason: `on`,
`manual_off`, `host_screen_off`, `disconnected`, `awaiting_host` or
`notification_boost`. Boost-capable firmware also reports configured duration
and remaining milliseconds.
The firmware also advertises `backlight-buttons-v1` for the physical controls.
Readback does not renew
the timeout. Applied means the driver call succeeded; it is not an electrical
measurement or a physical observation of light output.

## Validation

Host tests cover multi-monitor aggregation, the closed laptop case, unavailable
files, unknown states, validation/reload, capability gating, change-only
transmission, replay order and retry. Production native protocol checks cover
valid/invalid commands and inert readback. The firmware policy checks exercise
the exact five-minute boundary, legacy wake, controlled reconnect, unknown
state, long timeouts, local-selection precedence, manual-off persistence,
button bounce/hold/startup handling, boost expiry/cancellation and touch
suppression across wake. Both
applications must build because the input support is shared board support;
the physical-button API is opt-in and render-bench does not enable it.

Physical acceptance requires seeing the Snap bar go dark and wake, including
after the full five-minute daemon-loss interval. Serial readback and
monitor-power snapshots establish their respective code paths only.
