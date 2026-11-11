# Valve Oil UI

[← Back to the README](../README.md)

Valve Oil UI is a new web interface for the printer, built in this project. It is early and optional. It has a dashboard, the printer's files, and the QIDI Box, and more screens will follow. It sits alongside Fluidd at `http://<your printer's ip>/valve-oil/`. Fluidd stays the printer's main page and doesn't change, and both can be open at once.

## What's in it

- **Home** shows the print in progress, with its thumbnail filling in as it prints, time left and when it'll finish, pause, and cancel. Cancel needs a press and hold, so a stray tap doesn't end a print. Speed, flow, and Z offset can be adjusted from the same card. Between prints, Home shows recent files with how their last print went, the loaded filament, and print history.
- **Files** lists every G-code file with its thumbnail, print time, and filament, and takes uploads. Starting a print with a QIDI Box connected first matches each filament in the file to a loaded slot, as Fluidd's Box print dialog does.
- **QIDI Box** draws the filament path from each slot through the hub and toolhead to the nozzle, and has Load, Unload, Eject, Cut, the Box heaters, tool mapping, filament editing, Box settings, and load and unload speeds. While a load or unload runs it says what the Box is doing, and when a step fails it shows QIDI's error with **Retry** and **Stop**.
- The temperature tiles set the nozzle, bed, and chamber targets and the exhaust fan. **Emergency stop** is always at the top right.
- On a phone the screens rearrange into one column with tabs along the bottom, and everything works the same.
- Printer warnings show at the top of every screen, as in Fluidd. **Open Fluidd** goes back to Fluidd for everything Valve Oil UI doesn't have yet, such as the console, configuration files, and bed mesh.

## What the installer does

- **Installs the build in `~/tltg-valve-oil-ui`** and has the printer's web server serve it at `/valve-oil/`, next to Fluidd. It reaches Klipper through the same Moonraker connection Fluidd uses, so there's nothing to set up.
- **Updates replace the whole build.** Open Valve Oil UI tabs switch to the new version by themselves.
- **It's served only together with this project's [Fluidd](fluidd.md).** If the installer doesn't recognize QIDI's web server configuration for Fluidd, or `~/tltg-valve-oil-ui` already holds files the installer didn't put there, it leaves them alone and `/valve-oil/` isn't served.

## Undoing it

Uninstalling removes the `/valve-oil/` route and `~/tltg-valve-oil-ui`. Fluidd isn't affected.
