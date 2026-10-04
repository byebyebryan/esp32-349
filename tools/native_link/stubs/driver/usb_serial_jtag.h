#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    size_t rx_buffer_size;
    size_t tx_buffer_size;
} usb_serial_jtag_driver_config_t;

esp_err_t usb_serial_jtag_driver_install(
    const usb_serial_jtag_driver_config_t *config);
int usb_serial_jtag_read_bytes(uint8_t *buffer, size_t size,
                               unsigned int timeout_ticks);
int usb_serial_jtag_write_bytes(const void *buffer, size_t size,
                                unsigned int timeout_ticks);
bool usb_serial_jtag_is_connected(void);
