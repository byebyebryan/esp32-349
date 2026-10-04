# display_349

Shared board/display component for the ESP32-S3-Touch-LCD-3.49 V2. It owns
panel initialization, the TCA9554-controlled backlight/reset, touch input,
LVGL locking and the landscape display pipeline. Both repository projects
consume this component through `EXTRA_COMPONENT_DIRS`.

LVGL renders to a full PSRAM RGB565 buffer in DIRECT mode. Dirty rectangles
are transposed into a persistent portrait shadow framebuffer; GDMA stages
that shadow through internal DMA buffers for complete-frame QSPI transfers.
The panel runs at 40 MHz. The component does not own application UI, host
protocols or application font selections.

The [render-bench findings](../../projects/render-bench/README.md) explain the
panel constraints and measured optimization history. The shared shadow
conversion regression is [`tools/test_shadow_349.c`](../../tools/test_shadow_349.c);
its build command is in the [repository guide](../../README.md#shared-component-checks).
