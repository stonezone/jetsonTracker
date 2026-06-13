// Direct LoRa tracker — BASE node (USB on the Orin).
// SX1262 RX -> validate -> one JSON line per packet on USB serial.
// Loss/RSSI/SNR printed per packet from day one: we own the debugging
// story that Meshtastic used to provide.
// Spec: docs/superpowers/specs/2026-06-12-direct-lora-tracker.md

#include <Arduino.h>
#include <RadioLib.h>

#include "../common/board.h"
#include "../common/packet.h"
#include "../common/radio_config.h"

SX1262 radio = new Module(SX126X_CS, SX126X_DIO1, SX126X_RESET, SX126X_BUSY);

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
  Serial.println("{\"info\":\"base up, listening\"}");
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
