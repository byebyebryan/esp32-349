#include "shadow_349.h"

#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define MAX_FRAME_PIXELS ((size_t)640 * 172)
#define MAX_SOURCE_PIXELS ((size_t)(640 + 32) * 172)
#define FRAME_INITIAL UINT16_C(0xA55A)

static uint16_t source_storage[MAX_SOURCE_PIXELS + 2];
static uint16_t expected_storage[MAX_FRAME_PIXELS + 2];
static uint16_t output_storage[MAX_FRAME_PIXELS + 2];

static uint16_t swap_rgb565_bytes(uint16_t pixel)
{
    return (uint16_t)((pixel >> 8) | (pixel << 8));
}

static uint16_t sample_pixel(int x, int y, uint32_t salt)
{
    const uint32_t value = (uint32_t)x * UINT32_C(40503) ^
                           (uint32_t)y * UINT32_C(7919) ^ salt;
    return (uint16_t)(value ^ (value >> 16));
}

static void run_case(int ui_width, int ui_height,
                     int x, int y, int width, int height, int src_stride,
                     uint32_t salt)
{
    assert(ui_width > 0 && ui_height > 0);
    assert(x >= 0 && y >= 0 && width > 0 && height > 0);
    assert(x + width <= ui_width && y + height <= ui_height);
    assert(src_stride >= width);

    const size_t frame_count = (size_t)ui_width * (size_t)ui_height;
    const size_t source_count =
        (size_t)(height - 1) * (size_t)src_stride + (size_t)width;
    assert(frame_count <= MAX_FRAME_PIXELS);
    assert(source_count <= MAX_SOURCE_PIXELS);

    uint16_t *const source = source_storage + 1;
    uint16_t *const expected = expected_storage + 1;
    uint16_t *const output = output_storage + 1;

    source_storage[0] = UINT16_C(0xA55A);
    source_storage[source_count + 1] = UINT16_C(0x5AA5);
    expected_storage[0] = UINT16_C(0xC33C);
    expected_storage[frame_count + 1] = UINT16_C(0x3CC3);
    output_storage[0] = UINT16_C(0x9669);
    output_storage[frame_count + 1] = UINT16_C(0x6996);

    for (size_t i = 0; i < frame_count; i++) {
        expected[i] = FRAME_INITIAL;
        output[i] = FRAME_INITIAL;
    }

    for (int local_y = 0; local_y < height; local_y++) {
        for (int local_x = 0; local_x < width; local_x++) {
            const size_t source_offset =
                (size_t)local_y * (size_t)src_stride + (size_t)local_x;
            source[source_offset] = sample_pixel(local_x, local_y, salt);
        }
        if (local_y + 1 < height) {
            for (int pad = width; pad < src_stride; pad++) {
                source[(size_t)local_y * (size_t)src_stride + (size_t)pad] =
                    UINT16_C(0x0BAD);
            }
        }
    }

    /* Scalar reference: map each rectangle pixel into the full native shadow. */
    for (int local_y = 0; local_y < height; local_y++) {
        for (int local_x = 0; local_x < width; local_x++) {
            const uint16_t pixel = source[
                (size_t)local_y * (size_t)src_stride + (size_t)local_x];
            const size_t native_offset =
                (size_t)(ui_width - 1 - (x + local_x)) * (size_t)ui_height +
                (size_t)(y + local_y);
            expected[native_offset] = swap_rgb565_bytes(pixel);
        }
    }

    shadow_349_update(output, source, ui_width, ui_height,
                      x, y, width, height, src_stride);

    for (size_t i = 0; i < frame_count; i++) {
        assert(output[i] == expected[i]);
    }
    assert(source_storage[0] == UINT16_C(0xA55A));
    assert(source_storage[source_count + 1] == UINT16_C(0x5AA5));
    assert(expected_storage[0] == UINT16_C(0xC33C));
    assert(expected_storage[frame_count + 1] == UINT16_C(0x3CC3));
    assert(output_storage[0] == UINT16_C(0x9669));
    assert(output_storage[frame_count + 1] == UINT16_C(0x6996));
}

/* DIRECT-mode flush color_p points at the full frame, with full-frame stride. */
static void run_direct_frame_case(void)
{
    const int ui_width = 640;
    const int ui_height = 172;
    const int x = 37;
    const int y = 51;
    const int width = 73;
    const int height = 42;
    const size_t frame_count = (size_t)ui_width * (size_t)ui_height;
    uint16_t *const source = source_storage + 1;
    uint16_t *const expected = expected_storage + 1;
    uint16_t *const output = output_storage + 1;

    source_storage[0] = UINT16_C(0xA55A);
    source_storage[frame_count + 1] = UINT16_C(0x5AA5);
    expected_storage[0] = UINT16_C(0xC33C);
    expected_storage[frame_count + 1] = UINT16_C(0x3CC3);
    output_storage[0] = UINT16_C(0x9669);
    output_storage[frame_count + 1] = UINT16_C(0x6996);

    for (int source_y = 0; source_y < ui_height; source_y++) {
        for (int source_x = 0; source_x < ui_width; source_x++) {
            source[(size_t)source_y * (size_t)ui_width + (size_t)source_x] =
                sample_pixel(source_x, source_y, UINT32_C(0x34900006));
        }
    }
    for (size_t i = 0; i < frame_count; i++) {
        expected[i] = FRAME_INITIAL;
        output[i] = FRAME_INITIAL;
    }

    for (int local_y = 0; local_y < height; local_y++) {
        for (int local_x = 0; local_x < width; local_x++) {
            const uint16_t pixel = source[(size_t)(y + local_y) * (size_t)ui_width +
                                          (size_t)(x + local_x)];
            const size_t native_offset =
                (size_t)(ui_width - 1 - (x + local_x)) * (size_t)ui_height +
                (size_t)(y + local_y);
            expected[native_offset] = swap_rgb565_bytes(pixel);
        }
    }

    shadow_349_update(output, source + (size_t)y * (size_t)ui_width + (size_t)x,
                      ui_width, ui_height, x, y, width, height, ui_width);

    for (size_t i = 0; i < frame_count; i++) {
        assert(output[i] == expected[i]);
    }
    assert(source_storage[0] == UINT16_C(0xA55A));
    assert(source_storage[frame_count + 1] == UINT16_C(0x5AA5));
    assert(expected_storage[0] == UINT16_C(0xC33C));
    assert(expected_storage[frame_count + 1] == UINT16_C(0x3CC3));
    assert(output_storage[0] == UINT16_C(0x9669));
    assert(output_storage[frame_count + 1] == UINT16_C(0x6996));
}

int main(void)
{
    run_case(640, 172, 0, 0, 640, 172, 640, UINT32_C(0x34900001));
    run_case(64, 37, 7, 9, 21, 15, 27, UINT32_C(0x34900002));
    run_case(640, 172, 623, 161, 17, 11, 23, UINT32_C(0x34900003));
    run_case(19, 35, 3, 4, 16, 31, 20, UINT32_C(0x34900004));
    run_case(1, 1, 0, 0, 1, 1, 1, UINT32_C(0x34900005));
    run_direct_frame_case();

    puts("shadow_349: rectangle conversion matches scalar reference and preserves canaries");
    return 0;
}
