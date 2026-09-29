#include "dashboard.h"

#include <math.h>
#include <string.h>

static bool absent(const cJSON *item)
{
    return item == NULL || cJSON_IsNull(item);
}

static bool ratio(const cJSON *item, float *value, bool *known)
{
    *known = !absent(item);
    if (!*known) {
        return true;
    }
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)
            || item->valuedouble < 0 || item->valuedouble > 1) {
        return false;
    }
    *value = (float)item->valuedouble;
    return true;
}

static bool boolean(const cJSON *item, bool *value, bool *known)
{
    *known = !absent(item);
    if (!*known) {
        return true;
    }
    if (!cJSON_IsBool(item)) {
        return false;
    }
    *value = cJSON_IsTrue(item);
    return true;
}

static bool rate(const cJSON *item, double *value, bool *known)
{
    *known = !absent(item);
    if (!*known) {
        return true;
    }
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)
            || item->valuedouble < 0 || item->valuedouble > 1000000000000.0) {
        return false;
    }
    *value = item->valuedouble;
    return true;
}

static bool level(const cJSON *item, float *value, bool *present,
                  const char *flag, bool *flag_value, bool *flag_known)
{
    *present = !absent(item);
    if (!*present) {
        return true;
    }
    bool known;
    return cJSON_IsObject(item)
        && ratio(cJSON_GetObjectItemCaseSensitive(item, "level"), value, &known)
        && known
        && boolean(cJSON_GetObjectItemCaseSensitive(item, flag), flag_value, flag_known);
}

bool dashboard_parse(const cJSON *obj, status_dashboard_t *out)
{
    memset(out, 0, sizeof(*out));
    if (absent(obj)) {
        return true;
    }
    if (!cJSON_IsObject(obj)
            || !ratio(cJSON_GetObjectItemCaseSensitive(obj, "cpu"), &out->cpu, &out->cpu_valid)
            || !ratio(cJSON_GetObjectItemCaseSensitive(obj, "mem"), &out->mem, &out->mem_valid)
            || !boolean(cJSON_GetObjectItemCaseSensitive(obj, "network"), &out->network, &out->network_valid)
            || !rate(cJSON_GetObjectItemCaseSensitive(obj, "rx_bytes_per_s"),
                     &out->rx_bytes_per_s, &out->rx_bytes_per_s_valid)
            || !rate(cJSON_GetObjectItemCaseSensitive(obj, "tx_bytes_per_s"),
                     &out->tx_bytes_per_s, &out->tx_bytes_per_s_valid)
            || !level(cJSON_GetObjectItemCaseSensitive(obj, "battery"), &out->battery_level,
                      &out->battery_present, "charging", &out->charging, &out->charging_known)
            || !level(cJSON_GetObjectItemCaseSensitive(obj, "volume"), &out->volume_level,
                      &out->volume_present, "mute", &out->mute, &out->mute_known)) {
        return false;
    }
    const cJSON *bt = cJSON_GetObjectItemCaseSensitive(obj, "bluetooth");
    if (!absent(bt)) {
        if (!cJSON_IsNumber(bt) || bt->valuedouble != (double)bt->valueint
                || bt->valueint < 0 || bt->valueint > 999) {
            return false;
        }
        out->bluetooth_valid = true;
        out->bluetooth = bt->valueint;
    }
    out->valid = true;
    return true;
}
