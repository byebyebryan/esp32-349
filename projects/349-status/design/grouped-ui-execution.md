# Grouped UI execution and automated acceptance

Execution plan, 2026-09-26. The
[grouped UI direction](grouped-ui-plan.md) is committed in `bee6cab`.
This plan responds to the user's request to automate involved acceptance
sequences and reserve manual board work for hardware-dependent observations.
The goal loop completed this plan's automated and short Starship physical
gates on firmware `be39d5e49`; [the evidence](grouped-ui-acceptance.md) records
their scope. The exact negotiated contract is in
[grouped-ui-protocol.md](grouped-ui-protocol.md). Implementation commits are
recorded in the acceptance evidence.

## Execution outcome

Deliver Home/Notifications groups, vertical whole-card browsing, bounded
retention, and presentation timeout returning to Home. Establish automated
coverage of gesture/timer/notification interactions before the first board
trial. Finish with one short Starship visual/touch session plus automated
device readback, then record the actual evidence and restore normal mirroring.

Use the design document's proposed defaults as the execution starting point:
two bounded groups, bounded vertical card navigation, up to 32 retained cards,
10 s normal presentation fallback, manual browsing without an automatic return,
local × removal, explicit desktop dismissal removing the retained record,
and critical attention deferred until the current gesture settles. Geometry
and the wire contract are finalized in the checkpoints below.

## What is already automated

The existing [native fixture](../tools/native_ui/test_ui.c) compiles the
production `ui_deck.c`, deck/input policy, pinned LVGL, and actual font assets.
It injects pointer samples through `lv_indev`, advances virtual time, and
captures RGB565 output. It already checks held arrivals, critical focus,
replacement, reordered state, source/destination removal during drag and
settlement, release latching, hit targets, and card-count transitions.

On 2026-09-26 its Debug and Release builds were rerun and passed, with
assertions retained. Those results concern the current horizontal swipe UI.
The shared replay runner, desktop viewer, grouped scenarios and composed
production parser/state target are now implemented. Their current results
are recorded in [grouped-ui-acceptance.md](grouped-ui-acceptance.md); the
paragraph above records the original baseline, not the new candidate gate.

The direct UI fixture substitutes state access, dismissal transport, RTC, link state,
and locks. Its cache changes are injected directly. It proves real LVGL
interaction/rendering behavior, but does not prove firmware JSON parsing,
USB framing, ESP32 task scheduling, capacitive sensing, or panel transfer.
The host has separate policy, protocol, and PTY fake-device tests. Compose
these boundaries explicitly rather than describing the current fixture as a
full firmware emulator. The composed target now runs production parser/state
with those platform services adapted for native execution.

## Simulator choice

| Approach | Useful coverage | Decision for this goal |
|---|---|---|
| Native LVGL replay | Production widgets, real fonts, virtual pointer/time, rendered frames, and deterministic races | Main automated UI gate; extend the existing target |
| Desktop window using the same native UI | Inspect and try the actual layout without flashing | Add a small SDL viewer using the same UI/core; its speed is not board performance |
| Espressif QEMU | ESP32-S3 CPU/memory and supported peripherals; firmware debugging with alternate test adapters | Defer unless an ESP-IDF-specific defect cannot be covered by the native/probe path |
| Wokwi | Supported ESP32-S3 and listed display/input parts | Defer custom board modeling; the built-in hardware list does not establish support for this board's AXS15231B display/touch path |
| Physical board | Actual touch sampling, pixel appearance, display/DMA behavior, resource headroom, USB reconnection | Automated serial checks plus a short human smoke check |

Espressif documents ESP32-S3 QEMU support and an optional **virtual** display
requiring `esp_lcd_qemu_rgb`; that framebuffer is not a peripheral on the real
chip. A QEMU target would need adapted display/transport/platform setup and
would not establish this board's touch behavior or QSPI timing.
[Espressif QEMU guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/tools/qemu.html)

