#pragma once
#include <stdbool.h>

/* Bound recursive parsing/deletion within the 6 KiB link-task stack. Current
 * messages need at most five container levels, including styled card arrays. */
#define PROTO_JSON_MAX_DEPTH 16

/* Handle one JSON message from the host (prefix already stripped). */
void proto_handle_line(const char *json);

/* Announce the device; also sent in reply to a host hello request. */
void proto_send_hello(void);

/* Ask the host for a full sync (link RX overflow). */
void proto_handle_overflow(void);

/* Device-initiated actions (touch). */
void proto_send_input_dismiss(int id);
void proto_send_input_browse(bool home, int generation);
void proto_send_input_history_idle(int generation);
bool proto_send_input_activate(int id, int open_revision);
