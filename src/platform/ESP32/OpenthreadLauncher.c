/*
 *
 *    Copyright (c) 2022 Project CHIP Authors
 *
 *    Licensed under the Apache License, Version 2.0 (the "License");
 *    you may not use this file except in compliance with the License.
 *    You may obtain a copy of the License at
 *
 *        http://www.apache.org/licenses/LICENSE-2.0
 *
 *    Unless required by applicable law or agreed to in writing, software
 *    distributed under the License is distributed on an "AS IS" BASIS,
 *    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *    See the License for the specific language governing permissions and
 *    limitations under the License.
 */

#include "driver/uart.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_netif_types.h"
#include "esp_openthread.h"
#include "esp_openthread_lock.h"
#include "esp_openthread_netif_glue.h"
#include "esp_openthread_types.h"
#include "esp_vfs_eventfd.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "openthread/instance.h"
#include "openthread/logging.h"
#include "openthread/tasklet.h"

static esp_netif_t * openthread_netif                       = NULL;
static esp_openthread_platform_config_t * s_platform_config = NULL;

static esp_netif_t * init_openthread_netif(const esp_openthread_platform_config_t * config)
{
    esp_netif_config_t cfg = ESP_NETIF_DEFAULT_OPENTHREAD();
    esp_netif_t * netif    = esp_netif_new(&cfg);
    assert(netif != NULL);
    ESP_ERROR_CHECK(esp_netif_attach(netif, esp_openthread_netif_glue_init(config)));

    return netif;
}

static void ot_task_worker(void * context)
{
    // Run the main loop
    esp_openthread_launch_mainloop();

    esp_netif_destroy(openthread_netif);
    esp_openthread_netif_glue_deinit();

    esp_vfs_eventfd_unregister();
    vTaskDelete(NULL);
}

esp_err_t set_openthread_platform_config(esp_openthread_platform_config_t * config)
{
    if (!s_platform_config)
    {
        s_platform_config = (esp_openthread_platform_config_t *) malloc(sizeof(esp_openthread_platform_config_t));
        if (!s_platform_config)
        {
            return ESP_ERR_NO_MEM;
        }
    }
    memcpy(s_platform_config, config, sizeof(esp_openthread_platform_config_t));
    return ESP_OK;
}

esp_err_t openthread_init_stack(void)
{
    // Used eventfds:
    // * netif
    // * ot task queue
    // * radio driver
    esp_vfs_eventfd_config_t eventfd_config = {
        .max_fds = 3,
    };

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_vfs_eventfd_register(&eventfd_config));
    assert(s_platform_config);
    // Initialize the OpenThread stack
    ESP_ERROR_CHECK(esp_openthread_init(s_platform_config));
    // Initialize the esp_netif bindings
    openthread_netif = init_openthread_netif(s_platform_config);
    free(s_platform_config);
    s_platform_config = NULL;
    return ESP_OK;
}

esp_err_t openthread_launch_task(void)
{
    xTaskCreate(ot_task_worker, "ot_task", CONFIG_THREAD_TASK_STACK_SIZE, xTaskGetCurrentTaskHandle(), 5, NULL);
    return ESP_OK;
}
