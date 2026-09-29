#pragma once

typedef struct {
    const char *version;
} esp_app_desc_t;

const esp_app_desc_t *esp_app_get_description(void);
void esp_app_get_elf_sha256(char *dst, int length);
