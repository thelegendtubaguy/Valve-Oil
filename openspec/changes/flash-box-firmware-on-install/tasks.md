## 1. Firmware

- [x] 1.1 Regenerate the Box manifest as `max4-box-valve-oil-v1` with version `02.03.01.21-valve-oil.1`, admitting `02.03.01.21-tltg-rfid1` as an earlier build; pass `scripts/check_box_firmware.py` and the generator's `--check`.

## 2. Installer

- [x] 2.1 Flash every USB Box that needs it at the end of an install, without the `FLASH` prompt, skipping automatic-update child installs and dry runs.
- [x] 2.2 Leave the install successful when a Box can't be flashed before any write; stop with the rescue instructions after an unverified flash.
- [x] 2.3 Extend the Box firmware flow test.

## 3. Release

- [x] 3.1 Update the operator docs and the 26.11.10.1 changelog section.
- [ ] 3.2 Flash a test Box through the installer on the printer.
- [ ] 3.3 Run `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
