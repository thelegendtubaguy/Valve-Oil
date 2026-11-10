## 1. Box firmware

- [x] 1.1 Recover the FM17550 handlers, command table, response encoders, and unused handler bodies in Box firmware `02.03.01.21`.
- [x] 1.2 Write `fm17550_select` and `fm17550_transceive` in `firmware/box-rfid/` and generate `firmware/max4-box-rfid-v1.json` with `scripts/generate_box_firmware_manifest.py`.
- [x] 1.3 Extend `scripts/check_box_firmware.py` with handler emulation; pass it with both admitted bootloader captures and a Box RAM dump.
- [x] 1.4 Admit the earlier shutdown-fix image and update it from QIDI's stock image in `box-firmware.sh install`; extend the flow test.
- [x] 1.5 Flash a test Box; check select, NTAG reads, and a refused MIFARE login against real tags.

## 2. Reading tags

- [x] 2.1 Read Anycubic and Elegoo tags with the new commands and document the layouts in `openspec/observations/qidi-box/material-metadata.md`.
- [x] 2.2 Add `tltg_box_rfid`, which answers QIDI's reads through the new commands and leaves other firmware untouched.
- [x] 2.3 Decode Anycubic, Elegoo, and QIDI-layout NTAG tags into QIDI v1 blocks; unknown layouts read as no tag.
- [x] 2.4 Check that QIDI's insert read sets a slot from an Anycubic tag.

## 3. Filament database

- [x] 3.1 Generate the Anycubic and Elegoo catalog from the vendors' slicer profiles with stable `QFX` and `QFY` IDs.
- [x] 3.2 Add missing catalog filaments to every `filament-*.json` at Klipper start without changing existing entries.

## 4. Unknown tags

- [x] 4.1 Save reports for unusable tags and add `TLTG_BOX_RFID_REPORT`, without UID bytes.
- [x] 4.2 Add the spool-tag issue template.

## 5. Release

- [x] 5.1 Update `firmware/BOX_FIRMWARE.md`, `docs/firmware.md`, `docs/qidi-box.md`, `docs/settings.md`, `docs/under-the-hood.md`, and the README.
- [ ] 5.2 Bump the package version and add the `CHANGELOG.md` section.
- [ ] 5.3 Run `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
