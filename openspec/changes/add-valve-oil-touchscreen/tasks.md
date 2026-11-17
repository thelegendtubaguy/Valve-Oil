## 1. Find out what QIDI's app does

- [ ] 1.1 Inventory what `qidiclient` does besides drawing, for each supported firmware, and record it in `openspec/observations/qidi-platform.md`.
- [ ] 1.2 Test whether `qidiclient` keeps its duties while it doesn't own the screen.
- [ ] 1.3 Decide each duty: take over, leave to a screenless `qidiclient`, or drop and document. Decide the open questions in `design.md`.

## 2. Measure the drawing

- [ ] 2.1 Build Home and Printing on the USB installer's front end, and measure CPU, memory, and touch response during a print on a Max 4 and a Plus 5.
- [ ] 2.2 Choose that front end or LVGL from the measurements.

## 3. Shell

- [ ] 3.1 Add the app's service with its CPU and memory limits, and what the screen shows when it can't start.
- [ ] 3.2 Put the Valve Oil app on the screen in place of QIDI's on install, keep it there across reboots, updates, and QIDI firmware updates, and return the screen to QIDI's app on uninstall.
- [ ] 3.3 Take over the duties decided in 1.3, starting with saving `box_count` when the Boxes change.

## 4. Screens

- [ ] 4.1 Home, Files, the pre-print screen with bed mesh, timelapse, and polar cooler choices, the print start stages, Printing, Tune, paused with continue from another slot, and finished.
- [ ] 4.2 Box slots, slot setup, load, unload, and drying; the material and brand lists in Valve Oil UI.
- [ ] 4.3 Move with per-axis homing and the Z travel buttons, temperatures, and fans.
- [ ] 4.4 The Klipper-stopped screen and Settings, including the calibration tools.
- [ ] 4.5 Sleep with the bouncing logo, and waking on touch.

## 5. Validation

- [ ] 5.1 Print from the touchscreen on both printers, including pause, runout, cancel, and a Klipper stop.
- [ ] 5.2 Connect and remove a Box with the Valve Oil app on the screen.
- [ ] 5.3 Reboot, update Valve Oil, and apply a QIDI firmware update with the Valve Oil app installed; uninstall and confirm QIDI's app returns.

## 6. Release

- [ ] 6.1 Add the touchscreen docs page; update `docs/settings.md` and `docs/under-the-hood.md`.
- [ ] 6.2 Bump the package version and add the `CHANGELOG.md` section.
- [ ] 6.3 Run `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
