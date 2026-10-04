#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "link.h"

#define MAX_LINES 8
#define STREAM_CAPACITY (LINK_LINE_MAX * 8)

static TaskFunction_t s_task;
static uint8_t s_stream[STREAM_CAPACITY];
static size_t s_stream_length;
static size_t s_stream_offset;
static const size_t s_fragments[] = {1, 2, 5, 17, 255, 3, 128, 7};
static size_t s_fragment_index;
static jmp_buf s_task_finished;
static char s_lines[MAX_LINES][LINK_LINE_MAX];
static size_t s_line_lengths[MAX_LINES];
static int s_line_count;
static int s_overflow_count;
static bool s_driver_installed;
static bool s_vfs_installed;
static size_t s_rx_buffer_size;
static size_t s_tx_buffer_size;

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "CHECK failed at %s:%d: %s\n", \
                __FILE__, __LINE__, #condition); \
        exit(1); \
    } \
} while (0)

#include "../../main/link.c"

SemaphoreHandle_t xSemaphoreCreateMutex(void)
{
    return (void *)1;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t semaphore, unsigned int timeout)
{
    (void)semaphore;
    (void)timeout;
    return pdTRUE;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t semaphore)
{
    (void)semaphore;
    return pdTRUE;
}

BaseType_t xTaskCreate(TaskFunction_t task, const char *name,
                       unsigned int stack_depth, void *argument,
                       UBaseType_t priority, TaskHandle_t *handle)
{
    (void)name;
    (void)stack_depth;
    (void)argument;
    (void)priority;
    (void)handle;
    s_task = task;
    return pdPASS;
}

esp_err_t usb_serial_jtag_driver_install(
    const usb_serial_jtag_driver_config_t *config)
{
    s_driver_installed = true;
    s_rx_buffer_size = config->rx_buffer_size;
    s_tx_buffer_size = config->tx_buffer_size;
    return ESP_OK;
}

void usb_serial_jtag_vfs_use_driver(void)
{
    s_vfs_installed = true;
}

int usb_serial_jtag_read_bytes(uint8_t *buffer, size_t size,
                               unsigned int timeout_ticks)
{
    (void)timeout_ticks;
    if (s_stream_offset == s_stream_length) {
        longjmp(s_task_finished, 1);
    }
    size_t count = s_fragments[s_fragment_index++ %
                               (sizeof(s_fragments) / sizeof(s_fragments[0]))];
    if (count > size) count = size;
    if (count > s_stream_length - s_stream_offset) {
        count = s_stream_length - s_stream_offset;
    }
    memcpy(buffer, s_stream + s_stream_offset, count);
    s_stream_offset += count;
    return (int)count;
}

int usb_serial_jtag_write_bytes(const void *buffer, size_t size,
                                unsigned int timeout_ticks)
{
    (void)buffer;
    (void)timeout_ticks;
    return (int)size;
}

bool usb_serial_jtag_is_connected(void)
{
    return true;
}

static void capture_line(const char *json)
{
    CHECK(s_line_count < MAX_LINES);
    const size_t length = strlen(json);
    CHECK(length < sizeof(s_lines[0]));
    memcpy(s_lines[s_line_count], json, length + 1);
    s_line_lengths[s_line_count] = length;
    s_line_count++;
}

static void capture_overflow(void)
{
    s_overflow_count++;
}

static void append(const void *data, size_t length)
{
    CHECK(length <= sizeof(s_stream) - s_stream_length);
    memcpy(s_stream + s_stream_length, data, length);
    s_stream_length += length;
}

static void append_byte(uint8_t value)
{
    append(&value, sizeof(value));
}

static void append_exact_crlf_boundary(void)
{
    const size_t physical_length = LINK_LINE_MAX - 1;
    append(LINK_PREFIX, sizeof(LINK_PREFIX) - 1);
    const size_t payload_length = physical_length - (sizeof(LINK_PREFIX) - 1) - 1;
    for (size_t i = 0; i < payload_length; i++) append_byte('q');
    append_byte('\r');
    append_byte('\n');
}

static void append_oversized_line(size_t bytes_before_suffix, const char *suffix)
{
    append(LINK_PREFIX, sizeof(LINK_PREFIX) - 1);
    CHECK(bytes_before_suffix >= LINK_LINE_MAX);
    const size_t filler_length = bytes_before_suffix - (sizeof(LINK_PREFIX) - 1);
    for (size_t i = 0; i < filler_length; i++) append_byte('x');
    append(suffix, strlen(suffix));
    append_byte('\n');
}

static void append_valid_frame(const char *json)
{
    append(LINK_PREFIX, sizeof(LINK_PREFIX) - 1);
    append(json, strlen(json));
    append("\r\n", 2);
}

static void run_link_task(void)
{
    if (setjmp(s_task_finished) == 0) {
        s_task(NULL);
    }
}

int main(void)
{
    append_exact_crlf_boundary();
    append_oversized_line(LINK_LINE_MAX,
                          "@349 {\"injected\":true}");
    append_valid_frame("{\"ok\":1}");
    append_oversized_line(LINK_LINE_MAX * 3,
                          "@349 {\"also_injected\":true}");
    append_valid_frame("{\"ok\":2}");

    link_set_overflow_cb(capture_overflow);
    CHECK(link_start(capture_line) == ESP_OK);
    CHECK(s_driver_installed && s_vfs_installed);
    CHECK(s_rx_buffer_size == LINK_RX_BUFFER && s_tx_buffer_size == LINK_TX_BUFFER);
    CHECK(s_task != NULL);
    run_link_task();

    CHECK(s_line_count == 3);
    CHECK(s_line_lengths[0] == LINK_LINE_MAX - 1 -
          (sizeof(LINK_PREFIX) - 1) - 1);
    for (size_t i = 0; i < s_line_lengths[0]; i++) {
        CHECK(s_lines[0][i] == 'q');
    }
    CHECK(strcmp(s_lines[1], "{\"ok\":1}") == 0);
    CHECK(strcmp(s_lines[2], "{\"ok\":2}") == 0);
    CHECK(s_overflow_count == 2);
    puts("native link receiver: pass (exact boundary, fragmented overflow discard, recovery)");
    return 0;
}
