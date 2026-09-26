#pragma once

#define portMAX_DELAY 0xffffffffu
#define configASSERT(value) do { if (!(value)) __builtin_trap(); } while (0)
