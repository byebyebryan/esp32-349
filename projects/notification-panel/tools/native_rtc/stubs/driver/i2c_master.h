#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef void *i2c_master_bus_handle_t;
typedef void *i2c_master_dev_handle_t;
typedef struct {
    int dev_addr_length;
    int scl_speed_hz;
    uint16_t device_address;
} i2c_device_config_t;

#define I2C_ADDR_BIT_LEN_7 0

esp_err_t i2c_master_bus_add_device(i2c_master_bus_handle_t bus,
                                  const i2c_device_config_t *config,
                                  i2c_master_dev_handle_t *device);
esp_err_t i2c_master_transmit_receive(i2c_master_dev_handle_t device,
                                    const uint8_t *write_buffer, size_t write_size,
                                    uint8_t *read_buffer, size_t read_size, int timeout_ms);
esp_err_t i2c_master_transmit(i2c_master_dev_handle_t device,
                            const uint8_t *buffer, size_t size, int timeout_ms);
