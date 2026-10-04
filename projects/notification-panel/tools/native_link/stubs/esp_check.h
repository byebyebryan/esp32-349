#pragma once

#include "esp_err.h"

#define ESP_RETURN_ON_ERROR(expression, tag, message) do { \
    (void)(tag); \
    (void)(message); \
    const esp_err_t native_error = (expression); \
    if (native_error != ESP_OK) return native_error; \
} while (0)
