## 1. Confirm the vendor behavior

- [x] 1.1 Run a probe package from QIDI's offline-update page on the test Max 4 (`01.01.07.01`), Plus 5 (`01.01.02.01`), and Smart 5 (`01.01.02.01`), and record in `openspec/observations/qidi-platform.md` the menu path, whether an SOC-only manifest is accepted, the user and service state during `dpkg`, and what the app does afterwards.
- [x] 1.2 Confirm that SOC version `99.99.99.99` installs on all three, and that the install script restores QIDI's update folder from the drive on all three (`probe/`).
- [x] 1.3 Decide the design's open questions and approve the wording in `screens.html`.

## 2. Installer flow

- [x] 2.1 Split the install, update, and uninstall runs into a plan step that returns the applicable questions and a run step that takes the answers and needs no input.
- [x] 2.2 Move the SSH installer to it: every question first, one summary, then an unattended run.
- [x] 2.3 Report progress as named steps with a count and elapsed time, including system changes, a QIDI firmware update, and each board firmware flash.
- [x] 2.4 Write a log for every run to `printer_data/logs/`.
- [x] 2.5 Turn Box support on without asking when a new install finds QIDI Boxes, stop asking about it on later runs, and stop asking about tool-slot mappings.
- [x] 2.6 Restart Klipper after a run that changed something instead of asking.

## 3. Touchscreen front end

- [x] 3.1 Add Montserrat and its license (`THIRD_PARTY_NOTICES.md`), and move the existing touchscreen images in `scripts/render_touchscreen_images.py` to it.
- [x] 3.2 Render the screens in `screens.html`, and draw them with `update_screen.py` on the Max 4 and, turned, on the Plus 5 and Smart 5. Read touches from the Goodix device.
- [x] 3.3 Hold the screen with `install_screen.py` from the first screen, and give it back on quit, failure, and a crash of the front end.
- [x] 3.4 Cover the returning states: update available, up to date, change settings, restore a backup.
- [x] 3.5 Cover a board that isn't admitted, unsupported firmware (including the emptied update folder), a busy printer, and a QIDI firmware update that runs first.
- [x] 3.6 Write the log to the drive, then wait on the finish screen for the drive to come out before restarting; Restart now restarts with it in.
- [x] 3.7 Show the live progress screen during SSH installs and automatic updates in place of the still images.

## 4. USB drive image

- [x] 4.1 Build the drive image in `scripts/build_installer_bundle.py`. It needs one zip per printer (`QD_MAX4_`, `QD_PLUS5_`, `S5_`), each with a `99.99.99.99` SOC manifest, the hand-off package, a printer marker, and QIDI's manifest and board images for every supported firmware with `SHA256SUMS`. It also needs a shared `valve-oil/` folder with the bundle and each printer's target QIDI package. Pin every QIDI file by hash from the mirrored archives.
- [x] 4.2 Write the hand-off `postinst`. In order, it restores QIDI's update folder for the installed system package version, copies the bundle into `~/.cache/valve-oil`, starts the installer as its own systemd unit, and exits 0.
- [x] 4.3 In the installer unit, wait for `qidi-client`'s restart and Klipper, then remove the hand-off package's dpkg record.
- [x] 4.4 Run the QIDI firmware update from the drive's package when it matches the pin, and fall back to the mirror otherwise. On the target firmware, copy QIDI's system package file back into the update folder from the drive.
- [x] 4.5 Publish the drive image with each release, and extend the bundle smoke test to cover its layout, its hashes, and the hand-off script.

## 5. Validation

- [x] 5.1 Extend the installer lifecycle tests for the plan and run steps, the SSH flow, the dropped questions, and the hand-off restore, including a firmware with no restore set.
- [ ] 5.2 On a Max 4, a Plus 5, and a Smart 5, install, update, and change settings from USB. Include a printer on older supported firmware with no network, a busy printer, a failed step, and a drive left in across the reboot.
- [ ] 5.3 Install over SSH with the new flow on one printer, and check the touchscreen progress screen during an SSH install and an automatic update.

## 6. Release

- [x] 6.1 Update `README.md`, `docs/install.md`, `docs/troubleshooting.md`, and `docs/under-the-hood.md`.
- [x] 6.2 Bump the package version and add the `CHANGELOG.md` section.
- [x] 6.3 Run `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