Wokwi documents ESP32-S3 support, with built-in displays including ILI9341 and
other listed controllers; AXS15231B is absent from that list. This is a limit
of the documented built-in route, not proof that a custom model is impossible.
[Wokwi supported hardware](https://docs.wokwi.com/getting-started/supported-hardware)

The simulator should run the production UI and policy. A browser sketch can
compare layout ideas, but it is design evidence rather than the automated
acceptance engine. The desktop viewer and headless runner share their input
adapter and UI initialization so they cannot silently test different code.

## Checkpoints and file responsibilities

### 1. Make replay and capture the routine acceptance path

**Responsibility:** `tools/native_ui/`, small native platform adapters, and
test-runner documentation. Keep production interaction behavior unchanged
while establishing the baseline.

- Factor the existing fixture setup into a reusable headless runner and a
  thin SDL viewer. Preserve production fonts, RGB565, DIRECT buffering, and
  assertions in Debug and Release.
- Give scenarios explicit commands for pointer press/move/release, advancing
  time, injecting a notification/state event, and capturing an image/readback.
  Every scenario has a deterministic clock and reset state.
- Put captures and traces in a caller-selected artifact directory instead of
  overwriting fixed `/tmp` filenames. Report a compact scenario result and
  retain the event trace on failure.
- Provide one documented entry point for native checks and captures. Register
  the checks with CTest; headless runs do not depend on a GUI session.
- Port the existing held-gesture/race cases first. Make their automated results
  sufficient for those software behaviors, with no matching human sequence
  required after every successful build.

**Gate:** Current UI behavior passes replay in Debug and Release. The real-font
captures at rest and in motion remain unchanged from the reviewed baseline.
The viewer displays the same rendered UI as the headless target. Record the
shim boundaries; no ESP32 timing claim is made from desktop execution.

### 2. Settle grouped navigation and readability in the simulator

**Responsibility:** Group/input policy, production UI views, and native scenario
fixtures. Keep rail data and font generation outside this change.

- Introduce Home and Notifications with horizontal group ownership and vertical
  whole-card ownership. Preserve explicit ×, body-tap behavior, rail ownership,
  release latching, and stale cancellation.
- Compare a small number of vertical peek geometries using production font
  captures at 640 × 172. Select the body-line/peek budget and boundary cues.
- Use one recognizer/scroll owner per gesture. Evaluate native LVGL snapping
  against the existing explicit-policy approach before selecting one; retain
  captured IDs, copied text, and generation-safe completion.
- Inspect idle, empty, one-card, multi-card, long-text, and conditional-battery
  layouts in the shared viewer. Freeze layout and navigation defaults before
  integration, while preserving a real-board readability smoke check.

**Gate:** Automated horizontal/vertical/cancel/hit-target cases pass. Images
show the rail fixed, cards clipped, meaningful group/card cues, and the agreed
text budget. Changing counts or order does not imply a changed selected ID.

### 3. Freeze the lifecycle/wire contract and implement the host

**Responsibility:** `host/src/status349/{state,proto,daemon,config}.py`,
`sources/notifications.py`, host tests, and protocol documentation.

- Define the capability and exact envelopes for retained records, presentation
  leases/generations, remaining time, manual takeover, removal, and session
  reset. Define ordering relative to chunked full sync and maximum frame size.
- Specify separate display-card identity and desktop association lifetime.
  Set finite limits for retention-related metadata, pending replies, and
  suppression; specify what happens at each limit.
- Split presentation completion from record deletion. Preserve correlation
  for a replacement after timeout, and distinguish desktop closure reasons.
- Implement bounded retention, ordering, eviction, local removal, and monitor
  recovery. Support the existing active-card behavior for older firmware.
- Test deadlines with a controlled monotonic clock. Use PTY/fake-device cases
  for connection and actual serialized messages; wait for observable events
  rather than fixed timing sleeps where practical.

**Gate:** Host tests cover timeout/retention, timeout-then-replacement, stale
leases, manual takeover, critical events, closure reasons, eviction, local ×
followed by resync/replug, monitor restart/ID reuse, and both capability paths.
The contract is explicit enough for independent firmware/parser tests.

### 4. Integrate the production firmware state and replay the whole path

**Responsibility:** Firmware `proto.c`, `state.c/h`, group/input policy and UI,
native platform shims, host/native integration tests.

- Implement the negotiated retained/presentation state and device input events.
  Old hosts continue to select the current active-card UI.
- Extend the native target to execute production protocol/state code. Stub
  platform services such as allocation, mutexes, RTC, and descriptor access;
  keep JSON validation, staged commit, selection, and lifetime logic shared.
  Use a small portable core extraction only if the platform shims become larger
  than the logic they expose.
- Connect host-generated envelopes to that native parser/state/UI path, with
  input events returned to host handling. Do not reimplement the firmware
  parser in the test or claim end-to-end coverage from direct field assignment.
- Keep direct-state UI fixtures for fast focused cases, and add a bounded set
  of composed host → firmware parser/state → LVGL → host-input scenarios.
- Replay event interleavings while pressed, dragging, and settling. Save state
  and rendered frames before/after relevant events. Add bounded reproducible
  seeded sequences where they resolve ordering/cancellation risk.
- Check rotation conversion, dashboard parsing, existing text coverage,
  firmware compilation, and native memory safety on the paths being changed.
  Native sanitizers do not measure ESP32 internal memory or task scheduling.

**Gate:** The coverage matrix below passes on the integrated source. Failing
software cases are fixed and replayed before hardware deployment. Current
legacy behavior remains covered; parser/state substitutions are declared in
the report rather than hidden behind an emulator label.

### 5. Run automated board probes and one brief manual session

**Responsibility:** Starship deployment, protocol/readback recorder, resource
observations, final acceptance documentation, and normal mirror restoration.

- Identify and archive the firmware/ELF and original configuration. Flash the
  reviewed build, verify device identity, and isolate synthetic cards from real
  desktop notifications for the check.
- Automate serial checks for sync, retained counts/IDs, timeout retaining the
  record, replacement, removal, capacity/eviction, malformed transfers, and
  stale recovery. Confirm restoration through device readback and service logs.
- Collect heap/stack and transfer-health observations while exercising the
  integrated board. Readback/timing logs establish those metrics; they do not
  establish visual correctness or capacitive sensing.
- Run the short human checklist below on one candidate. A later fix repeats
  the affected hardware check, plus the automated regression suite.

**Gate:** Correct automatic return and retained browsing on the real display,
readable layout, reliable physical two-axis input, no observed artifacts or
stuck motion, and correct replug recovery. Restore normal mirroring and clear
synthetic records; record exact builds and the scope of each result.

## Coverage matrix

| Behavior | Routine automated coverage | Human/physical requirement |
|---|---|---|
| Axis lock, short cancel, flick, bounds, button/rail ownership | Policy tests and real LVGL pointer replay | Brief check of real touch sampling and feel |
| Arrivals/replacement/sync during a held gesture | Timed native/composed replay with state and frame assertions | Targeted follow-up only if hardware evidence contradicts replay |
| Source/destination removal during drag/settlement | Native replay and parser/state integration | No routine hold-and-inject sequence |
| Timeout returns Home while retaining the card | Controlled-clock host/UI integration | One short visible demonstration |
| Manual reading, critical deferral, stale/older timeout | Controlled event interleavings and frame capture | Ordinary touch smoke; no timed hold choreography |
| Local ×, replacement, desktop closure reasons | Host/production parser/state round trips | One actual × touch |
| Empty/1/2/32/33 cards and truthful counts | Host selection, native state/UI, automated board readback | A representative multi-card layout |
| Clip/text/font/conditional-rail geometry | Real-font capture review and agreed image baselines | Readability/color/physical-size check |
| Chunk ordering, malformed/interrupted transfer, legacy negotiation | Host/fake-device and native production-parser tests | Automated board serial smoke |
| USB replug and boot | Host/fake reconnect plus automated board recorder | One unplug/replug when the user is available |
| DMA/panel health, ESP32 heap/stack and scheduling | Board logs/readback during probes | Visual observation of motion/artifacts; native speed is not board speed |

## Manual session: approximately 3–5 minutes

The opt-in [physical recorder](../tools/run_grouped_smoke.py) manages isolation,
uses the production host policy for incoming touch events, and resumes normal
mirroring on exit. Its offline self-test is included in the routine native
command; [operator commands](../tools/native_ui/README.md#opt-in-physical-recorder)
cover persistent cards, one announced timeout, replug and finish. The first
Starship session completed with the user's passing observations and verified
normal restoration; see [acceptance evidence](grouped-ui-acceptance.md).

1. **Look:** inspect Home and a representative multi-card group for legibility,
   clipping, persistent rail, and a useful vertical peek.
2. **Touch:** make a few horizontal group swipes and vertical card swipes,
   one short cancel, and one × tap. Confirm direction ownership, finger-following,
   and absence of accidental dismissals. No twenty-attempt count or prolonged
   finger-holding sequence is required as the routine gate.
3. **Observe one presentation:** start an announced short notification test,
   watch it return to Home, then swipe back and confirm the card is retained.
4. **Replug once:** the recorder waits for USB return, verifies full state, and
   logs elapsed recovery. Check normal layout after it reconnects.

Check availability before the session and keep ordinary cards visible until
the user finishes. Only the announced timeout case has a short window.
Present the touch checklist together; the recorder controls data injection,
identity, cleanup, and logs. Avoid repeating the whole session across exploratory
builds. A sensor-specific fault can justify one focused follow-up, with the
reason stated.

## Scope and reporting

- Use the existing renderer, fonts, USB path, and host sources. Additional
  groups, body-text scrolling, disk history, custom Wokwi chips, and a QEMU
  board port are separate work.
- Preserve the accepted observed responsiveness. Record bounded board timing
  and resource observations; no new FPS tuning target is imposed.
- Record whether a result is policy, native LVGL, composed parser/state,
  automated board, or human evidence. Keep simulator captures and logs as
  development artifacts; accept image baselines through review rather than
  automatically replacing them on every run.
- Commit cohesive automation, lifecycle/protocol, UI integration, and evidence
  changes through the user's requested review workflow. No push, Snap rollout,
  soak, or powered-board suspend test is included in this execution proposal.
