## Why

The QIDI Box reads QIDI, Anycubic, Elegoo, and Bambu Lab spool tags, but nothing can write one. Owners with untagged spools, or spools from brands the Box can't read, have to set each slot by hand every time they load it. QIDI's firmware has a write command, but it only writes a fixed test pattern and no QIDI software calls it. The Valve Oil Box firmware already carries the reader commands a write needs (`fm17550_select` and `fm17550_transceive`); only host code and a control are missing.

## What Changes

- Write a Box slot's filament and colors to a blank or rewritable spool tag on the slot's reader, on operator request, with the spool turned by hand and no slot motion. The tag carries the filament's database ID, so any entry can be written whatever its vendor, and optionally the entry's name, so entries an operator added read the same on another printer.
- Read the tag back after writing and report whether the write took.
- Refuse tags that are locked or carry another brand's data, including Bambu Lab tags (RSA-signed) and password-protected Anycubic tags, and refuse on Boxes without the Valve Oil Box firmware.
- Add a **Write spool tag** dialog on the Valve Oil UI's QIDI Box screen and the Fluidd fork's QIDI Box card, beside **Read RFID**. The Box turns a spool on an empty slot until its tag comes past; with filament fed, the operator turns it by hand.
- Add a diagnostic command to scan a reader's tags and write them with every option.
- Read Creality, TigerTag, and OpenTag3D spool tags as well, and add Creality's filaments to QIDI's filament database with the other vendors'. OpenPrintTag uses ISO 15693 tags, which the Box's reader can't talk to.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: writing a slot's filament to a spool tag; reading Creality, TigerTag, and OpenTag3D tags; Creality filaments in QIDI's database.

## Impact

- `installer/klipper/tltg/box_rfid.py`, `installer/klipper/tltg-optimized-macros/box_rfid.cfg`, and the generated `installer/klipper/tltg/filament_catalog.py` with `scripts/generate_filament_catalog.py`.
- The Valve Oil UI's QIDI Box screen (`ui/`), and possibly the Fluidd fork's Box card with the pinned `system_optimizations.fluidd_build` in `installer/package.yaml`.
- `docs/qidi-box.md`, `docs/settings.md`, `docs/valve-oil-ui.md`, and `CHANGELOG.md`.
- No Box firmware change.
- RS-485 Boxes have no Klipper MCU and are not covered.
