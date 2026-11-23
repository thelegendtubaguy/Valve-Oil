# Community-supported printers

[← Back to the README](../README.md)

Valve Oil supports two kinds of printer:

- **Officially supported printers** are printers the maintainer owns. Each release is tested on them before it ships.
- **Community-supported printers** are printers the maintainer doesn't own. Valve Oil installs on them with the same code and the same safety checks, but releases ship **without being tested on them**. Owners find a release's problems on these printers, and report them.

The README lists the two kinds in separate tables. When you install or update on a community-supported printer, the installer says so: over SSH before its questions and in its summary, and on the touchscreen beside the printer's name, with a screen of its own before a new install's questions.

Everything else is the same on both kinds of printer. The installer checks the printer's firmware and boards and refuses what this release doesn't support, makes a backup before changing anything, and rolls back when an install fails. Uninstall and `restore.sh` work the same.

## What community support means for you

- A release can reach your printer with a problem that the officially supported printers don't have. Automatic updates install releases as they come, so you may prefer to leave them off and update by hand once a release has been out for a while.
- Report problems as [issues](https://github.com/thelegendtubaguy/valve-oil/issues). Name your printer and its QIDI firmware, and attach the support bundle from Valve Oil UI's **Report a problem**. The maintainer can't reproduce a problem on a printer they don't have, so a fix may need you to try it on your printer first.
- A printer whose owners stop testing it, or whose problems can't be fixed without one, may be dropped from a later release. Later releases then refuse to install on it, as they refuse any printer they don't list, and the printer keeps the release it has.

## Adding a printer

The installer only installs on printers that a release lists, so a new printer needs a release that adds it. This is the same work that added the Plus 5 and the Smart 5, done by someone who has the printer. Open an issue first, naming the printer and its QIDI firmware, so the work can be planned with the maintainer.

### Who can't be added

A printer must meet every requirement below. These printers don't, and can't be community-supported:

| Printer | Why |
|---|---|
| QIDI Plus 4 | QIDI's firmware has Python 3.7. |

### Requirements

1. **Python 3.9.** The printer's `python3`, and the Python of QIDI's Klipper and Moonraker, must be Python 3.9, as on every printer Valve Oil supports. Valve Oil's installer, its services, and the programs it adds to Klipper and Moonraker are built for Python 3.9. Check with:

   ```bash
   python3 --version
   ~/klippy-env/bin/python --version
   ~/moonraker-env/bin/python --version
   ```

   This holds until Valve Oil changes the Python it needs, for every printer at once.

2. **QIDI's Klipper platform**, as on the printers Valve Oil supports: a `qidi` user with `~/printer_data`, QIDI's Klipper, Moonraker, and Fluidd, and a QIDI firmware that names the printer in its firmware manifest (`QD_MAX4_SOC_…`, `S5_SOC_…`).

3. **QIDI's firmware, published and mirrored.** Each QIDI firmware the printer supports must be one QIDI has published, mirrored in a `Qidi-<printer>-Defaults` repository like [Qidi-Max4-Defaults](https://github.com/thelegendtubaguy/Qidi-Max4-Defaults), with its stock configuration recorded under `installer/stock/`. Valve Oil supports the printer on those firmware versions only.

4. **The printer's own motion and positions.** Homing, cutter, wiper, and waste-chute moves, and the printer's positions and calibration defaults, go in `installer/klipper/printers/<printer>/`, under the same macro names every printer uses. Every change the installer makes to QIDI's files must be checked against that printer's stock files.

5. **Slicer start G-code** for OrcaSlicer and QIDI Studio, and the printer's G-code path contracts (print start, end, filament change, homing, and the others) passing `scripts/check_gcode_paths.py`.

6. **Board firmware recorded.** The printer's toolhead board, mainboard, and QIDI Box firmware, and their bootloaders, are read from a real printer and recorded, so the installer knows which boards it may flash and leaves the others alone. A board Valve Oil doesn't build firmware for stays on QIDI's. Serial numbers and other hardware identifiers are never committed.

7. **Every check passing**, as for any change: the installer tests, the release bundle's smoke test, and the checks in `AGENTS.md`.

8. **An owner who tests it.** Before the printer is added, its owner installs the release that adds it, then runs through updating, uninstalling, and restoring, a print start, homing, and the filament changes it has, and sends the logs. After that, they agree to report what they find in later releases.

The printer is added to `installer/package.yaml` with `support: community`, and to the README's community-supported table.
