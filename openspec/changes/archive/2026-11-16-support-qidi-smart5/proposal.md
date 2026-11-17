## Why

The project's own QIDI Smart 5 is on its way, and QIDI's Smart 5 firmware `01.01.02.01` is mirrored in `Qidi-Smart5-Defaults`. The installer refuses the Smart 5: `installer/package.yaml` `printers:` doesn't declare it, and its SOC version `01.01.02.01` is also the Plus 5's, while the installer identifies the printer by firmware version alone.

The Smart 5 shares the Plus 5's host software, toolhead board family, Box stack, and macro library, but its motion hardware is new. An i.MX RT1050 mainboard on `/dev/ttyS1` drives X, Y, and Z through QIDI's new `cuc` module and homes X and Y by motor stall. It has a single Z motor and no Z tilt, chamber heater, or polar cooler. Its bed is 180 mm, its cutter is at the front right, and its chute and wiper are behind the bed at X 100. `openspec/observations/qidi-platform.md` (QIDI Smart 5) records this evidence, from QIDI's package and the project's Smart 5.

This change adds Smart 5 `01.01.02.01` support and lists what only a running Smart 5 can settle.

## What Changes

- Identify printers by firmware key: the SOC version, plus a printer's key prefix when its versions repeat another printer's. The Smart 5's keys are `smart5-<version>`, and the firmware manifest's SOC file name (`S5_SOC_…`) picks it. The Max 4 and Plus 5 keep bare versions, so their installed state still matches. A manifest without a SOC file name no longer identifies a version two printers share.
- Admit Smart 5 `smart5-01.01.02.01` with its own stock snapshot, guarded-patch variants, `homing.py` source-patch variant, and macro folder `installer/klipper/printers/smart5/`.
- Run Z tilt only where the printer has one, and the bed screw check only where screw positions are set up.
- Add Smart 5 OrcaSlicer and QIDI Studio packs and a Smart 5 start-print contract.
- Build the THR FIFO fix from the Smart 5's own THR image `02.02.01.09`, admit its THR bootloader, and choose board manifests per printer, so the Smart 5's i.MX RT mainboard is neither read nor flashed.
- Make the target firmware a firmware key, with the Smart 5's mirror, archive, and Debian package `s5-system`; declare its kernel for zram; and add its touchscreen app to the reviewed builds.
- Keep off the Smart 5 what isn't built or checked for it: the mainboard closed-loop fix, the 565 mm/s cap, and Box motion defaults. Its homing and speed values stay QIDI's.
- Update the operator docs. The README's supported printers, the version, and the changelog follow at admission.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: printer identity by firmware key; Smart 5 admission and baseline; which host and firmware features a Smart 5 receives.
- `qidi-firmware-updates`: board images chosen per printer; boards no manifest pins are not managed.
- `firmware-artifacts`: a THR build per QIDI THR image.
- `optimized-printer-behavior`: Smart 5 motion and positions under the shared macro names; Z tilt and the bed screw check only on printers that have them.

## Impact

- Installer identity: `installer/runtime/firmware.py`, `models.py`, `manifest.py`, and the install, uninstall, restore, auto-update, and THR callers.
- `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`, a new `installer/stock/qidi-smart5-defaults/`, `installer/klipper/printers/smart5/`, and `installer/klipper/qidi/homing-smart5.py`.
- Common macros `filament.cfg` and `helpers.cfg`, and `tltg_bed_soak`.
- `orcaslicer_gcode/smart5/`, `qidistudio_gcode/smart5/`, the start-print contracts and generated views, and the slicer macro checker.
- Board images and firmware tools: `installer/runtime/board_images.py` and its callers, `firmware/smart5-thr-valve-oil-v1.json`, the THR build, check, and flash scripts, `scripts/thr_bootloaders.py`, and the Box manifest's targets.
- `installer/runtime/qidiclient_firmware_popup.py`, `qidi_package.py`, and `firmware_update.py`.
- Lifecycle, matrix, contract, THR, board, and compatibility tests.
- `docs/` and the platform observations; `README.md`, the version, and `CHANGELOG.md` at admission.
- No change to Max 4 or Plus 5 behavior.
