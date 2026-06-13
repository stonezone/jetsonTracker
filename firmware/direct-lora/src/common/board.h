// Board-safety aliases for the Seeed Wio Tracker L1.
//
// CRITICAL: on this variant PIN_LED2 (logical pin 12 / P1.00) is the SAME
// physical pin as PIN_BUZZER — verified against variant.h. Driving "LED2"
// per packet would chirp the buzzer continuously. STATUS_LED is therefore
// pinned to PIN_LED1 (the only safe indicator) and we never touch LED2/D12.
#pragma once
#include <Arduino.h>

#define STATUS_LED PIN_LED1
