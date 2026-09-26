#pragma once

#include <stdint.h>

/*
 * Convert a row-major RGB565 source rectangle into the native-orientation
 * shadow. Source begins at the rectangle's top-left pixel and uses `src_stride`
 * pixels between rows. For each local source pixel (local_x, local_y), write
 * byte-swapped RGB565 at destination row
 * `(ui_width - 1 - (x + local_x))`, column `(y + local_y)`. Destination row
 * stride is `ui_height` pixels.
 *
 * The caller supplies distinct valid buffers, positive dimensions, and a
 * positive source stride. Rectangle bounds must fit inside the UI dimensions.
 * The function performs no allocation.
 */
void shadow_349_update(uint16_t *dst, const uint16_t *src,
                       int ui_width, int ui_height,
                       int x, int y, int width, int height, int src_stride);
