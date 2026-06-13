/* Linker script for nRF52840 over SoftDevice S140 v7.x (app base 0x27000).
 *
 * WHY THIS EXISTS: the Wio Tracker L1 bootloader ships S140 7.3.0
 * (per INFO_UF2.TXT), whose application base is 0x27000. The PlatformIO
 * adafruit_feather_nrf52840 board def links against S140 6.1.1 (app base
 * 0x26000) — flashing that image left the app 0x1000 too low, so its vector
 * table and SoftDevice region overlapped and the board hard-faulted into a
 * boot loop on first bring-up (2026-06-13).
 *
 * This is the framework's own nrf52840_s140_v6.ld with ONLY the FLASH ORIGIN
 * moved 0x26000 -> 0x27000 (and length adjusted). RAM origin (0x20006000),
 * sections, and the nrf52_common.ld include are byte-for-byte the v6 values —
 * verified identical against the framework's nrf52833_s140_v7.ld, which uses
 * the same 0x27000 app base and 0x20006000 RAM origin. Keeping the framework's
 * section layout guarantees compatibility with the Adafruit core's startup.
 */

SEARCH_DIR(.)
GROUP(-lgcc -lc -lnosys)

MEMORY
{
  FLASH (rx)     : ORIGIN = 0x27000, LENGTH = 0xED000 - 0x27000

  RAM (rwx) :  ORIGIN = 0x20006000, LENGTH = 0x20040000 - 0x20006000
}

SECTIONS
{
  . = ALIGN(4);
  .svc_data :
  {
    PROVIDE(__start_svc_data = .);
    KEEP(*(.svc_data))
    PROVIDE(__stop_svc_data = .);
  } > RAM

  .fs_data :
  {
    PROVIDE(__start_fs_data = .);
    KEEP(*(.fs_data))
    PROVIDE(__stop_fs_data = .);
  } > RAM
} INSERT AFTER .data;

INCLUDE "nrf52_common.ld"
