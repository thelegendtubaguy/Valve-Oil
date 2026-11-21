## 1. Before a stock Plus 4 is available

- [x] 1.1 Record the `2.1.1` SOC payload and one owner's runtime report in the Plus 4 observations.
- [x] 1.2 Add `installer/stock/qidi-plus4-defaults/firmwares/plus4-2.1.1/` from the payload's `printer_data/config`, and check it against the payload hashes. The factory files not in the payload follow in 2.2.
- [x] 1.3 Add the Python 3.7 syntax check for `installer/klipper/` (D6) and list it under **Validation** in `AGENTS.md`.
- [x] 1.4 Classify every common macro file by the QIDI macros and Klipper objects it uses: printer-neutral, needs a Plus 4 version, or not for the Plus 4. Classify every guarded patch, source patch, extra module, and `system_optimizations` entry the same way.
- [ ] 1.5 Load each extra module against QIDI's Plus 4 Klipper (the payload's files over `QIDITECH/klipper` `PLUS4`) under Python 3.7, and record which ones import and register. The static import check is done and recorded in `inventory.md`; loading under 3.7 waits for a Python 3.7 environment or the printer (2.4).
- [ ] 1.6 Compare the Plus 4's `box_extras.so`, `box_stepper.so`, and `multi_color_controller.so` with the Plus 5 builds in the vendor harness, covering every method a package Box module wraps or calls. Record the result in the QIDI Box observations. A strings check is done: the Plus 4 builds name 24 of the 25 QIDI-specific lookups checked, all but `_pause_print_retry_flow` (optional).
- [ ] 1.7 Draft `installer/klipper/printers/plus4/` from QIDI's `2.1.1` macros, with stock coordinates, currents, speeds, and dwells, and only the improvements D8 lists.
- [ ] 1.8 Add printer `user` and `home` to the manifest and derive the runtime paths from the detected printer (D4). Add Plus 4 detection and version reading (D3).
- [ ] 1.9 Add the Plus 4 interpreter step to the launchers (D5), pinned by SHA-256, and the uninstall step that removes it.
- [ ] 1.10 Add the per-printer declaration to `system_optimizations` (D7), with the existing three printers declaring what they get today.
- [ ] 1.11 Add a start-print contract for the Plus 4 and draft `orcaslicer_gcode/plus4/` and `qidistudio_gcode/plus4/` from QIDI's Plus 4 profiles.
- [ ] 1.12 Extend the lifecycle and patch matrix tests to the Plus 4 snapshot, with the fixture under `/home/mks` and a failure on any write under `/home/qidi`.

## 2. On a stock Plus 4, read-only

- [ ] 2.1 Write a read-only collection script for Plus 4 owners and run it on a stock printer: the data table in `design.md`, plus a model identifier readable without `sudo`. The script is `scripts/collect_plus4.py`; it runs on the printer's Python 3.7, redacts serial numbers, and reads no Wi-Fi or account files. It has run on one Beacon-converted `2.1.1` printer (recorded in the observations); a stock printer is still needed, and so is a model identifier `mks` can read (D3).
- [ ] 2.2 Copy the factory `plr.cfg`, `time_update.cfg`, `timelapse.cfg`, `KAMP_Settings.cfg`, and `KAMP/` into the snapshot, with SHA-256. The modified printer's copies don't match QIDI's published history, so they wait for a stock printer.
- [ ] 2.3 Compare the full `~/klipper` and `~/moonraker` trees with the payload and with `QIDITECH/klipper` `PLUS4`.
- [ ] 2.4 Run the downloaded interpreter on the printer: `bootstrap.py --version` and a dry run.
- [ ] 2.5 Time stock `G28` repeatedly and keep the `klippy.log` lines.
- [ ] 2.6 Watch the stock tool change, `MOVE_TO_TRASH`, `CLEAR_NOZZLE`, and park moves for clearances.
- [ ] 2.7 Run `PROBE_ACCURACY`, and trace a Box load and unload with `scripts/box_trace/`.
- [ ] 2.8 Measure idle and printing main-thread load and memory.

## 3. Admission and validation

- [ ] 3.1 Add the `plus4` printer to `installer/package.yaml` with its variants, macro folder, snapshot, user, and home. Update `installer/supported_upgrade_sources.yaml`.
- [ ] 3.2 Run the installer core tests, the known-versions check, the config formatter, the slicer macro check, the start-print contract check, the Python 3.7 check, OpenSpec validation, and the bundle smoke test.
- [ ] 3.3 Publish a `prerelease` build for Plus 4 owners. Record their dry runs, installs, prints, and uninstalls in the observations before a release lists the Plus 4.
- [ ] 3.4 Update the README supported printers, `docs/`, the version, and `CHANGELOG.md`.
