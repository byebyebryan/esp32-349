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
    assert(!d.rx_bytes_per_s_valid && !d.tx_bytes_per_s_valid); /* Older host. */
    assert(!d.cpu_freq_mhz_valid && !d.mem_used_bytes_valid);
    assert(parse("{\"cpu_freq_mhz\":null,\"mem_used_bytes\":null}", &d));
    assert(!d.cpu_freq_mhz_valid && !d.mem_used_bytes_valid);
    assert(parse("{\"cpu_freq_mhz\":3600.5,\"mem_used_bytes\":25769803776}", &d));
    assert(d.cpu_freq_mhz_valid && d.cpu_freq_mhz == 3600.5);
    assert(d.mem_used_bytes_valid && d.mem_used_bytes == 25769803776.0);
    assert(parse("{\"cpu_freq_mhz\":0,\"mem_used_bytes\":0}", &d));
    assert(d.cpu_freq_mhz_valid && d.cpu_freq_mhz == 0);
    assert(d.mem_used_bytes_valid && d.mem_used_bytes == 0);
    assert(parse("{\"cpu_freq_mhz\":100000,\"mem_used_bytes\":1125899906842624}", &d));
    assert(d.cpu_freq_mhz == 100000.0 && d.mem_used_bytes == 1125899906842624.0);
    assert(parse("{\"cpu\":0,\"mem\":1,\"network\":false,\"battery\":{\"level\":0.5,\"charging\":true},\"volume\":{\"level\":0.2,\"mute\":null},\"bluetooth\":999}", &d));
    assert(d.cpu_valid && d.cpu == 0 && d.mem_valid && d.mem == 1);
    assert(d.network_valid && !d.network && d.battery_present && d.charging_known && d.charging);
    assert(d.volume_present && !d.mute_known && d.bluetooth_valid && d.bluetooth == 999);
    assert(parse("{\"cpu\":0.25,\"mem\":0.75,\"network\":true,\"rx_bytes_per_s\":0,\"tx_bytes_per_s\":1000000000000,\"future\":1}", &d));
    assert(d.valid && d.rx_bytes_per_s_valid && d.rx_bytes_per_s == 0);
    assert(d.tx_bytes_per_s_valid && d.tx_bytes_per_s == 1000000000000.0);
    const char *bad[] = {
        "[]", "{\"cpu\":true}", "{\"cpu\":-0.01}", "{\"mem\":1.01}",
        "{\"cpu\":1e999}", "{\"network\":1}", "{\"battery\":{}}",
        "{\"volume\":{\"level\":null}}", "{\"battery\":{\"level\":0.5,\"charging\":1}}",
        "{\"bluetooth\":1000}", "{\"bluetooth\":-1}", "{\"bluetooth\":1.5}",
        "{\"rx_bytes_per_s\":true}", "{\"rx_bytes_per_s\":-1}",
        "{\"rx_bytes_per_s\":1000000000001}", "{\"tx_bytes_per_s\":1e999}",
        "{\"tx_bytes_per_s\":\"12\"}",
        "{\"cpu_freq_mhz\":true}", "{\"cpu_freq_mhz\":-1}",
        "{\"cpu_freq_mhz\":100001}", "{\"cpu_freq_mhz\":1e999}",
        "{\"cpu_freq_mhz\":\"3600\"}", "{\"mem_used_bytes\":true}",
        "{\"mem_used_bytes\":-1}", "{\"mem_used_bytes\":1.5}",
        "{\"mem_used_bytes\":1125899906842625}", "{\"mem_used_bytes\":1e999}",
        "{\"mem_used_bytes\":\"24G\"}"
    };
    for (unsigned i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        assert(!parse(bad[i], &d));
    }
    puts("dashboard parser: legacy, nullable, absent hardware, finite ratios, and malformed readings pass");
    return 0;
}
