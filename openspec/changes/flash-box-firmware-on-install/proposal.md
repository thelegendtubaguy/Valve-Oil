## Why

Anycubic and Elegoo spool tags read only on a USB Box running the Box firmware from `add-box-rfid-tag-support`. As long as `box-firmware.sh` is the only way to get it, most owners will never run it, and their Boxes keep QIDI's firmware with its shutdown restarts and no tag support. The firmware's version also still carries the old `tltg` name.

## What Changes

- Every install and update, including `--yes`, flashes the bundled Box firmware to each configured USB Box that needs it after the rest of the install, without a `FLASH` prompt. Someone is at the printer because they started the installer.
- Automatic updates never flash a Box, because nobody may be there to run `rescue` if a flash fails.
- A Box the installer can't flash safely or doesn't recognize is left unchanged, and the install still succeeds and says why. A flash that can't be verified stops the install with the rescue instructions, as `box-firmware.sh install` does.
- No opt-out. `box-firmware.sh restore` still puts QIDI's firmware back, and the next install flashes the Valve Oil firmware again.
- The Box firmware reports `02.03.01.21-valve-oil.<revision>`, which is QIDI's version, the project name, and a revision that goes up with each new Box build. The first is `02.03.01.21-valve-oil.1` (`firmware/max4-box-valve-oil-v1.json`, patch `max4-box-valve-oil-v1`). The development build `02.03.01.21-tltg-rfid1` is admitted as an earlier build and replaced.

## Capabilities

### Modified Capabilities
- `installer-lifecycle`: installs flash USB Boxes; automatic updates don't.
- `box-firmware-transfer`: the installer's install path reaches the Box transfer tool.

## Impact

- `installer/runtime/box_firmware.py`, `installer/runtime/cli.py`, `scripts/generate_box_firmware_manifest.py`, `firmware/max4-box-valve-oil-v1.json`, `installer/tests/integration/test_box_firmware_flow.py`.
- Docs: `README.md`, `docs/firmware.md`, `docs/install.md`, `docs/under-the-hood.md`, `docs/qidi-box.md`, `firmware/BOX_FIRMWARE.md`, `CHANGELOG.md` (26.11.10.1).
- The firmware-artifacts delta in `add-box-rfid-tag-support` now names version `02.03.01.21-valve-oil.1`.
