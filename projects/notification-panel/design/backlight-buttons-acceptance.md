# Backlight button checkpoint

Candidate date: **2026-10-08**; base source `b188cf6` with uncommitted changes.
This follows the earlier [backlight policy deployment](backlight-acceptance.md)
and records the [current controls/default contract](backlight-policy.md).
Hardware writes and live checks target **Snap only**.

## Automated evidence

- Host/tooling suite after the reconnect repair: **413 passed**.
- Native Debug and Release: **15/15 CTests each**; policy/debounce and production
  parser/state checks cover local-selection retention and manual-off persistence.
- ESP-IDF **5.5.3** builds passed for notification-panel and render-bench.
- Snap's focused host display/config/discovery checks: **60 passed**.
- Presentation assets were regenerated with Pillow **12.0.0**, with unchanged
  PNG/GIF bytes and updated source/build hashes. Documentation/media validation
  and `git diff --check` passed.

## Deployment evidence

Snap's paired V2 board, USB serial **28:84:85:92:C4:3C**, received an
application-only write at `0x10000`, verified by esptool. The build is
`b188cf6-dirty`, with application SHA-256
`7d741a5222efb4dd0cff1244b3eafdb0d104cceee6a8be33ce79148283c08f66`
(3,825,664 bytes) and ELF SHA-256
`b3c9d41bba5e070dab880bb736719bc997caa5c2cb8411ca22841b60508206c7`.

The fresh 16 MiB backup is retained privately under
`~/.local/share/esp32-349/backups/buttons-snap-20261008T213609Z/` on Snap,
SHA-256 `a94fedf9951d6410ec022c15d393baa4fe120a0bb85c5fe14fcea39f6b14fb3c`.
Its application matches the previous checkpoint and the partition table matches
the candidate. Source, application/partition artifacts, configuration, pairing
and the service unit were backed up before writes.

After the daemon restart, a pre-existing Linux port-owner scan blocked before
opening the tty; one worker was waiting for a filesystem response. The scan now
reads descriptor links first and follows device paths, avoiding unrelated
filesystem descriptors while retaining tty owner detection and kernel tty
exclusivity. A regression verifies that unrelated filesystem metadata is never
followed and a real tty owner is still found. The repaired daemon connected on
the same boot **1828867666** without a further board reset.

Initial live readback: 50% applied/target/selected/host baseline, `manual_off=false`,
both buttons released with zero clicks, and `backlight-buttons-v1` advertised.
The first full 300-second trial was interrupted by the user's brightness-button
trial after the 150-second sample. Readback then showed a selected 100% and 14
brightness clicks, while retaining the 50% host baseline. The probe's fixed-50%
assertion stopped and its cleanup resumed the daemon. This is not a completed
timeout result; its private evidence is retained as
`disconnect-interrupted-evidence.json`.

## Dimming correction

The user reported that the 25% step appeared off. Inspection of the pinned V2
schematic and AP3032 datasheet established a filtered analog feedback circuit,
whose calculated current cutoff is about 64.29% high PWM duty. The existing
full-range inverted mapping exceeded that cutoff at 25%. Shared board support
now maps nominal LED-current fractions over the usable range; the four steps
use 123/82/41/0 of 256 duty counts. This is a circuit-derived model, without a
lux/current measurement. Native checks independently reconstruct the sense
voltage and verify the four current fractions and monotonic mapping.

The initial application above and the original full-flash backup remain
retained. Snap's source was backed up again before applying this correction.
Corrected application identity and subsequent observations follow below.

At approximately **14:57 PDT**, the corrected application-only write passed
esptool data verification and the daemon resumed immediately. Its application
is 3,825,696 bytes, SHA-256
`e4d3f423ac36c41b1dcd1209568d55c43e45ba47561dc49d947dc9a13b75c997`,
and ELF SHA-256
`dc3c6bc6bcf51ff32632cfb383734fe6cad6435e8e9817250f04853a4b56745d`.
Artifacts are retained in `candidate-v2/` alongside the first candidate.
Device hello reports ELF prefix `dc3c6bc6b`; boot **1155817523** initially
reported 50% with released buttons and zero clicks. Both firmware builds and
15/15 native CTests in Debug and Release passed again after this correction.

## Final live-policy checks

