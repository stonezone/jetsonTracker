// Direct LoRa tracker — BASE node (USB on the Orin).
// SX1262 RX -> validate -> one JSON line per packet on USB serial.
// Loss/RSSI/SNR printed per packet from day one: we own the debugging
// story that Meshtastic used to provide.
// Spec: docs/superpowers/specs/2026-06-12-direct-lora-tracker.md

#include <Arduino.h>
#include <RadioLib.h>

#include "../common/packet.h"
#include "../common/radio_config.h"

SX1262 radio = new Module(D4, D1, D2, D3);

static volatile bool rx_flag = false;
static uint16_t last_seq = 0;
static bool have_seq = false;
static uint32_t received = 0, lost = 0, bad = 0;

static void on_rx() { rx_flag = true; }

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED1, OUTPUT);
  pinMode(PIN_LED2, OUTPUT);

  radio.setRfSwitchPins(D5, RADIOLIB_NC);
  int16_t st = radio.begin(RADIO_FREQ_MHZ, RADIO_BW_KHZ, RADIO_SF, RADIO_CR,
                           RADIO_SYNC_WORD, RADIO_TX_DBM, RADIO_PREAMBLE);
  radio.setDio2AsRfSwitch(true);
  if (st != RADIOLIB_ERR_NONE) {
    while (true) {
      Serial.printf("{\"err\":\"radio_init\",\"code\":%d}\n", st);
      delay(1000);
    }
  }
  radio.setPacketReceivedAction(on_rx);
  radio.startReceive();
  Serial.println("{\"info\":\"base up, listening\"}");
}

void loop() {
  digitalWrite(PIN_LED1, (millis() / 1000) % 2);  // slow heartbeat
  if (!rx_flag) return;
  rx_flag = false;

  TrackerPacket pkt;
  int16_t st = radio.readData((uint8_t *)&pkt, PKT_LEN);
  float rssi = radio.getRSSI();
  float snr = radio.getSNR();
  radio.startReceive();  // re-arm immediately

  if (st != RADIOLIB_ERR_NONE || !pkt_valid(&pkt)) {
    bad++;
    Serial.printf("{\"err\":\"bad_packet\",\"code\":%d,\"bad_total\":%lu}\n",
                  st, (unsigned long)bad);
    return;
  }

  if (have_seq) {
    uint16_t expected = (uint16_t)(last_seq + 1);
    if (pkt.seq != expected)
      lost += (uint16_t)(pkt.seq - expected);  // u16 wrap-safe gap
  }
  last_seq = pkt.seq;
  have_seq = true;
  received++;

  digitalWrite(PIN_LED2, HIGH);
  // One self-describing JSON line per packet — the Orin reader's contract.
  Serial.printf(
      "{\"seq\":%u,\"tracker_ms\":%lu,\"lat\":%.7f,\"lon\":%.7f,"
      "\"speed_mps\":%.2f,\"course_deg\":%.2f,\"hacc_m\":%.2f,"
      "\"fix\":%d,\"sats\":%u,\"batt_mv\":%u,"
      "\"rssi\":%.1f,\"snr\":%.1f,\"rx\":%lu,\"lost\":%lu}\n",
      pkt.seq, (unsigned long)pkt.tracker_ms,
      pkt.lat_e7 / 1e7, pkt.lon_e7 / 1e7,
      pkt.speed_cm_s / 100.0, pkt.course_cdeg / 100.0, pkt.hacc_cm / 100.0,
      (int)(pkt.flags_sats & PKT_FLAG_FIX_VALID), pkt.flags_sats >> 8,
      pkt.battery_mv, rssi, snr,
      (unsigned long)received, (unsigned long)lost);
  digitalWrite(PIN_LED2, LOW);
}
