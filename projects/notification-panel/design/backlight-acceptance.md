# Backlight acceptance

Checkpoint: **2026-10-08**, candidate based on `b188cf6` with local changes.

The contract is in [backlight policy](backlight-policy.md). This record
distinguishes local checks, Snap backup/deployment identity, serial behavior
and user-observed illumination. Starship hardware is outside this deployment.

## Local validation

- Host/tooling: 408 passed; three optional live-desktop tests skipped.
- Native production checks: 15/15 passed in Debug and Release.
- Notification-panel and render-bench firmware builds passed with ESP-IDF
  v5.5.3, including the shared touch gate.
- Focused display/config tests also passed on Snap: 41 passed.
- Presentation assets were regenerated; their PNG/GIF bytes were unchanged.
  Source/build hashes in the manifest were refreshed.

## Snap deployment

The paired V2 board on Snap was flashed at approximately **14:09 PDT on
2026-10-08**, through the stable USB path ending
`28:84:85:92:C4:3C-if00`. Starship's board and host service were not used for
deployment or hardware tests.

Snap's clean checkout was advanced from `f5a31a4` to the published `b188cf6`
base before installing the locally tested changes. The candidate was uncommitted
at this checkpoint. The existing service was restarted while its sticky serial
pause was set, then resumed after the application write and verification.

| Artifact | Identity |
| --- | --- |
| Application | 3,824,208 bytes; SHA-256 `21600c1a58da8d0f4604ea80633c81f48bb2a601ae10c1f8ce2ac867fbb202bd` |
| ELF | SHA-256 `26974d492032d88b9011bbfb6c0393b9f857805ac38256a0516aa8ae35843b37` |
| Device hello | Build `b188cf6-dirty`, ELF prefix `26974d492`, `backlight-v1`, boot ID `896233743` |
| Defaults observed | Brightness 100%, disconnect timeout 300 seconds, host screen following enabled |

The full 16 MiB previous flash, previous application slot, partition table,
source, configuration, saved pairing and service file are retained privately
on Snap under:

```text
~/.local/share/esp32-349/backups/backlight-snap-20261008T210030Z/
```

The full backup SHA-256 is
`e0909f8c9c4e41f69c63d1888115e98f4a7e984e355d320f02b71e847fe457da`.
It identifies the previous `d6e782b-dirty` application with ELF prefix
`9188da1dc`. The existing partition table exactly matched the candidate:
factory application at `0x10000`, size 8 MiB. Only the application was written;
esptool verified its data hash. Bootloader, partition table and NVS were
preserved.

## Device and host observations

- Snap reports two enabled external DP connectors and a connected but disabled
  laptop panel. The aggregate is on; the laptop panel does not block off.
- A diagnostic monitor off/on capture observed applied 0% with
  `host_screen_off`, followed by applied 100% with `on`, with the same boot ID.
  The first sustained-off attempt did not establish its four-second hold:
  Snap's external monitors woke immediately. The reason for that wake has not
  been confirmed by the operator.
- Direct commands applied 65% brightness and then 0%. A fresh ping did not
  override screen-off. An unknown (`null`) screen decision retained off while
  accepting a change back to configured brightness 100%.
- The full daemon-loss trial passed with a 300-second policy. Inert readback
  every 30 seconds did not renew it: applied 100% at 299.004 seconds, then 0%
  with `disconnected` at 300.704 seconds. The probe used the daemon's normal
  non-resetting serial-open path and checked the same boot ID throughout.
- After expiry, a fresh hello/pong exchange left the backlight at 0% with
  `awaiting_host`. Resuming the daemon replayed its on decision and restored
  100%, without changing boot ID `896233743`.
- Final state is an active, unpaused Snap daemon, linked to the same paired
  board, with both external monitors restored on. Configuration, saved pairing
  and service-file SHA-256 hashes match their pre-deployment copies.

Private structured observations and the exact probe script are retained with
the backup. Serial readback reports successful driver application rather than
measuring voltage or emitted light.

## Physical acceptance

User observation of the bar going fully dark and waking remains pending.
The held-finger wake check is also pending: input should remain cancelled
until release, and the next fresh press should work normally. No physical
illumination or touch acceptance is claimed by the automated checks.

## Subsequent checkpoints

The [button/default checkpoint](backlight-buttons-acceptance.md) records the
later 50% default, dimming correction, full timeout and user-observed physical
controls. The [boost checkpoint](backlight-boost-acceptance.md) records the
subsequent notification feature and source publication. The dates, 100% default
and artifact identities above belong to this initial trial.
