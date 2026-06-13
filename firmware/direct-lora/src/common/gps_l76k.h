// L76K GNSS bring-up over PMTK (Quectel protocol; facts per GPT research
// 2026-06-12, cross-checked against the Quectel Lx0/Lx6 protocol spec):
//
//   - PMTK220 <interval ms>: 100..10000; values <=1000 are exact. L76K's
//     listed module max is 5 Hz -> 200 ms is the floor we target.
//   - PMTK251 <baud>: NO ACK by design (port speed changes underneath);
//     reverts to 9600 on full cold restart / standby, so this whole
//     sequence re-runs on EVERY boot.
//   - PMTK314: per-sentence output divider; RMC+GGA every fix is all the
//     packet needs (lat/lon/speed/course + quality/sats/HDOP).
//   - ACK: $PMTK001,<cmd>,<flag> with flag 3 = success.
//   - Checksum: XOR of chars between '$' and '*'.
//
// 5 Hz NMEA needs >=57600 baud to drain the UART; we use 57600 even at
// 2 Hz to keep buffer pressure at zero.
#pragma once
#include <Arduino.h>

#define L76K_RUN_BAUD 57600
#define L76K_MIN_INTERVAL_MS 200  // module max 5 Hz (Seeed L76K spec)

static inline void l76k_send(Stream &port, const char *body) {
  uint8_t ck = 0;
  for (const char *p = body; *p; ++p) ck ^= (uint8_t)*p;
  char tail[8];
  snprintf(tail, sizeof(tail), "*%02X\r\n", ck);
  port.print('$');
  port.print(body);
  port.print(tail);
}

// Drain the GNSS UART for `window_ms`, echoing any $PMTK001 ACK lines to
// the debug port. Non-blocking beyond the window: a missing ACK is logged,
// never fatal (outdoor cadence measurement is the real verification).
static inline void l76k_log_acks(HardwareSerial &gps, Stream &dbg,
                                 uint32_t window_ms) {
  uint32_t until = millis() + window_ms;
  char line[100];
  size_t n = 0;
  while ((int32_t)(until - millis()) > 0) {
    while (gps.available()) {
      char c = (char)gps.read();
      if (c == '\n' || n >= sizeof(line) - 1) {
        line[n] = 0;
        if (strstr(line, "$PMTK001")) {
          dbg.print("[l76k] ");
          dbg.println(line);
        }
        n = 0;
      } else if (c != '\r') {
        line[n++] = c;
      }
    }
  }
}

// Full boot sequence. `interval_ms` is clamped to the module's 5 Hz floor.
// Sequence per protocol spec: raise baud first (at the old rate), reopen,
// then filter + rate at the new rate.
static inline void l76k_init(HardwareSerial &gps, Stream &dbg,
                             uint32_t default_baud, uint16_t interval_ms) {
  if (interval_ms < L76K_MIN_INTERVAL_MS) interval_ms = L76K_MIN_INTERVAL_MS;

  gps.begin(default_baud);
  delay(200);
  l76k_send(gps, "PMTK251,57600");  // no ACK by design
  gps.flush();
  delay(150);
  gps.end();
  gps.begin(L76K_RUN_BAUD);
  delay(100);

  // RMC + GGA every fix, everything else off.
  l76k_send(gps, "PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0");
  char rate[24];
  snprintf(rate, sizeof(rate), "PMTK220,%u", interval_ms);
  l76k_send(gps, rate);

  dbg.printf("[l76k] init: baud=%u filter=RMC+GGA interval=%ums\n",
             (unsigned)L76K_RUN_BAUD, interval_ms);
  l76k_log_acks(gps, dbg, 1500);  // expect $PMTK001,314,3 and $PMTK001,220,3
}
