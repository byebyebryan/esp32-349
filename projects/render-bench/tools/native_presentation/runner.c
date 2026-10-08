/* Compile the unchanged application scene; board calls are native-only stubs. */
#include "../../main/main.c"
#include <string.h>

#define WIDTH 640
#define HEIGHT 172
#define FRAMES 160
#define SAMPLE_MS 40
static uint16_t draw_buffer[WIDTH * HEIGHT];
static uint16_t framebuffer[WIDTH * HEIGHT];

static void flush(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    assert(area->x1 >= 0 && area->y1 >= 0);
    assert(area->x2 < WIDTH && area->y2 < HEIGHT);
    for (int y = area->y1; y <= area->y2; y++) {
        size_t offset = (size_t)y * WIDTH + (size_t)area->x1;
        memcpy(framebuffer + offset, (uint16_t *)pixels + offset,
               (size_t)(area->x2 - area->x1 + 1) * sizeof(uint16_t));
    }
    lv_display_flush_ready(display);
}

static void capture(const char *directory, int frame)
{
    char path[4096];
    int length = snprintf(path, sizeof(path), "%s/frame-%03d.ppm", directory, frame);
    assert(length > 0 && (size_t)length < sizeof(path));
    FILE *file = fopen(path, "wb");
    assert(file);
    assert(fprintf(file, "P6\n%d %d\n255\n", WIDTH, HEIGHT) > 0);
    for (int y = 0; y < HEIGHT; y++) {
        uint8_t row[WIDTH * 3];
        for (int x = 0; x < WIDTH; x++) {
            uint16_t pixel = framebuffer[y * WIDTH + x];
            unsigned red = (pixel >> 11) & 31, green = (pixel >> 5) & 63;
            unsigned blue = pixel & 31;
            row[x * 3] = (uint8_t)((red << 3) | (red >> 2));
            row[x * 3 + 1] = (uint8_t)((green << 2) | (green >> 4));
            row[x * 3 + 2] = (uint8_t)((blue << 3) | (blue >> 2));
        }
        assert(fwrite(row, sizeof(row), 1, file) == 1);
    }
    assert(fclose(file) == 0);
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    lv_init();
    lv_display_t *display = lv_display_create(WIDTH, HEIGHT);
    assert(display);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer),
                           LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(display, flush);
    app_main();
    lv_obj_update_layout(lv_screen_active());
    int horizontal_turns = 0, vertical_turns = 0;
    puts("frame,time_ms,x,y,dx,dy");
    for (int frame = 0; frame < FRAMES; frame++) {
        int previous_dx = ball_dx, previous_dy = ball_dy;
        for (int ms = 0; ms < SAMPLE_MS; ms++) {
            lv_tick_inc(1);
            lv_timer_handler();
        }
        horizontal_turns += ball_dx != previous_dx;
        vertical_turns += ball_dy != previous_dy;
        assert(ball_x >= 0 && ball_x + BALL_SIZE <= WIDTH);
        assert(ball_y >= 0 && ball_y + BALL_SIZE <= HEIGHT);
        lv_refr_now(display);
        capture(argv[1], frame);
        printf("%d,%u,%d,%d,%d,%d\n", frame, lv_tick_get(), ball_x, ball_y,
               ball_dx, ball_dy);
    }
    assert(horizontal_turns > 0 && vertical_turns > 0);
    lv_deinit();
    return 0;
}
