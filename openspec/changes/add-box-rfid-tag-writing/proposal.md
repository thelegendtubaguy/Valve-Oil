## Why

The QIDI Box reads QIDI, Anycubic, Elegoo, and Bambu Lab spool tags, but nothing can write one. Owners with untagged spools, or spools from brands the Box can't read, have to set each slot by hand every time they load it. QIDI's firmware has a write command, but it only writes a fixed test pattern and no QIDI software calls it. The Valve Oil Box firmware already carries the reader commands a write needs (`fm17550_select` and `fm17550_transceive`); only host code and a control are missing.

## What Changes

- Write a Box slot's filament and colors to a blank or rewritable spool tag on the slot's reader, on operator request, with the spool turned by hand and no slot motion.
- Read the tag back after writing and report whether the write took.
- Refuse tags that are locked or carry another brand's data, including Bambu Lab tags (RSA-signed) and password-protected Anycubic tags, and refuse on Boxes without the Valve Oil Box firmware.
- Add a control for it in the project's Fluidd fork, alongside the Box card's **Read RFID**.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: writing a slot's filament to a spool tag.

## Impact

- `installer/klipper/tltg/box_rfid.py` and `installer/klipper/tltg-optimized-macros/box_rfid.cfg`.
- The Fluidd fork's Box card, and the pinned `system_optimizations.fluidd_build` in `installer/package.yaml`.
- `docs/qidi-box.md`, `docs/settings.md`, and `CHANGELOG.md`.
- No Box firmware change.
- RS-485 Boxes have no Klipper MCU and are not covered.
