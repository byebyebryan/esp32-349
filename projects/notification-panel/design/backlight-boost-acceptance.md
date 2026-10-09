# Notification brightness boost checkpoint

Candidate date: **2026-10-08**; base source `133abd4` with uncommitted changes.
This follows the [button/default checkpoint](backlight-buttons-acceptance.md)
and records the [notification-boost contract](backlight-policy.md#notification-boost).
Hardware writes and live service checks in this trial targeted **Snap only**.

## Automated checks

- Isolated host/tooling suite: **417 passed**, three live-desktop checks skipped
  with the session bus deliberately unset.
- Native Debug and Release: **15/15 CTests each**, including exact boost expiry,
  arrival restart, selected-level preservation, off/button/reload cancellation,
  disabled configuration and absence of deferred boosts. Production parser
  checks cover legacy/grouped accepted cards, invalid/uncached cards and inert
  snapshots. Host checks cover capability negotiation and replay isolation.
- ESP-IDF **5.5.3** notification-panel build passed. Shared board support and
  render-bench are unchanged by this feature.
- Snap display/config/discovery suite: **67 passed**.
- Presentation assets regenerated with Pillow **12.0.0**, preserving PNG/GIF
  bytes while updating source/build hashes. Documentation/media validation and
  `git diff --check` passed.

## Deployment

Snap's paired V2 board, USB serial **28:84:85:92:C4:3C**, received an
application-only write at `0x10000`, verified by esptool. Its application is
3,826,576 bytes, SHA-256
`2e343665cdcf71c519be20e2785bbd7cf4ebc2a41cf899f5ee2cea09562794bd`;
ELF SHA-256
`ff75ea07a9b4a4e753c39d9e04340faaa7470996a8cd044dd74255a0d897da18`.
Device hello reports build `133abd4-dirty`, ELF prefix `ff75ea07a`, boot
**431581288** and `backlight-boost-v1`.

The previous verified application/ELF/partition artifacts, source and host files
were retained before writes under
`~/.local/share/esp32-349/backups/boost-snap-20261008T224024Z/` on Snap.
The earlier full-flash backup remains retained in the button checkpoint's
backup directory. The partition table is unchanged; configuration, pairing
and the service unit retain their pre-deployment hashes. The daemon was paused
for the application write, restarted with the new host code and resumed.

## Live policy evidence

A direct serial trial on the same boot used selected brightness 65%. A fresh
notification applied 100%; replaying the same display control and a cached
snapshot preserved the original deadline. A second live arrival reset remaining
time to 30,000 ms. Readback showed 100% at **29.700 seconds**, then returned to
65% with zero remaining boost at **30.200 seconds**.

Direct screen-off control canceled an active boost. An arrival while off stayed
dark, and subsequent screen-on restored 65% without a deferred boost. A short
two-second disconnect setting proved timeout precedence over the longer boost;
a later arrival stayed dark awaiting control, and reconnect restored 65%.
Disabling boost canceled it immediately and ignored a later arrival. Cleanup
restored the normal 50% host baseline, 300-second timeout and 30-second boost,
then resumed the daemon and its actual notification cache without a board reset.
This short timeout trial does not replace the earlier full 300-second record.

The installed daemon's notification path then applied 100% while retaining a
50% selection and returned to 50%. Its capture includes another live arrival
during the first window, so the observed return was **33.632 seconds** after
the initial test notification. This remains separate from the quiet direct
trial's 30-second boundary evidence.

The first combined operator trial did not capture a Brightness click. A repeated
trial captured one debounced click at **29.506 seconds**, before boost expiry:
applied/target/selected brightness became 75%, remaining boost became zero,
and the host baseline remained 50%. Daemon pause/resume retained 75% on the
same boot and cached replay did not restart the boost.

## Physical acceptance and final state

The user confirmed the visible boost/return behavior, then confirmed that
pressing Brightness during the repeated boost immediately settled at 75% and
stayed there. These observations are separate from serial counters and native
assertions. Electrical current and lux were not measured.

At the end of this Snap trial, the daemon was active, unpaused and linked,
with manual off clear, selected brightness 75%, host baseline 50%, timeout 300
seconds and boost duration 30 seconds. Final source hashes matched the local
candidate and preserved host-file hashes remained unchanged. Structured
observations and source/artifact manifests are retained privately with the
backup. The candidate was uncommitted at this validation
checkpoint; earlier screen-off/held-touch acceptance limits remain unchanged.

## Publication

The reviewed source was committed and pushed as
[`b9046f4`](https://github.com/byebyebryan/esp32-349/commit/b9046f4417a0bed22ff5c07cc6f2ca101ef2b87d).
Review repeated all 24 host display tests and the native backlight/protocol
checks in Debug and Release; documentation/media provenance and Snap source
parity passed. Publication did not rebuild or replace the Snap application
identified above.
