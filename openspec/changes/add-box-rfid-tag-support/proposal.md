## Why

The QIDI Box reads only QIDI's own spool tags. Owners want Anycubic, Elegoo, and Bambu Lab spools recognized, and those brands' filaments in QIDI's filament database. The Box's FM17550 reader already sees Anycubic tags (NFC Type 2, ATQA `0x0044`, 7-byte UID, SAK `0x00`), but QIDI's firmware tries a MIFARE Classic login on every tag and reads nothing when it fails. Bambu Lab tags are MIFARE Classic with per-tag keys, which QIDI's fixed-key login cannot open. QIDI's firmware has a write command, but it only writes a fixed test pattern, and no QIDI host software calls it. `openspec/observations/qidi-box/material-metadata.md` and `mcu-firmware.md` record the recovered reader behavior and tag formats.

## What Changes

- Replace the USB Box firmware fix with a cumulative build, `max4-box-valve-oil-v1`. It keeps the shutdown fix and adds two reader commands in place of four QIDI FM17550 debug commands that no QIDI software uses:
  - `fm17550_select` wakes a tag (including a halted one), selects it through every cascade level, and returns its ATQA, UID, and SAK.
  - `fm17550_transceive` sends one frame, or a MIFARE login, and returns the reply.
  QIDI's own read command is unchanged.
- Let `box-firmware.sh install` update a Box that runs the earlier shutdown fix, building the new image from QIDI's stock image.
- Add a Klipper module that serves QIDI's RFID reads through the new commands. It reads QIDI tags, Anycubic tags, Elegoo tags, Bambu Lab tags (read only, with the keys derived from each tag's UID), and QIDI-format data on NTAG tags, and hands QIDI's parser a QIDI-format block, so QIDI's slot assignment, touchscreen, and Fluidd behavior are unchanged. Without the new firmware, QIDI's reads pass through untouched.
- Add every Anycubic, Elegoo, and Bambu Lab filament from the vendors' slicer profiles to QIDI's filament database at Klipper start, without changing existing entries.
- Save a report of any tag the Box reads but cannot use, and add `TLTG_BOX_RFID_REPORT` and a spool-tag issue template so owners can send reports for new brands.
- Tag writing, and keeping operator edits across QIDI firmware updates, are left for a later change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `firmware-artifacts`: the Box image adds the RFID commands to the shutdown fix.
- `box-firmware-transfer`: a Box on an earlier build of this project is admitted and updated from QIDI's stock image.
- `optimized-printer-behavior`: reading Anycubic, Elegoo, and Bambu Lab tags, their filaments in QIDI's database, and tag reports.
- `installer-lifecycle`: the optimized configuration may add catalog filaments to QIDI's database files.

## Impact

- `firmware/box-rfid/`, `firmware/max4-box-valve-oil-v1.json` (replaces `max4-box-shutdown-fix-v1.json`), `scripts/generate_box_firmware_manifest.py`, `scripts/build_box_firmware.py`, `scripts/check_box_firmware.py`, `scripts/flash_box_firmware.py`, and `installer/runtime/box_firmware.py`.
- `installer/klipper/tltg/box_rfid.py`, the generated `installer/klipper/tltg/filament_catalog.py` (`scripts/generate_filament_catalog.py`), and `installer/klipper/tltg-optimized-macros/box_rfid.cfg`.
- QIDI's `filament-*.json` files gain entries, which changes the current rule that nothing modifies them.
- `installer/package.yaml`, `docs/`, `.github/ISSUE_TEMPLATE/spool-tag.yml`, and `CHANGELOG.md`.
- RS-485 Boxes have no Klipper MCU and are not covered.
