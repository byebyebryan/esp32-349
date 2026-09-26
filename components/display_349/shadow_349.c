#include "shadow_349.h"

#include <stddef.h>

static uint16_t swap_rgb565_bytes(uint16_t pixel)
{
    return (uint16_t)((pixel >> 8) | (pixel << 8));
}

void shadow_349_update(uint16_t *dst, const uint16_t *src,
                       int ui_width, int ui_height,
                       int x, int y, int width, int height, int src_stride)
{
    for (int local_x = 0; local_x < width; local_x++) {
        const int native_y = ui_width - 1 - (x + local_x);
        uint16_t *native_row = dst + (size_t)native_y * (size_t)ui_height + (size_t)y;

        for (int local_y = 0; local_y < height; local_y++) {
            const size_t source_offset =
                (size_t)local_y * (size_t)src_stride + (size_t)local_x;
            native_row[local_y] = swap_rgb565_bytes(src[source_offset]);
        }
    }
}
