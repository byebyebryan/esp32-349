#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Deterministic clock controls exported by the standalone protocol runner. */
void native_protocol_set_time(int64_t now_us);
void native_protocol_advance_time(int64_t delta_us);
void native_protocol_set_connected(bool connected);
bool native_protocol_rtc_last_set(int64_t *epoch_utc, int *offset_sec, int *set_count);
