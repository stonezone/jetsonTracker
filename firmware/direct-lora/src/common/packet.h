// Direct LoRa tracker — wire format. One packet type, one direction.
// 32 bytes little-endian; CRC16-CCITT over bytes 0..29. Spec:
// docs/superpowers/specs/2026-06-12-direct-lora-tracker.md
#pragma once
#include <stdint.h>
#include <string.h>

#define PKT_MAGIC 0x57  // 'W'
#define PKT_VERSION 1
#define PKT_LEN 32

// flags bitfield (low byte; high byte = satellites in use)
#define PKT_FLAG_FIX_VALID 0x01   // GNSS reports a FRESH fix (age-gated, not just sticky-valid)
#define PKT_FLAG_SPEED_VALID 0x02
#define PKT_FLAG_COURSE_VALID 0x04

#define PKT_GPS_AGE_STALE 0xFFFF  // gps_age_ms sentinel: no fix / older than we'll vouch for

#pragma pack(push, 1)
typedef struct {
  uint8_t magic;        // PKT_MAGIC
  uint8_t version;      // PKT_VERSION
  uint16_t seq;         // wraps; receiver derives loss from gaps
  uint32_t tracker_ms;  // ms since tracker boot (freshness, not wall time)
  int32_t lat_e7;       // degrees * 1e7
  int32_t lon_e7;
  uint16_t speed_cm_s;
  uint16_t course_cdeg; // degrees * 100, 0..35999
  uint16_t hacc_cm;     // horizontal accuracy estimate, cm (0 = unknown)
  uint16_t flags_sats;  // low byte PKT_FLAG_*, high byte = sats in use
  uint16_t battery_mv;
  uint16_t gps_age_ms;  // ms since the GNSS last committed a fix; 0xFFFF = stale/unknown.
                        // The base/Orin judge freshness from THIS, not tracker_ms,
                        // because a beacon fires whether or not GPS updated.
  uint8_t reserved[2];  // future: heading source, temperature
  uint16_t crc;         // CRC16-CCITT (0xFFFF init) over bytes 0..29
} TrackerPacket;
#pragma pack(pop)

_Static_assert(sizeof(TrackerPacket) == PKT_LEN, "packet must be 32 bytes");

static inline uint16_t pkt_crc16(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (int b = 0; b < 8; b++)
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

static inline void pkt_seal(TrackerPacket *p) {
  p->magic = PKT_MAGIC;
  p->version = PKT_VERSION;
  p->crc = pkt_crc16((const uint8_t *)p, PKT_LEN - 2);
}

// Returns 1 if magic/version/crc all check out.
static inline int pkt_valid(const TrackerPacket *p) {
  if (p->magic != PKT_MAGIC || p->version != PKT_VERSION) return 0;
  return pkt_crc16((const uint8_t *)p, PKT_LEN - 2) == p->crc;
}
