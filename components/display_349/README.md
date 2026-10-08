# display_349

Shared board/display component for the ESP32-S3-Touch-LCD-3.49 V2. It owns
panel initialization, the TCA9554-controlled backlight/reset, touch input,
LVGL locking and the landscape display pipeline. Both repository projects
consume this component through `EXTRA_COMPONENT_DIRS`.

The pin map targets **V2**. The [official hardware guide](https://docs.waveshare.com/ESP32-S3-Touch-LCD-3.49)
explains revision identification and the V1/V2 wiring changes.

LVGL renders to a full PSRAM RGB565 buffer in DIRECT mode. Dirty rectangles
are transposed into a persistent portrait shadow framebuffer; GDMA stages
that shadow through internal DMA buffers for complete-frame QSPI transfers.
The panel runs at 40 MHz. The component does not own application UI, host
protocols or application font selections.
Backlight calls run under the LVGL lock. Turning it off cancels pointer input;
wake waits for a held finger to be released before accepting a fresh press.
V2 brightness percentages use the AP3032's filtered-PWM feedback network to
map nominal LED current, rather than full-range inverted PWM. See the
[backlight circuit/model](../../projects/notification-panel/design/backlight-policy.md)
and its physical validation. Zero always disables BL_EN.

Applications may opt into `display_349_buttons_init()` and poll every 20 ms.
V2 brightness/BOOT (GPIO0) and Power/SYS_OUT (GPIO16) are active-low inputs.
The API debounces for 30 ms, reports one click on release, ignores the initial
release of a button held at startup, and never operates the battery power
latch. RESET is wired to CHIP_PU. Application policy belongs with the selected
project; notification-panel uses these inputs for backlight controls.

The [render-bench findings](../../projects/render-bench/docs/rendering.md) explain the
panel constraints and measured optimization history. The shared shadow
conversion regression is [`tools/test_shadow_349.c`](../../tools/test_shadow_349.c);
its build command is in the [repository guide](../../docs/development.md#shared-component-checks).
