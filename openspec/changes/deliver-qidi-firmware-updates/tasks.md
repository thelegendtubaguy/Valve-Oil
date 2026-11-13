## 1. Printer checks

Run on the test Max 4 and Plus 5 before building anything that depends on them, and record each result in `openspec/observations/qidi-platform.md`.

- [x] 1.3 Install the current QIDI package by hand with `dpkg -i --force-overwrite` while the services are stopped. Record what survives (files outside `printer_data/config`, the installer folder), how long it takes, and peak memory.
- [x] 1.4 On the Max 4, run `UPGRADE_MOTOR` with the installed closed-loop image and record the status sequence and duration.
- [x] 1.7 Write a still image to `/dev/fb0` with the touchscreen app stopped on both printers, and record that it stays up and that the app repaints on start.

## 2. Block QIDI's package updates

- [x] 2.1 Add the block of design D2 to the reviewed-build patch: fail the app's extract step before it deletes `~/update`, chosen by the caller's decision, with QIDI's original preserved and restored on uninstall. Leave unreviewed touchscreen builds unchanged.
- [x] 2.2 Find the online-update notice in both reviewed touchscreen builds and extend the reviewed-build patch to hide it, keeping the per-board notices.
- [x] 2.3 Apply the block only when the THR and the mainboard are admitted, and leave existing installs with an unadmitted THR or mainboard in today's mode.

## 3. Deliver the QIDI firmware

- [x] 3.1 Give each printer in `installer/package.yaml` a target firmware, its mirror release, archive SHA-256 and size, and the versions it can be updated from; validate them in the manifest loader and `scripts/check_installer_known_versions.py`.
- [x] 3.2 Download and verify the package into the installer's cache directory, resuming a partial download.
- [x] 3.3 Run the update as a detached unit in the order of design D4, with the pending record and resume at boot.
- [x] 3.4 Stage the automatic updater's bundle outside `/tmp`.
- [x] 3.5 Update the Max 4 closed-loop drivers with QIDI's `UPGRADE_MOTOR` and image when the target package changes their version (design D13); printer check 1.4 confirms it on hardware.
- [x] 3.6 Draw the "do not power off" screen for each printer's panel while the touchscreen app is stopped.
- [x] 3.7 Make a manual `install.sh` hand off to the detached run and follow its progress, and report the result of a run the session did not see.
- [x] 3.8 Capture the operator's saved calibration (`printer.cfg`'s `SAVE_CONFIG` block, `saved_variables.cfg`) before the SOC install and put it back after it, before the board and install steps, naming what isn't carried over.

## 4. Board firmware

- [x] 4.1 Let the firmware manifests and transfer tools hold images per target firmware.
- [x] 4.2 Flash the THR and the Max 4 mainboard from installs and automatic updates without a prompt, and remove the THR question and the mainboard script's exclusivity. Keep `--flash-thr-fifo` accepted.
- [x] 4.3 Admit the Plus 5 mainboard for QIDI's stock image.
- [x] 4.4 Raise the input shaper warning after a THR flash until the operator recalibrates.
- [x] 4.5 Record a hold when `box-firmware.sh restore` runs, cleared by the next release.

## 5. Admission

- [x] 5.1 Snapshot the THR, mainboard, and every USB Box read-only in preflight, and refuse a new install when one is not admitted.
- [x] 5.2 Build the prefilled printer-support issue link from the refusal, including the printer details the form asks for, and add any missing field IDs to the form.
- [x] 5.3 Publish a printer warning with that link for an unadmitted board on an existing install.

## 6. Update check and pending work

- [x] 6.1 Install the check's timer and service with every install and remove them only on uninstall; make enrollment the only switch.
- [x] 6.2 In the check, find USB Boxes that are not on their pinned firmware from Moonraker status, and flash them on enrolled printers.
- [x] 6.3 Write the pending-work status file and have `tltg_printer_warnings` read it while Klipper runs.
- [x] 6.4 Add the status and settings service beside the add-ons service, with its nginx route, the automatic-update switch, and "update now".

## 7. Fluidd and Valve Oil UI

- [x] 7.1 Add the Valve Oil section to the Fluidd fork, cut a fork release, and pin it in `installer/package.yaml`.
- [x] 7.2 Add the same section to `ui/` and the fake Moonraker.

## 8. Tests, docs, and release

- [x] 8.1 Extend the lifecycle and Box firmware flow tests for a firmware update, a refused new install, an existing install with an unadmitted board, a Box connected between releases, and the enrollment switch. Keep `scripts/run_installer_core_tests.py` under ten seconds.
- [x] 8.2 Update `README.md`, `docs/install.md`, `docs/firmware.md`, `docs/settings.md`, `docs/troubleshooting.md`, `docs/under-the-hood.md`, `docs/valve-oil-ui.md`, and `firmware/*.md`.
- [x] 8.3 Bump the package version with `scripts/bump_installer_version.py` and write the `CHANGELOG.md` section.
- [x] 8.4 Run `openspec validate --all --strict` and the validation commands for every area touched.

## 9. Hardware pass

- [x] 9.4 Uninstall and confirm QIDI's touchscreen app is back as QIDI shipped it, which ends the block.
