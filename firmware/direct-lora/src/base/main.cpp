// Direct LoRa tracker — BASE node (USB on the Orin).
// SX1262 RX -> validate -> one JSON line per packet on USB serial.
// Loss/RSSI/SNR printed per packet from day one: we own the debugging
// story that Meshtastic used to provide.
// Spec: docs/superpowers/specs/2026-06-12-direct-lora-tracker.md

#include <Arduino.h>
#include <RadioLib.h>
#include <TinyGPSPlus.h>

#include "../common/board.h"
#include "../common/gps_l76k.h"
#include "../common/packet.h"
#include "../common/radio_config.h"

SX1262 radio = new Module(SX126X_CS, SX126X_DIO1, SX126X_RESET, SX126X_BUSY);

// The base is a Wio Tracker L1 too — it has its own L76K. That fix is the
// CAMERA/tripod reference position: the Orin's base_lock calibration reads it,
// and the base->remote bearing (camera position + relayed tracker position)
// is what solves the pan heading. So the base must report its own position,
// stabilized. Stationary tripod -> 1 Hz is plenty.
TinyGPSPlus base_gps;
static double base_lat_mean = 0, base_lon_mean = 0, base_alt_mean = 0;
static uint32_t base_n = 0;            // samples in the current valid run
static uint32_t base_fix_since_ms = 0; // when the current valid+good run began
static uint32_t last_base_emit_ms = 0;
static const uint32_t BASE_SETTLE_MS = 20000;  // continuous good fix before "stable"
static const uint16_t BASE_HDOP_X10_MAX = 25;  // HDOP <= 2.5 to count toward a lock

static volatile bool rx_flag = false;
static uint16_t last_seq = 0;
static uint32_t last_rx_ms = 0;   // last valid packet — drives the LED link indicator
static uint32_t last_tracker_ms = 0;
static bool have_seq = false;
static uint32_t received = 0, lost = 0, bad = 0;

static void on_rx() { rx_flag = true; }

