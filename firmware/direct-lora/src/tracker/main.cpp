// Direct LoRa tracker — TRACKER node (on the surfer).
// L76K GNSS -> 32-byte packet -> SX1262 TX. One-way, stateless.
// Spec: docs/superpowers/specs/2026-06-12-direct-lora-tracker.md
//
// Pin aliases (D0..) come from the vendored seeed_wio_tracker_L1 variant
// (run fetch_variant.sh once). LED language: green heartbeat = alive,
// blue flash = packet transmitted; both solid = no GNSS fix yet.

#include <Arduino.h>
#include <RadioLib.h>
#include <TinyGPSPlus.h>

#include "../common/gps_l76k.h"
#include "../common/packet.h"
#include "../common/radio_config.h"

// SX1262 wiring per variant.h: CS=D4 DIO1=D1 RESET=D2 BUSY=D3, RXEN=D5
// (TXEN is DIO2-controlled on this module -> RADIOLIB_NC).
SX1262 radio = new Module(D4, D1, D2, D3);

TinyGPSPlus gps;
static TrackerPacket pkt;
static uint16_t seq = 0;
static uint32_t last_tx_ms = 0;

static uint16_t battery_mv() {
  // VBAT on D16 behind a /2 divider; 12-bit ADC, 3.6 V internal reference.
  analogReadResolution(12);
  uint32_t raw = analogRead(PIN_VBAT);
  return (uint16_t)(raw * 3600UL * 2UL / 4095UL);
}

void setup() {
  Serial.begin(115200);   // USB debug
  pinMode(PIN_LED1, OUTPUT);
  pinMode(PIN_LED2, OUTPUT);

  // GNSS: wake from standby, then full PMTK bring-up (baud 57600, RMC+GGA
  // only, rate matched to the beacon but clamped to the L76K's 5 Hz floor).
  // Re-runs every boot by design: PMTK251/220 revert on cold restart.
  pinMode(PIN_GPS_STANDBY, OUTPUT);
  digitalWrite(PIN_GPS_STANDBY, HIGH);
  delay(300);
  l76k_init(Serial1, Serial, GPS_BAUDRATE, BEACON_INTERVAL_MS);

  // RX/TX switch: RXEN held low while transmitting is handled by RadioLib
  // via setRfSwitchPins; DIO2 drives TXEN on this module.
  radio.setRfSwitchPins(D5, RADIOLIB_NC);
  int16_t st = radio.begin(RADIO_FREQ_MHZ, RADIO_BW_KHZ, RADIO_SF, RADIO_CR,
                           RADIO_SYNC_WORD, RADIO_TX_DBM, RADIO_PREAMBLE);
  radio.setDio2AsRfSwitch(true);
  if (st != RADIOLIB_ERR_NONE) {
    // Radio dead: solid green + fast blue blink, keep printing the code.
    while (true) {
      Serial.printf("[tracker] radio init failed: %d\n", st);
      digitalWrite(PIN_LED1, HIGH);
      digitalWrite(PIN_LED2, (millis() / 125) % 2);
      delay(1000);
    }
  }
  Serial.println("[tracker] radio up; waiting for GNSS");
}

static void fill_packet() {
  memset(&pkt, 0, sizeof(pkt));
  pkt.seq = seq++;
  pkt.tracker_ms = millis();
  uint16_t flags = 0;
  uint8_t sats = gps.satellites.isValid() ? (uint8_t)gps.satellites.value() : 0;
  if (gps.location.isValid()) {
    flags |= PKT_FLAG_FIX_VALID;
    pkt.lat_e7 = (int32_t)(gps.location.lat() * 1e7);
    pkt.lon_e7 = (int32_t)(gps.location.lng() * 1e7);
  }
  if (gps.speed.isValid()) {
    flags |= PKT_FLAG_SPEED_VALID;
    pkt.speed_cm_s = (uint16_t)min(gps.speed.mps() * 100.0, 65535.0);
  }
  if (gps.course.isValid()) {
    flags |= PKT_FLAG_COURSE_VALID;
    pkt.course_cdeg = (uint16_t)(gps.course.deg() * 100.0) % 36000;
  }
  if (gps.hdop.isValid())
    pkt.hacc_cm = (uint16_t)min(gps.hdop.hdop() * 500.0, 65535.0);  // ~5m/HDOP heuristic
  pkt.flags_sats = flags | ((uint16_t)sats << 8);
  pkt.battery_mv = battery_mv();
  pkt_seal(&pkt);
}

void loop() {
  while (Serial1.available()) gps.encode(Serial1.read());

  digitalWrite(PIN_LED1, (millis() / 500) % 2);            // heartbeat
  uint32_t now = millis();
  if (now - last_tx_ms < BEACON_INTERVAL_MS) return;
  // Airtime guard: independent of BEACON_INTERVAL_MS so a config edit can
  // never push the duty cycle past the regulatory budget.
  if (now - last_tx_ms < RADIO_MIN_TX_INTERVAL_MS) return;
  last_tx_ms = now;

  fill_packet();
  digitalWrite(PIN_LED2, HIGH);
  int16_t st = radio.transmit((uint8_t *)&pkt, PKT_LEN);
  digitalWrite(PIN_LED2, LOW);

  if ((pkt.seq % 10) == 0)
    Serial.printf("[tracker] seq=%u fix=%d sats=%u batt=%umV tx=%d\n",
                  pkt.seq, (int)(pkt.flags_sats & PKT_FLAG_FIX_VALID),
                  pkt.flags_sats >> 8, pkt.battery_mv, st);
}
