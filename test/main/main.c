/*
 * ESP32-P4 overclock check.
 *
 * Measures the real CPU clock against the crystal, then runs a PSRAM pattern
 * test on core 0 and a compute check on core 1 until reset. Any FAIL line
 * means the clocks are too high for this chip.
 */
#include <inttypes.h>
#include <stdio.h>
#include "esp_clk_tree.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#define PSRAM_WORDS   (4u * 1024u * 1024u / 4u)   /* 4 MiB: larger than the caches */
#define HASH_ROUNDS   4000000u
#define HASH_EXPECTED ((uint32_t)0x39982d98u)     /* computed off-device */

static volatile uint32_t s_psram_passes, s_cpu_passes, s_errors;

/* CPU cycles counted over 10 ms of crystal-backed time, in kHz. */
static uint32_t measure_cpu_khz(void)
{
    int64_t t0 = esp_timer_get_time();
    uint32_t c0 = esp_cpu_get_cycle_count();
    while (esp_timer_get_time() - t0 < 10000) {
    }
    uint32_t cycles = esp_cpu_get_cycle_count() - c0;   /* under 2^32 below 400 GHz */
    int64_t us = esp_timer_get_time() - t0;
    return (uint32_t)((uint64_t)cycles * 1000u / (uint64_t)us);
}

static uint32_t pattern(uint32_t kind, uint32_t index, uint32_t seed)
{
    switch (kind) {
    case 0: return 0u;
    case 1: return 0xFFFFFFFFu;
    case 2: return 0xAAAAAAAAu;
    case 3: return 0x55555555u;
    case 4: return index;
    case 5: return ~index;
    default: return (index + seed) * 2654435761u;       /* changes every pass */
    }
}

static void psram_task(void *arg)
{
    uint32_t *buf = heap_caps_malloc(PSRAM_WORDS * 4u, MALLOC_CAP_SPIRAM);
    if (buf == NULL) {
        printf("PSRAM test skipped: no 4 MiB PSRAM block (is CONFIG_SPIRAM on?)\n");
        vTaskDelete(NULL);
    }
    for (uint32_t pass = 0;; pass++) {
        for (uint32_t kind = 0; kind < 7u; kind++) {
            for (uint32_t i = 0; i < PSRAM_WORDS; i++) {
                buf[i] = pattern(kind, i, pass);
            }
            for (uint32_t i = 0; i < PSRAM_WORDS; i++) {
                uint32_t want = pattern(kind, i, pass);
                if (buf[i] != want) {
                    s_errors++;
                    printf("FAIL psram: pass %" PRIu32 " pattern %" PRIu32 " offset 0x%" PRIx32
                           " expected 0x%08" PRIx32 " read 0x%08" PRIx32 "\n",
                           pass, kind, i * 4u, want, buf[i]);
                    break;
                }
            }
        }
        s_psram_passes++;
    }
}

static void cpu_task(void *arg)
{
    for (;;) {
        uint32_t x = 0x12345678u, h = 0u;
        for (uint32_t i = 0; i < HASH_ROUNDS; i++) {
            x ^= x << 13;
            x ^= x >> 17;
            x ^= x << 5;
            h = (h ^ x) * 16777619u;
        }
        if (h != HASH_EXPECTED) {
            s_errors++;
            printf("FAIL cpu: hash 0x%08" PRIx32 " expected 0x%08" PRIx32 "\n", h, HASH_EXPECTED);
        }
        s_cpu_passes++;
    }
}

void app_main(void)
{
    uint32_t configured = 0;
    esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_CPU, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &configured);
    printf("\nESP32-P4 overclock check\n");
    printf("configured: CPU %d MHz", CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
#ifdef CONFIG_SPIRAM_SPEED
    printf(", PSRAM %d MHz", CONFIG_SPIRAM_SPEED);
#endif
    printf(" (clock tree reports %" PRIu32 " MHz)\n", configured / 1000000u);

    xTaskCreatePinnedToCore(psram_task, "psram", 4096, NULL, 1, NULL, 0);
    xTaskCreatePinnedToCore(cpu_task, "cpu", 4096, NULL, 1, NULL, 1);

    for (;;) {
        uint32_t khz = measure_cpu_khz();
        printf("[%5" PRId64 " s] measured CPU %" PRIu32 ".%03" PRIu32 " MHz | psram passes %" PRIu32
               " | cpu passes %" PRIu32 " | %s (%" PRIu32 " errors)\n",
               esp_timer_get_time() / 1000000, khz / 1000u, khz % 1000u,
               s_psram_passes, s_cpu_passes, s_errors ? "FAIL" : "ok", s_errors);
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