// Re-arm RX, logging + retrying on failure (a silent startReceive() failure
// would make the base deaf with no diagnostic).
static void arm_receive(const char *where) {
  int16_t st = radio.startReceive();
  if (st != RADIOLIB_ERR_NONE) {
    Serial.printf("{\"err\":\"start_receive\",\"where\":\"%s\",\"code\":%d}\n", where, st);
    delay(50);
    radio.standby();
    radio.startReceive();
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(STATUS_LED, OUTPUT);

  radio.setRfSwitchPins(SX126X_RXEN, RADIOLIB_NC);
  int16_t st = radio.begin(RADIO_FREQ_MHZ, RADIO_BW_KHZ, RADIO_SF, RADIO_CR,
                           RADIO_SYNC_WORD, RADIO_TX_DBM, RADIO_PREAMBLE,
                           SX126X_DIO3_TCXO_VOLTAGE);
  radio.setDio2AsRfSwitch(true);
  if (st != RADIOLIB_ERR_NONE) {
    while (true) {
      Serial.printf("{\"err\":\"radio_init\",\"code\":%d}\n", st);
      delay(1000);
    }
  }
  radio.setPacketReceivedAction(on_rx);
  arm_receive("setup");

  // Base's own GPS = the camera reference position. 1 Hz (stationary tripod).
  pinMode(PIN_GPS_STANDBY, OUTPUT);
  digitalWrite(PIN_GPS_STANDBY, HIGH);
  delay(300);
  l76k_init(Serial1, Serial, GPS_BAUDRATE, 1000);

  Serial.println("{\"info\":\"base up, listening + base GPS\"}");
}

// Accumulate the base's own fix while valid + good HDOP (running mean, so the
// locked position settles); reset the window the moment the fix drops.
static void update_base_gps() {
  while (Serial1.available()) base_gps.encode(Serial1.read());
  bool valid = base_gps.location.isValid() && base_gps.location.age() < 2000;
  uint16_t hdop_x10 = base_gps.hdop.isValid()
      ? (uint16_t)min(base_gps.hdop.hdop() * 10.0, 65535.0) : 999;
  if (valid && hdop_x10 <= BASE_HDOP_X10_MAX) {
    if (base_fix_since_ms == 0) {          // start of a fresh valid run
      base_fix_since_ms = millis();
      base_n = 0;
      base_lat_mean = base_lon_mean = base_alt_mean = 0;
    }
    base_n++;
    base_lat_mean += (base_gps.location.lat() - base_lat_mean) / base_n;
    base_lon_mean += (base_gps.location.lng() - base_lon_mean) / base_n;
    double alt = base_gps.altitude.isValid() ? base_gps.altitude.meters() : 0.0;
    base_alt_mean += (alt - base_alt_mean) / base_n;
  } else {
    base_fix_since_ms = 0;                 // lost it -> restart the settle window
  }
}

// One {"base":1,...} line per second. "stable" goes 1 after a continuous
// good-fix run >= BASE_SETTLE_MS; that's the Orin's cue that base_lock can
// latch a settled camera position. lat/lon are the running mean.
static void emit_base_position() {
  bool valid = base_fix_since_ms != 0;
  bool stable = valid && (millis() - base_fix_since_ms) >= BASE_SETTLE_MS;
  uint32_t hold_s = valid ? (millis() - base_fix_since_ms) / 1000 : 0;
  uint16_t hdop_x10 = base_gps.hdop.isValid()
      ? (uint16_t)min(base_gps.hdop.hdop() * 10.0, 65535.0) : 999;
  Serial.printf(
      "{\"base\":1,\"fix\":%d,\"lat_e7\":%ld,\"lon_e7\":%ld,\"alt_m\":%d,"
      "\"sats\":%u,\"hdop_x10\":%u,\"stable\":%d,\"hold_s\":%lu}\n",
      valid ? 1 : 0, (long)(base_lat_mean * 1e7), (long)(base_lon_mean * 1e7),
      (int)base_alt_mean,
      base_gps.satellites.isValid() ? base_gps.satellites.value() : 0,
      hdop_x10, stable ? 1 : 0, (unsigned long)hold_s);
}

void loop() {
  // LED as a serial-free link indicator (USB-CDC enumeration is broken in
  // this build — see spec; the bootloader's USB works, the app's doesn't).
  // FAST blink = receiving packets within the last 1.5s (link is live);
  // SLOW blink = idle/no packets. Turn the tracker on and this flips
  // slow->fast: that IS Phase 2 passing, visible to the eye.
  uint32_t led_now = millis();
  bool linked = last_rx_ms != 0 && (led_now - last_rx_ms) < 1500;
  digitalWrite(STATUS_LED, linked ? (led_now / 100) % 2 : (led_now / 1000) % 2);

  // Base's own GPS: pump every loop, emit a {"base":1,...} line at 1 Hz.
  // Done before the rx_flag early-return so it runs even with no packets.
  update_base_gps();
  if (millis() - last_base_emit_ms >= 1000) {
    last_base_emit_ms = millis();
    emit_base_position();
  }

  if (!rx_flag) return;
  rx_flag = false;

  TrackerPacket pkt;
  int16_t st = radio.readData((uint8_t *)&pkt, PKT_LEN);
  int rssi_x10 = (int)(radio.getRSSI() * 10);
  int snr_x10 = (int)(radio.getSNR() * 10);
  arm_receive("loop");  // re-arm immediately

  if (st != RADIOLIB_ERR_NONE || !pkt_valid(&pkt)) {
    bad++;
    Serial.printf("{\"err\":\"bad_packet\",\"code\":%d,\"bad_total\":%lu}\n",
                  st, (unsigned long)bad);
    return;
  }

  // Reboot detection: tracker_ms is millis() on the tracker — monotonic within
  // a boot, resets on reboot. A backwards jump is the authoritative signal
  // (more reliable than seq alone). Reset the session counters instead of
  // counting ~65000 phantom losses from the seq wrap.
  if (have_seq && pkt.tracker_ms + 1000 < last_tracker_ms) {
    Serial.printf("{\"info\":\"tracker_reboot\",\"last_seq\":%u,\"seq\":%u}\n",
                  last_seq, pkt.seq);
    have_seq = false;
    lost = 0;
  }

  if (have_seq) {
    int16_t gap = (int16_t)(pkt.seq - (uint16_t)(last_seq + 1));
    if (gap > 0) lost += gap;          // forward gap = real loss
    // gap < 0 = duplicate / out-of-order / old packet: do NOT add 65535
  }
  last_seq = pkt.seq;
  last_tracker_ms = pkt.tracker_ms;
  have_seq = true;
  received++;
  last_rx_ms = millis();   // drives the fast-blink link indicator

  // Integer-scaled JSON: no embedded float printf, exact on the Orin, and the
  // Orin checks "fix" before trusting lat_e7/lon_e7 (no phantom 0,0 point).
  Serial.printf(
      "{\"seq\":%u,\"tracker_ms\":%lu,\"fix\":%d,"
      "\"lat_e7\":%ld,\"lon_e7\":%ld,\"gps_age_ms\":%u,"
      "\"speed_cm_s\":%u,\"course_cdeg\":%u,\"hacc_cm\":%u,"
      "\"sats\":%u,\"batt_mv\":%u,"
      "\"rssi_x10\":%d,\"snr_x10\":%d,\"rx\":%lu,\"lost\":%lu}\n",
      pkt.seq, (unsigned long)pkt.tracker_ms,
      (int)(pkt.flags_sats & PKT_FLAG_FIX_VALID),
      (long)pkt.lat_e7, (long)pkt.lon_e7, pkt.gps_age_ms,
      pkt.speed_cm_s, pkt.course_cdeg, pkt.hacc_cm,
      pkt.flags_sats >> 8, pkt.battery_mv,
      rssi_x10, snr_x10,
      (unsigned long)received, (unsigned long)lost);
}
