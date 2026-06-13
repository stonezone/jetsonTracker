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

// Compile-time TX-rate FLOOR (10 Hz ceiling). This is NOT the airtime guard
// by itself — the real, modem-aware guard is computed at boot from
// radio.getTimeOnAir(PKT_LEN) and held to >=3x airtime, so changing SF/BW/
// payload can't silently blow the duty budget. This constant is just the
// hard "never faster than 10 Hz" backstop the runtime guard is max()'d with.
#define RADIO_MIN_TX_INTERVAL_MS 100

// Beacon rate (bring-up: 2 Hz; raise to 5 Hz only after the L76K's real
// outdoor cadence is measured — PMTK accepted != fixes delivered).
#define BEACON_INTERVAL_MS 500
