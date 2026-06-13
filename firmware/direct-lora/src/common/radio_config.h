// Radio constants — REGULATORY-SIGNIFICANT. US915 defaults.
// Region/frequency/power/duty are compile-time on purpose: a runtime
// misconfiguration must not be able to put the radio outside the band.
// Spec: docs/superpowers/specs/2026-06-12-direct-lora-tracker.md
#pragma once

// US 902-928 MHz ISM. Single fixed channel mid-band, clear of the
// Meshtastic US default hopping set's busiest edges.
#define RADIO_FREQ_MHZ 914.875f
#define RADIO_BW_KHZ 250.0f
#define RADIO_SF 7
#define RADIO_CR 5           // 4/5
#define RADIO_SYNC_WORD 0x57 // private network, not Meshtastic's 0x2B
#define RADIO_TX_DBM 17      // headroom below SX1262 +22 max; raise after field test
#define RADIO_PREAMBLE 8

// Airtime guard: a 32-byte SF7/BW250 frame is ~35 ms. Refuse to schedule
// transmissions closer than this regardless of configured rate — a config
// error must degrade to a slower beacon, never flood the band.
#define RADIO_MIN_TX_INTERVAL_MS 100  // hard ceiling 10 Hz

// Beacon rate (bring-up: 2 Hz; raise to 5 Hz only after the L76K's real
// outdoor cadence is measured — PMTK accepted != fixes delivered).
#define BEACON_INTERVAL_MS 500
