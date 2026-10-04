#pragma once

#include <stdbool.h>
#include "cJSON.h"

/* Nullable readings distinguish unavailable hardware from a zero reading. */
typedef struct {
    bool valid;
    bool cpu_valid, mem_valid, network_valid;
    float cpu, mem;
    bool cpu_freq_mhz_valid, mem_used_bytes_valid;
    double cpu_freq_mhz, mem_used_bytes;
    bool network;
    bool rx_bytes_per_s_valid, tx_bytes_per_s_valid;
    double rx_bytes_per_s, tx_bytes_per_s;
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
