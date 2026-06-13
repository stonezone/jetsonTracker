// Direct LoRa tracker — TRACKER node (on the surfer).
// L76K GNSS -> 32-byte packet -> SX1262 TX. One-way, stateless.
// Spec: docs/superpowers/specs/2026-06-12-direct-lora-tracker.md
//
// Pins/clocks come from the vendored seeed_wio_tracker_L1 variant via its
// SX126X_* macros (run fetch_variant.sh once). LED: green (LED1) heartbeats
// when a fresh fix is flowing, solid when GNSS is stale. We never touch LED2
// — on this board it IS the buzzer (PIN_LED2 == PIN_BUZZER == D12).

#include <Arduino.h>
#include <RadioLib.h>
#include <TinyGPSPlus.h>

#include "../common/board.h"
#include "../common/gps_l76k.h"
#include "../common/packet.h"
#include "../common/radio_config.h"

// SX1262 wiring from variant.h: CS=D4 DIO1=D1 RESET=D2 BUSY=D3; RXEN on a
// GPIO (SX126X_RXEN), TX gated by DIO2 (SX126X_DIO2_AS_RF_SWITCH); a 1.8V
// TCXO on DIO3 (SX126X_DIO3_TCXO_VOLTAGE) — that voltage MUST be passed to
// begin() or the radio won't start.
SX1262 radio = new Module(SX126X_CS, SX126X_DIO1, SX126X_RESET, SX126X_BUSY);

TinyGPSPlus gps;
static TrackerPacket pkt;
static uint16_t seq = 0;
static uint32_t last_tx_ms = 0;
static uint32_t tx_fail = 0;
static uint32_t min_tx_interval_ms = RADIO_MIN_TX_INTERVAL_MS;

// Non-blocking TX state: the radio transmits in the background (DIO1 fires
// on completion) so the loop NEVER stops draining the GPS UART during the
// ~tens-of-ms airtime. The Adafruit nRF52 Serial1 RX ring is only 64 bytes;
// a blocking transmit() would drop NMEA bytes mid-sentence and make the
// L76K's cadence look worse than it is — fatal to honest 5 Hz measurement.
static volatile bool tx_done = false;
static bool tx_busy = false;

// A fix older than this is not vouched for as the subject's current position.
static const uint32_t FIX_FRESH_MS = 2000;

// GNSS cadence telemetry (answers the open "is the L76K really 5 Hz" question
// at the bench — PMTK ACK means "accepted", not "delivering fresh fixes").
// Triggered by isUpdated() once per commit; see log_gps_cadence().
static uint32_t last_loc_update_ms = 0;

static void on_tx_done() { tx_done = true; }

static uint16_t battery_mv() {
  // VBAT on PIN_VBAT behind a /2 divider; 12-bit ADC. The 3.6 V full-scale
  // assumes the core's default internal reference (AR_INTERNAL 0.6 V * 1/6),
  // which is the Adafruit nRF52 default — set explicitly so it isn't folklore.
  analogReference(AR_INTERNAL);
  analogReadResolution(12);
  uint32_t raw = analogRead(PIN_VBAT);
  return (uint16_t)(raw * 3600UL * 2UL / 4095UL);
}

void setup() {
  Serial.begin(115200);   // USB debug
  pinMode(STATUS_LED, OUTPUT);

  // GNSS: wake from standby, then full PMTK bring-up (baud 57600, RMC+GGA
  // only, rate matched to the beacon, clamped to the L76K's 5 Hz floor).
  // Re-runs every boot by design: PMTK251/220 revert on cold restart.
  pinMode(PIN_GPS_STANDBY, OUTPUT);
  digitalWrite(PIN_GPS_STANDBY, HIGH);
  delay(300);
  l76k_init(Serial1, Serial, GPS_BAUDRATE, BEACON_INTERVAL_MS);

  // RF switch tables (HAL GPIO, no SPI) before begin(); DIO2-as-switch is an
  // SPI command so it goes AFTER begin(). TXEN slot = NC (DIO2 handles TX).
  radio.setRfSwitchPins(SX126X_RXEN, RADIOLIB_NC);
  int16_t st = radio.begin(RADIO_FREQ_MHZ, RADIO_BW_KHZ, RADIO_SF, RADIO_CR,
                           RADIO_SYNC_WORD, RADIO_TX_DBM, RADIO_PREAMBLE,
                           SX126X_DIO3_TCXO_VOLTAGE);
  radio.setDio2AsRfSwitch(true);
  if (st != RADIOLIB_ERR_NONE) {
    while (true) {  // no radio, no point beaconing — blink fast, print the code
      Serial.printf("[tracker] radio init failed: %d\n", st);
      digitalWrite(STATUS_LED, (millis() / 125) % 2);
      delay(1000);
    }
  }
  radio.setPacketSentAction(on_tx_done);  // DIO1 -> tx_done, non-blocking TX

  // Honest airtime guard: derive the floor from the radio's ACTUAL modem
  // settings instead of trusting a hardcoded constant. ToA is µs; hold the
  // beacon to >=3x airtime (≈33% duty ceiling) and never faster than the
  // compile-time RADIO_MIN_TX_INTERVAL_MS.
  uint32_t toa_ms = radio.getTimeOnAir(PKT_LEN) / 1000;
  min_tx_interval_ms = max(min_tx_interval_ms, toa_ms * 3);
  Serial.printf("[tracker] radio up; toa=%lums min_tx=%lums beacon=%dms\n",
                (unsigned long)toa_ms, (unsigned long)min_tx_interval_ms,
                BEACON_INTERVAL_MS);
}

