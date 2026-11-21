## Why

QIDI Plus 4 owners have asked for Valve Oil, and the project has no Plus 4. An owner with a Plus 4 on QIDI's current `2.1.1` firmware has offered the evidence and the testing. The installer refuses the Plus 4 because `installer/package.yaml` `printers:` lists only the Max 4, Plus 5, and Smart 5, and it would not get that far: the Plus 4's `python3` is 3.7.3 and the installer needs 3.9.

The Plus 4 is not a variant of the Max 4 the way the Plus 5 and Smart 5 are. `openspec/observations/qidi-platform.md` (QIDI Plus 4) records the `2.1.1` SOC payload and one owner's runtime report:

- **Host:** Debian 10 with Python 3.7.3 and glibc 2.28 on `aarch64`, an MKS image with the `mks` user in `/home/mks`, and kernel modules for `5.16.20-rockchip64`.
- **No firmware manifest:** the version is in `/root/xindi/version`, which `mks` can't read without `sudo`.
- **No `klipper-macros-qd/`:** one `gcode_macro.cfg` holds homing, print start and end, pause, cancel, and the probe macros, and `box1.cfg` the tool changes. The common optimized macros override macro names that don't exist on a Plus 4.
- **Other hardware:** a `smart_effector` probe with dual Z and `[z_tilt]`, a serial touchscreen driven by `xindi`, and compiled Box modules built for Python 3.7 with Box MCU firmware `1.1.1`.

This change lists what is known, what must be read from a stock Plus 4, and the work to admit Plus 4 firmware `2.1.1` once that evidence is in.

## What Changes

- Admit Plus 4 firmware `2.1.1` as a fourth printer, `plus4`, detected without a firmware manifest. It gets its own stock snapshot, its own paths and user (`/home/mks`, `mks`), and firmware-scoped guarded-patch variants.
- Run the installer on a Plus 4 under a standalone CPython 3.11 that the launcher downloads and checks by SHA-256, only on that printer. The installer code is unchanged and still targets Python 3.9.
- Keep the package's Klipper extras loadable under Python 3.7, the Plus 4's `klippy-env`, and check that statically.
- Split the optimized macro tree into the printer-neutral files every printer gets and the files that override QIDI's `klipper-macros-qd` names. Add `installer/klipper/printers/plus4/` with Plus 4 versions of print start and end, homing, cancel, filament, and Box moves under QIDI's Plus 4 macro names.
- Keep everything tied to Max 4 or Plus 5 hardware or images off on the Plus 4: board firmware (Box, THR, mainboard, closed loop), the Printer screen card and touchscreen patches, zram modules and Rockchip service changes, load-cell tools, the step-loss monitor, and RS-485 Boxes.
- Add Plus 4 OrcaSlicer and QIDI Studio packs, and update the README's supported printers, the operator docs, and the changelog.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: Plus 4 admission, detection, paths, and runtime; which host and firmware features a Plus 4 receives.
- `optimized-printer-behavior`: Plus 4 print start and end, homing, and Box moves under QIDI's Plus 4 macro names.

## Impact

- `installer/package.yaml` (`printers:`, patch variants), a new `installer/stock/qidi-plus4-defaults/`, a new `installer/klipper/printers/plus4/`, and `installer/supported_upgrade_sources.yaml`.
- Printer detection and runtime paths in `installer/runtime/` (`cli.py`, `manifest.py`, firmware detection), and the release launchers in `installer/release/`.
- The start-print contract and generated views, `orcaslicer_gcode/plus4/`, and `qidistudio_gcode/plus4/`.
- Installer lifecycle and matrix tests, the bundle smoke test, and a Python 3.7 syntax check for `installer/klipper/extras/`.
- `README.md`, `docs/`, `CHANGELOG.md`, and the Plus 4 observations.
- No change to Max 4, Plus 5, or Smart 5 behavior.
