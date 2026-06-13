// L76K / CASIC GNSS bring-up for the Seeed Wio Tracker L1.
//
// CRITICAL (root-caused 2026-06-13, ground truth = pinned Meshtastic SHA
// 88137c60): the GNSS on this board speaks CASIC/PCAS, NOT MTK/PMTK, and runs
// at a FIXED 9600 baud. Meshtastic keeps it at 9600 (GPS_BAUDRATE_FIXED), inits
// with PCAS commands, sends NO PMTK, and never switches to 57600. An earlier
// PMTK251->57600 attempt here was IGNORED by the module (it stayed at 9600)
// while the MCU moved to 57600 -> permanent baud mismatch -> zero parsed NMEA
// (the sats=0/no-fix bug seen on both boards outdoors). Pins and standby were
// already correct: PIN_SERIAL1_RX/TX = the GPS UART pins, and standby HIGH =
// awake (GPS_STANDBY_ACTIVE defaults LOW upstream). The fix is: stay at 9600,
// drop PMTK, use the Meshtastic PCAS init sequence.
#pragma once
#include <Arduino.h>

// Send a $<body>*<checksum>\r\n NMEA/PCAS sentence (checksum = XOR of body
// chars). Verified to reproduce Meshtastic's literal checksums, e.g.
// l76k_send("PCAS04,7") -> "$PCAS04,7*1E".
static inline void l76k_send(Stream &port, const char *body) {
  uint8_t ck = 0;
  for (const char *p = body; *p; ++p) ck ^= (uint8_t)*p;
  char tail[8];
  snprintf(tail, sizeof(tail), "*%02X\r\n", ck);
  port.print('$');
  port.print(body);
  port.print(tail);
}

// Drain the GNSS UART for `window_ms`, echoing the first few raw NMEA/PCAS lines
// plus the total byte count to the debug port. This is the proof the module is
// talking: bytes>0 = GNSS alive at this baud; bytes==0 = still silent (deeper
// issue). Replaces the old PMTK-ACK sniffer (this module never emits $PMTK001).
static inline void l76k_drain_echo(HardwareSerial &gps, Stream &dbg,
                                   uint32_t window_ms) {
  uint32_t until = millis() + window_ms;
  char line[120];
  size_t n = 0;
  uint32_t bytes = 0, lines = 0;
  while ((int32_t)(until - millis()) > 0) {
    while (gps.available()) {
      char c = (char)gps.read();
      bytes++;
      if (c == '\n' || n >= sizeof(line) - 1) {
        line[n] = 0;
        if (n > 0 && line[0] == '$' && lines < 6) {
          dbg.print("[gps] ");
          dbg.println(line);
          lines++;
        }
        n = 0;
      } else if (c != '\r') {
        line[n++] = c;
      }
    }
  }
  dbg.printf("[gps] init drain: %lu bytes, %lu lines\n",
             (unsigned long)bytes, (unsigned long)lines);
}

// Bring up the GNSS. `baud` must be the module's fixed 9600. `interval_ms` is
// currently unused: we mirror Meshtastic's L76K path, which sets no rate (module
// default ~1 Hz). A PCAS02 rate command can be added later once a fix is proven.
static inline void l76k_init(HardwareSerial &gps, Stream &dbg,
                             uint32_t baud, uint16_t interval_ms) {
  (void)interval_ms;
  gps.begin(baud);
  delay(250);
  // Meshtastic GNSS_MODEL_MTK/L76K init (literal PCAS commands + 250 ms gaps):
  l76k_send(gps, "PCAS04,7");                          // constellations: GPS+GLONASS+BeiDou
  delay(250);
  l76k_send(gps, "PCAS03,1,0,0,0,1,0,0,0,0,0,,,0,0");  // NMEA output: GGA + RMC on
  delay(250);
  l76k_send(gps, "PCAS11,3");                           // nav/dynamic mode
  delay(250);
  dbg.printf("[l76k] init: PCAS @ %u baud (GGA+RMC)\n", (unsigned)baud);
  l76k_drain_echo(gps, dbg, 1500);
}