static void fill_packet() {
  memset(&pkt, 0, sizeof(pkt));
  pkt.seq = seq;   // advanced only after the radio ACCEPTS the TX (see loop)
  pkt.tracker_ms = millis();
  pkt.gps_age_ms = PKT_GPS_AGE_STALE;
  uint16_t flags = 0;
  uint8_t sats = gps.satellites.isValid() ? (uint8_t)gps.satellites.value() : 0;

  // isValid() is sticky-true forever after the first fix — freshness MUST
  // come from age(). Only vouch for the position if it was committed recently.
  uint32_t loc_age = gps.location.age();
  if (gps.location.isValid() && loc_age < FIX_FRESH_MS) {
    flags |= PKT_FLAG_FIX_VALID;
    pkt.lat_e7 = (int32_t)(gps.location.lat() * 1e7);
    pkt.lon_e7 = (int32_t)(gps.location.lng() * 1e7);
    pkt.gps_age_ms = (uint16_t)min(loc_age, (uint32_t)(PKT_GPS_AGE_STALE - 1));
  }
  if (gps.speed.isValid() && gps.speed.age() < FIX_FRESH_MS) {
    flags |= PKT_FLAG_SPEED_VALID;
    pkt.speed_cm_s = (uint16_t)min(gps.speed.mps() * 100.0, 65535.0);
  }
  if (gps.course.isValid() && gps.course.age() < FIX_FRESH_MS) {
    flags |= PKT_FLAG_COURSE_VALID;
    pkt.course_cdeg = (uint16_t)(gps.course.deg() * 100.0) % 36000;
  }
  // hacc/sats only mean anything alongside a fresh fix; zero them otherwise
  // so the JSON can't imply quality for a position we won't vouch for.
  if ((flags & PKT_FLAG_FIX_VALID) && gps.hdop.isValid())
    pkt.hacc_cm = (uint16_t)min(gps.hdop.hdop() * 500.0, 65535.0);  // ~5m/HDOP heuristic
  if (!(flags & PKT_FLAG_FIX_VALID)) sats = 0;
  pkt.flags_sats = flags | ((uint16_t)sats << 8);
  pkt.battery_mv = battery_mv();
  pkt_seal(&pkt);
}

// Log GNSS cadence once per real commit — the bench measurement of the
// L76K's true outdoor fix rate.
static void log_gps_cadence() {
  // isUpdated() fires once per new commit; reading lat()/lng() here clears
  // that flag exactly once. (The old millis()-age() commit key could jitter
  // by 1ms — age() calls millis() internally, then we called it again —
  // double-logging the same commit; the trigger is now unambiguous.)
  // fill_packet() gates on age(), not isUpdated(), so clearing it here is safe.
  if (!gps.location.isUpdated()) return;
  (void)gps.location.lat();
  (void)gps.location.lng();
  uint32_t now = millis();
  if (last_loc_update_ms != 0)
    Serial.printf("[gps] update dt=%lums age=%lums sats=%u\n",
                  (unsigned long)(now - last_loc_update_ms),
                  (unsigned long)gps.location.age(),
                  gps.satellites.isValid() ? gps.satellites.value() : 0);
  last_loc_update_ms = now;
}

void loop() {
  // Pump the GPS UART EVERY iteration, including throughout a background TX.
  while (Serial1.available()) gps.encode(Serial1.read());
  log_gps_cadence();

  // Reap a finished background transmit.
  if (tx_busy && tx_done) {
    tx_done = false;
    if (radio.finishTransmit() != RADIOLIB_ERR_NONE) tx_fail++;
    tx_busy = false;
  }

  // LED: heartbeat while a fresh fix is flowing, solid while GNSS is stale.
  bool fresh = gps.location.isValid() && gps.location.age() < FIX_FRESH_MS;
  digitalWrite(STATUS_LED, fresh ? (millis() / 500) % 2 : HIGH);

  if (tx_busy) return;                                   // radio still sending
  uint32_t now = millis();
  if (now - last_tx_ms < (uint32_t)BEACON_INTERVAL_MS) return;
  if (now - last_tx_ms < min_tx_interval_ms) return;     // airtime/duty guard

  fill_packet();
  int16_t st = radio.startTransmit((uint8_t *)&pkt, PKT_LEN);  // non-blocking
  // Stamp the attempt time on BOTH paths: a persistent radio fault (SPI/BUSY)
  // must not become a tight retry loop that spams serial and starves the GPS
  // UART — a failed start now waits a full beacon interval like any other.
  last_tx_ms = now;
  if (st == RADIOLIB_ERR_NONE) {
    tx_busy = true;
    seq++;            // advance only on an accepted TX (failed starts reuse seq)
  } else {
    tx_fail++;
  }

  if ((pkt.seq % 10) == 0)
    Serial.printf("[tracker] seq=%u fix=%d sats=%u age=%ums batt=%umV start=%d fail=%lu\n",
                  pkt.seq, (int)(pkt.flags_sats & PKT_FLAG_FIX_VALID),
                  pkt.flags_sats >> 8, pkt.gps_age_ms, pkt.battery_mv, st,
                  (unsigned long)tx_fail);
}
