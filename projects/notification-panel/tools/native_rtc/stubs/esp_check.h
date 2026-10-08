#pragma once

#define ESP_RETURN_ON_ERROR(expression, tag, ...) do { \
    const esp_err_t result = (expression); \
    (void)(tag); \
    if (result != ESP_OK) return result; \
} while (0)
