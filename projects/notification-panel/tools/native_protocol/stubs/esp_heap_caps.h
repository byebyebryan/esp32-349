#pragma once

#include <stddef.h>

#define MALLOC_CAP_SPIRAM 0x1
#define MALLOC_CAP_8BIT 0x2

void *heap_caps_calloc(size_t count, size_t size, unsigned int capabilities);
void heap_caps_free(void *memory);
