#pragma once

#include "FreeRTOS.h"

typedef void (*TaskFunction_t)(void *);
typedef void *TaskHandle_t;

BaseType_t xTaskCreate(TaskFunction_t task, const char *name,
                       unsigned int stack_depth, void *argument,
                       UBaseType_t priority, TaskHandle_t *handle);
