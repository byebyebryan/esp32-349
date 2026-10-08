# USB handshake prototype validation — 2026-10-02

This is the transport prototype record, before persistent pairing was deployed.
The current selection/reconnect contract is in the
[setup guide](../docs/setup.md#serial-discovery), with deployed and physical evidence
in [USB pairing acceptance](usb-pairing-acceptance.md).

Hardware validation on 2026-10-02 confirmed that the existing transport can
identify these displays without changing USB stacks or adding pairing:

| Target | Firmware build | Successful connections | Final hello + pong exchange |
| --- | --- | --- | --- |
| Snap 349 | `d6e782b-dirty` (`9188da1dc`) | 4 | 2.0 ms |
| Starship 349 | `a80e0b6` (`1275d47ee`) | 4 | 2.1 ms |

The probe preconfigured DTR and RTS asserted before opening and never invoked
the reset helper. Three consecutive opens on each 349 returned the same
`boot_id`; opening does not inherently require a reset. This matches the
no-action control-line combination in the
[ESP32-S3 TRM, Table 33.3-2](https://documentation.espressif.com/esp32-s3_technical_reference_manual_en.pdf).
The daemon in use during that validation deliberately reset its known target
on resume. The persistent-pairing implementation also uses these asserted
control lines for startup, reconnect and resume.

The probe sent `@349 {"t":"hello"}` followed by a newline, required protocol
version 1, a firmware string and `link`/`bar` capabilities, then required a
fresh numeric `ping.ts` echoed by `pong.ts`. Nine hello-validation cases and
six simulated exchanges covered valid replies, malformed fields, echoed
requests, incompatible protocol versions, wrong/missing pong and silence.

Starship's RLCD was rejected in five trials while its 50 fps benchmark
continued. Its firmware has no serial command reader: the single hello write
timed out after 0.5 seconds, and ordinary cleanup added about 30 seconds.
Discarding queued output with `reset_output_buffer()` before closing reduced
measured cleanup to 1.7 ms. Discovery bounds reads, writes and the handshake
off the event loop. Rejection and disconnect discard unsent output before
closing; the adopted transport suppresses PySerial-asyncio's cleanup flush.

The prototype and raw captures remain ignored local artifacts under
`.cache/handshake-validation/` on Snap and Starship. Both daemons were restored
to their configured displays without a service restart. Other firmware,
hotplug races and a hub power-cycle test remain outside this prototype
validation; later deployment and hub-cycle results are recorded in
[USB pairing acceptance](usb-pairing-acceptance.md).
