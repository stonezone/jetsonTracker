// nRF52 hardware watchdog (audit F1). A wedged radio/GPS or a hung loop is
// otherwise unrecoverable in the field — the WDT force-resets the MCU so the
// node reboots and re-acquires. Register-level (no extra lib); the WDT_* /
// NRF_WDT symbols come from the nRF52840 MDK headers the core already includes.
//
// IMPORTANT: once started the nRF52 WDT CANNOT be stopped and its timeout is
// fixed. Start it AFTER setup()'s slow init and feed it once per loop(). Both
// node loops are non-blocking (millis-gated, GPS pumped each pass), so an 8 s
// timeout never trips in normal operation but catches a true hang.
#pragma once
#include <Arduino.h>

static const uint32_t WDT_TIMEOUT_S = 8;

static inline void wdt_start(uint32_t timeout_s = WDT_TIMEOUT_S) {
  NRF_WDT->CONFIG = (WDT_CONFIG_HALT_Pause << WDT_CONFIG_HALT_Pos) |
                    (WDT_CONFIG_SLEEP_Run  << WDT_CONFIG_SLEEP_Pos);
  NRF_WDT->CRV = (timeout_s * 32768UL) - 1;   // 32.768 kHz LFCLK
  NRF_WDT->RREN = WDT_RREN_RR0_Msk;            // reload register 0
  NRF_WDT->TASKS_START = 1;
}

static inline void wdt_feed() {
  NRF_WDT->RR[0] = WDT_RR_RR_Reload;
}
