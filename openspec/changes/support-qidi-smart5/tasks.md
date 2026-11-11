## 1. Before the printer arrives

- [x] 1.1 Record QIDI's Smart 5 `01.01.02.01` package, Klipper tree, stock configuration, and slicer profiles in the Smart 5 observations.
- [x] 1.2 Add `installer/stock/qidi-smart5-defaults/firmwares/01.01.02.01/` from `Qidi-Smart5-Defaults` `c8e9da16` and check it against the payload hashes.
- [x] 1.3 Identify printers by firmware key, with the Smart 5's `smart5-` prefix chosen from the SOC file name; keep Max 4 and Plus 5 keys bare.
- [x] 1.4 Classify every guarded patch, source patch, and extra module for the Smart 5, and draft the `smart5-01.01.02.01` variants and the `homing.py` dwell variant.
- [x] 1.5 Draft `installer/klipper/printers/smart5/` (`homing.cfg`, `nozzle_motion.cfg`, `printer_profile.cfg`) from QIDI's Smart 5 macros.
- [x] 1.6 Run Z tilt only where `z_tilt` exists, and the bed screw check only where screw positions are set up.
- [x] 1.7 Draft the Smart 5 OrcaSlicer and QIDI Studio packs and the Smart 5 start-print contract.
- [x] 1.8 Extend the lifecycle, matrix, contract, and THR tests to the Smart 5 snapshot.
- [ ] 1.9 Compare the Smart 5 and Plus 5 `box_extras.so`, `box_stepper.so`, and `multi_color_controller.so` in the vendor harness for every method a package Box module wraps or calls.

## 2. On the Smart 5, read-only

- [ ] 2.1 Run the collection script from issue 190 on the stock printer, including `MCU_ID.cfg`, Klipper objects, and MCU versions.
- [ ] 2.2 Capture the boot image and `/proc/kallsyms`, and compare the kernel with the builds the zram modules target.
- [ ] 2.3 Capture `/dev/fb0` on a known screen and read `event0`'s axis ranges; record the rotation and touch frame.
- [ ] 2.4 Diff the live `printer.cfg` against stock `01.01.02.01`.
- [ ] 2.5 Time stock `G28` repeatedly and keep the `klippy.log` lines; check repeatability with and without QIDI's first single touch.
- [ ] 2.6 Watch stock cut, wipe, chute, scrape, and park moves cold at Z 70 for clearances, and check whether the extruder sits above the cutter.
- [ ] 2.7 Run `WEIGHTING_DEBUG_QUERY` and `PROBE_ACCURACY`.
- [ ] 2.8 Measure idle and printing host load and memory.
- [ ] 2.9 Photograph the mainboard, THR, and SoC boards and the bed screws.

## 3. On the Smart 5, with services stopped

- [ ] 3.1 Snapshot the THR bootloader and classify it.
- [ ] 3.2 With a Box attached, snapshot its bootloader, trace a stock load, unload, and tool change with `scripts/box_trace/`, and decide Smart 5 Box motion defaults.

## 4. Admission and validation

- [x] 4.1 Add the `smart5` printer to `installer/package.yaml` with its variants, macro folder, and stock snapshot. Update `installer/supported_upgrade_sources.yaml`.
- [x] 4.2 Run the installer core tests, the known-versions check, the config formatter, the slicer macro check, the start-print contract check, and OpenSpec validation.
- [x] 4.3 Run the bundle smoke test.
- [ ] 4.4 Install on the Smart 5. Confirm admission, guarded patches, full, XY, and single-axis homing, print start through each slicer pack, cut, wipe, chute, rear-bed scrape, end print, and the screen card.
- [ ] 4.5 Exercise update, uninstall, restore, and recovery after a QIDI firmware update on the Smart 5.
- [ ] 4.6 Decide zram, a `02.02.01.09` THR fix, and the cold cut for the Smart 5 from 2.2, 2.6, and 3.1, and record the decisions.
- [x] 4.7 Update `docs/`.
- [ ] 4.8 At admission, add the Smart 5 to the README's supported printers, advance the version, and add the `CHANGELOG.md` entry.