The full post-reset daemon-loss trial passed on boot **1973334757**. Inert
readback every 30 seconds did not renew the deadline: applied 50% at
**299.004 seconds**, then 0% with `disconnected` at **300.704 seconds**.
Fresh hello/pong probes kept it dark with `awaiting_host`. Resuming the daemon
restored 50% on the same boot. Direct commands also verified 65%, screen-off
remaining dark through a ping, and unknown screen state retaining off while
accepting the 50% baseline.

A subsequent monitor-power capture restored both external DP outputs on and
verified the linked bar at 50%. The outputs woke immediately after the off
command, and this capture contained no aggregate `host_screen_off` sample.
It therefore adds no sustained host-screen-off acceptance; the earlier
[policy checkpoint](backlight-acceptance.md) records a brief real-source off/on
transition. The new direct board commands and native host-source cases passed.

Final deployment source matches the local candidate. Configuration, saved
pairing and the service unit retain their pre-deployment hashes. The daemon is
active, unpaused and linked through the paired stable serial path; manual off
is clear, brightness is 50%, and both external monitors are on. At that
checkpoint, no commit or push had been made. Private source manifests, probes
and structured observations remain with the backup.

## Physical observations

Live readback recorded two Power clicks: manual-off at 0%, then automatic-on at
50%, on the first boot `1828867666`.

After the dimming correction, the user confirmed four visible brightness
levels, accepted 50% as the default, observed Power turning the screen off,
and confirmed RESET restarts the device. The fresh board readback captured
selected 25% with seven brightness clicks, then 50% with twelve brightness
clicks and two Power clicks. These button presses did not change the host
baseline of 50%.

The RESET trial re-enumerated USB, ending the in-progress diagnostic probe
with an I/O error. Its cleanup resumed the daemon, which linked to new boot
**1973334757** at 50%, manual-off clear and zero button counters. This is an
expected reset interruption, not a completed timeout trial; the evidence is
retained as `disconnect-reset-interrupted-evidence.json`. The full timeout
trial was restarted on that boot after the operator finished.

Native assertions and serial counters remain separate from the user-confirmed
illumination/button observations. Electrical current and lux were not measured.
The held-finger wake gate remains unobserved physically; its native checks pass.
The [testing guide](../docs/testing.md) describes the operator sequence.

## Review correction

The subsequent October 8 review found a clock/state ordering race in the main
loop. It sampled time before acquiring the state lock, so a newer host receipt
on the other core could make fresh traffic appear expired and briefly turn the
backlight off. The decision clock is now sampled while holding the same lock
as the host-receipt timestamp. This changes neither the timeout nor the button
contract.

The isolated host/tooling suite passed **410 tests**, with three live-desktop
checks skipped because the session bus was deliberately unset. Native Debug
and Release each passed **15/15 CTests**, and notification-panel rebuilt
successfully. The shared driver and render-bench inputs are unchanged from the
two successful firmware builds above.

Snap's exact paired board received another application-only write at
`0x10000`, with esptool data verification. Its application is 3,825,696 bytes,
SHA-256 `32845cae2032188df77859d4c513706c42d0b119509c5b630945a4df8c53fafd`,
and ELF SHA-256
`092daa1784e7aadd8b1002a21fa0c9ba3fdf18e4dc80ce72c02f9a0030c00bea`.
Device hello reports build `b188cf6-dirty`, ELF prefix `092daa178` and boot
**839314302**. The prior corrected application, source and preserved host files
were retained before this write under
`~/.local/share/esp32-349/backups/review-backlight-snap-20261008T221856Z/`.
The original full-flash backup remains in the earlier backup directory.

A 20-second direct serial burst completed **3,648 pings/pongs** and **975
backlight readbacks**. Every readback reported applied and target 50% with
reason `on`, and no unexpected disconnect-off transition appeared in serial
logs. After daemon resume, a **75.004-second** observation at its configured
60-second sync interval collected **366 readbacks**, all at 50%, with both
external monitors on and the same boot identity. This is automated scheduling
and driver evidence; it adds no new optical or held-touch acceptance.

The daemon remains active, unpaused and linked. Final source hashes match the
reviewed candidate, and configuration, pairing and service-unit hashes retain
their pre-deployment values. Structured review evidence is kept privately with
the new backup. The full 300-second and physical-button results above remain
their own dated observations.
