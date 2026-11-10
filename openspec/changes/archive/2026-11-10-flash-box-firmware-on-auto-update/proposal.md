## Why

Installs flash the Valve Oil Box firmware to every USB Box, but automatic updates skip it, so owners who rely on automatic updates keep an older Box firmware until they run the installer by hand. A new Box firmware should reach them the same way the rest of a release does.

## What Changes

- The install an automatic update runs for a new release flashes each configured USB Box that needs the bundled Box firmware, as an interactive or `--yes` install does.
- An automatic update check that finds the release already installed never flashes a Box, so a Box put back on QIDI's firmware with `box-firmware.sh restore` stays that way until the next release.
- `box-firmware.sh restore` and `rescue` are still refused inside an automatic update's install, and automatic updates still never reach the mainboard tool.
- An automatic update skips a USB Box whose newest capture found a bootloader or application the release still doesn't admit, so each release doesn't stop Klipper to capture it again. A release that admits it flashes the Box.
- A board left unchanged because its bootloader or firmware isn't supported now says to open a support issue (the printer-support template, which gains a field for the capture's `report.json`) and attach only that summary.
- A Box left unchanged for that reason gets a warning in Fluidd's banner from `tltg_printer_warnings`, which names the report the installer copies to Klipper's log folder for download from Fluidd's Logs. It clears once the Box has the Valve Oil firmware.
- An unverified flash during an automatic update fails the update and leaves Klipper, the touchscreen, and the automatic-update timer stopped with the rescue record, as an install's does.

## Capabilities

### Modified Capabilities
- `installer-lifecycle`: automatic updates flash USB Boxes when they install a release.
- `box-firmware-transfer`: an automatic update's install reaches the Box transfer tool.

## Impact

- `installer/runtime/box_firmware.py`, `installer/runtime/mainboard_firmware.py`, `installer/klipper/tltg/printer_warnings.py`, `installer/tests/integration/test_box_firmware_flow.py`, `.github/ISSUE_TEMPLATE/printer-support.yml`.
- Docs: `docs/install.md`, `docs/firmware.md`, `docs/under-the-hood.md`, `firmware/BOX_FIRMWARE.md`, `firmware/MAINBOARD_FIRMWARE.md`, `CHANGELOG.md` (26.11.10.1).
