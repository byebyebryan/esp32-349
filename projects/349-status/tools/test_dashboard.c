/* Native checks execute the firmware parser against the same IDF cJSON. */
#include <assert.h>
#include <math.h>
#include <stdio.h>

#include "dashboard.h"

static bool parse(const char *json, status_dashboard_t *out)
{
    cJSON *obj = cJSON_Parse(json);
    assert(obj);
    const bool valid = dashboard_parse(obj, out);
    cJSON_Delete(obj);
    return valid;
}

int main(void)
{
    status_dashboard_t d;
    assert(dashboard_parse(NULL, &d) && !d.valid); /* Old host. */
    assert(parse("{\"cpu\":null,\"mem\":null,\"network\":null,\"battery\":null,\"volume\":null,\"bluetooth\":null}", &d));
    assert(d.valid && !d.cpu_valid && !d.mem_valid && !d.network_valid && !d.battery_present);
    assert(parse("{\"cpu\":0,\"mem\":1,\"network\":false,\"battery\":{\"level\":0.5,\"charging\":true},\"volume\":{\"level\":0.2,\"mute\":null},\"bluetooth\":999}", &d));
    assert(d.cpu_valid && d.cpu == 0 && d.mem_valid && d.mem == 1);
    assert(d.network_valid && !d.network && d.battery_present && d.charging_known && d.charging);
    assert(d.volume_present && !d.mute_known && d.bluetooth_valid && d.bluetooth == 999);
    const char *bad[] = {
        "[]", "{\"cpu\":true}", "{\"cpu\":-0.01}", "{\"mem\":1.01}",
        "{\"cpu\":1e999}", "{\"network\":1}", "{\"battery\":{}}",
        "{\"volume\":{\"level\":null}}", "{\"battery\":{\"level\":0.5,\"charging\":1}}",
        "{\"bluetooth\":1000}", "{\"bluetooth\":-1}", "{\"bluetooth\":1.5}"
    };
    for (unsigned i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        assert(!parse(bad[i], &d));
    }
    puts("dashboard parser: legacy, nullable, absent hardware, finite ratios, and malformed readings pass");
    return 0;
}
