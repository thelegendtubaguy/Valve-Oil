## Why

QIDI's touchscreen app is closed, and this project can only work around it: two patched instructions to stop a false firmware notice, a restart button in Fluidd for when it freezes, and a reminder to switch off AI toggles that no longer match the service behind them. It shows `Printer service failed to start` for a day while Moonraker runs normally, hides Klipper's real error behind an error code, and knows nothing about what Valve Oil adds: the faster print start's stages, Box slots matched to a file's filaments, tag reading for other brands, or the calibration tools. A touchscreen app of this project's own can show all of that, in the branding the USB installer's screens introduce.

## What Changes

- Add a Valve Oil touchscreen app for the Max 4 and Plus 5 that replaces QIDI's app on the screen. There is no switch between the two; uninstalling returns the screen to QIDI's app. `screens.html` in this change holds the concept mockups.
- It shows temperatures, loaded filament, and the last print; lists files and starts, pauses, resumes, tunes, and cancels prints; shows the print start as named stages; shows and sets QIDI Box slots; moves the toolhead and sets heaters and fans; and opens the calibration tools.
- It shows Klipper's real stop reason with one recovery action.
- What QIDI's app does for the printer besides drawing the screen keeps working, or is listed in the docs as something the Valve Oil touchscreen does not do.
- QIDI's app stays on the printer, unmodified, so uninstall can return the screen to it.

## Capabilities

### New Capabilities

- `valve-oil-touchscreen`: the touchscreen app that replaces QIDI's, its removal on uninstall, and what it shows and controls.

### Modified Capabilities

None.

## Impact

- A new touchscreen program and its service, sharing the drawing and touch code of the USB installer's touchscreen front end (change `add-usb-touchscreen-installer`).
- `installer/runtime/` and `installer/package.yaml`: installing the program in place of QIDI's app on the screen, and returning the screen to QIDI's app on uninstall.
- The Fluidd fork: the touchscreen app restart control follows whichever app owns the screen.
- `openspec/observations/qidi-platform.md`: an inventory of what `qidiclient` does besides drawing, which this change depends on.
- `docs/` (a new touchscreen page, `settings.md`, `under-the-hood.md`) and `CHANGELOG.md`.
