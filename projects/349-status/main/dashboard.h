#pragma once

#include <stdbool.h>
#include "cJSON.h"

/* Nullable readings distinguish unavailable hardware from a zero reading. */
typedef struct {
    bool valid;
    bool cpu_valid, mem_valid, network_valid;
    float cpu, mem;
    bool network;
    bool battery_present, charging_known, charging;
    float battery_level;
    bool volume_present, mute_known, mute;
    float volume_level;
    bool bluetooth_valid;
    int bluetooth;
} status_dashboard_t;

/* Missing/null dashboard selects the legacy UI. Malformed readings fail the
 * entire update, so a chunked sync cannot commit a partially parsed panel. */
bool dashboard_parse(const cJSON *obj, status_dashboard_t *out);
