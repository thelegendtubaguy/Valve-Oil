## Context

QIDI's touchscreen app installs a package from a USB drive under **Firmware → Offline Update**. Each printer's app lists only zips under `QD_Update/` whose names start with its own prefix: `QD_MAX4_` (Max 4), `QD_PLUS5_` (Plus 5), `S5_` (Smart 5). For the one picked, it checks only that the file exists and ends in `.zip` or `.deb`, deletes `/home/qidi/update/`, and unzips the file there. It then reads `firmware_manifest.json` and compares the SOC version with the installed one. When they differ, it stops Moonraker and Klipper, copies the SOC file to `qidi.deb`, and runs `dpkg -i --force-overwrite` as root. Finally it starts Klipper and Moonraker and restarts itself. It verifies no signature, and components whose file is missing are skipped.

A probe package ran this way on the test Max 4 (`01.01.07.01`), Plus 5 (`01.01.02.01`), and Smart 5 (`01.01.02.01`), all on Valve Oil `26.11.15.1`. The runs established:

- The `postinst` runs as root, a child of `qidiclient`, with Klipper and Moonraker stopped.
- The install takes about 5 s.
- The app restarts itself about 17 s after `dpkg` returns.
- An SOC version of `99.99.99.99` is installed on all three.
- A `postinst` can restore QIDI's update folder from files the zip carried.
- The drive is mounted `vfat` at `printer_data/gcodes/USB`, and a 25 MiB file copies from it in about 1.2 s.

The details are under "Touchscreen firmware updates" in `openspec/observations/qidi-platform.md`. The probe's build scripts are in `probe/` beside this file, for reference. Copy them to a scratch folder to run them, since they write their output next to themselves.

Valve Oil already has most of the parts a touchscreen installer needs:

- `update_screen.py` draws to the framebuffer with no third-party module. It uses PNGs rendered at build time by `scripts/render_touchscreen_images.py` and decoded with zlib, turns them for the Plus 5's portrait panel, and turns the backlight on.
- `install_screen.py` holds `qidi-client` stopped for an install. A systemd-run watcher starts it again if the installer dies, and it stays stopped when a board needs recovery.
- `qidi_package.py` and `firmware_update.py` download QIDI's package for the target firmware from this project's mirror, verify it, and make it QIDI's update folder. `board_images.py` pins each board's image per target firmware.
- `tltg_remote_screen.py` reads `/dev/fb0` and writes touch events, so the touch device's format is known.

The installer detects the firmware from `/home/qidi/update/firmware_manifest.json`, using the SOC version and the SOC file name's printer prefix. Today it asks its questions at different points in the run:

- automatic updates;
- AI detection;
- enabling a QIDI Box;
- the tool-slot mapping, when it doesn't match;
- the reboot after host tuning;
- on uninstall, restoring system optimizations.

Board firmware is flashed on every install without a question. A new install is refused before any change when a board isn't admitted.

## Goals / Non-Goals

**Goals:**

- Install, update, change settings, and restore a backup from the touchscreen with a USB drive, with no computer or network after the download.
- One drive image for every supported printer.
- Ask everything before changing anything, over SSH too.
- Show real steps and elapsed time, and end on a screen that says what to do next.
- Leave QIDI's update folder as it was.

**Non-Goals:**

- Uninstalling from the drive.
- Replacing QIDI's touchscreen app outside the installer run.
- Changing what an install does to the printer, apart from the questions dropped below.
- Wi-Fi setup from the installer.
- Installing on firmware the package doesn't support.

## Decisions

### One drive image for every printer

Each release publishes one zip that the owner extracts to the root of a FAT32 drive:

```
QD_Update/QD_MAX4_ValveOil_<version>.zip    hand-off package + QIDI restore sets for the Max 4
QD_Update/QD_PLUS5_ValveOil_<version>.zip   the same for the Plus 5
QD_Update/S5_ValveOil_<version>.zip         the same for the Smart 5
valve-oil/                                  the Valve Oil bundle, and each printer's target QIDI package
```

Each printer's app lists only its own zip, so no one can pick another printer's. Each zip holds:

- a `firmware_manifest.json` whose only entry is `SOC`, with version `99.99.99.99`, naming the hand-off package;
- the hand-off package;
- `valve-oil-restore/` with QIDI's manifest and board images, not the system package, for each firmware the release supports on that printer.

Each set carries a `SHA256SUMS` file. The bundle and QIDI's packages are stored once, in `valve-oil/`, which the hand-off reads from the mounted drive. `QD_Mandatory_Update.zip`, which the app starts with no tap, is never used.

The SOC version must always count as an update, because the app wipes and unzips before it compares versions. A skipped install would leave QIDI's update folder empty with none of our code run, as one probe run without a restore did on the Smart 5. `99.99.99.99` is higher than any QIDI version and was installed on all three printers.

### The hand-off package only restores and hands off

The hand-off is a Debian package with its own name. It installs no files and has `Architecture: all`. Its `postinst` runs under the app's `dpkg`, which holds the dpkg lock, and the app restarts Klipper, Moonraker, and itself right after it. So the `postinst` does no install work. In order, it:

1. **Restores QIDI's update folder.** It reads its printer from the zip's marker file and the installed QIDI version with `dpkg-query` (`qd-max4-system`, `qd-plus5-system`, `s5-system`). It picks the matching set (`<version>`, or `<version>_<date>` where QIDI released two packages under one version) and checks the set against `SHA256SUMS`. It removes everything the update extracted except `qidi.deb`, which the app removes itself. It copies the set in, mode `0644` and owned by root, and checks the result against `SHA256SUMS` again.
2. **Copies the bundle archive** from the drive's `valve-oil/` folder into `~/.cache/valve-oil`, where Valve Oil keeps its working files, owned by the printer's user. The installer unpacks it as `install-latest.sh` does, to `~/valve-oil`.
3. **Starts the installer** as its own transient systemd unit, so the app's restart doesn't kill it, and exits 0.

The installer unit waits until `qidi-client` has restarted and Klipper is ready. It removes the hand-off package's dpkg record, then takes the screen with `install_screen.py`'s hold and watcher. The hold starts at the first screen, not at the first board read. The unit starts `qidi-client` again when the owner quits, and the printer reboots after an install as it does today.

When no set matches, the `postinst` restores nothing and still starts the installer. The installer shows the unsupported-firmware screen, which says the update folder was emptied and how to get QIDI's files back from QIDI's firmware download. The Max 4 has two `01.01.06.04` packages that differ only in the mainboard image. The later one is restored, and the firmware update that `01.01.06.04` always gets replaces the folder anyway.

### QIDI's packages on the drive

`valve-oil/` carries each printer's target QIDI package, as named in `installer/package.yaml`. That is about 600 MB at the current targets: Max 4 265 MB, Plus 5 178 MB, Smart 5 166 MB. It serves two purposes:

- **Older firmware updates offline.** An install on a firmware the release updates from runs the QIDI firmware update first. It uses the drive's package once it matches the pinned size and SHA-256, the same check as a mirror download, and falls back to the mirror only when the drive's copy is missing or doesn't match.
- **The folder ends exactly as QIDI left it.** On the target firmware, the installer copies QIDI's system package file back into the update folder from that package, so the folder matches QIDI's package exactly.

The restore sets come from the same mirrored archives. `probe/fetch_restore_sets.py` reads only the manifest and board images out of each archive with HTTP range requests. The release build pins them by hash.

### One flow, two front ends

The installer runtime gains a plan step and a run step. The plan step detects the printer and returns the questions that apply. The run step takes the answers and needs no input. The terminal installer and the touchscreen front end both drive those two steps, so the checks, backup, rollback, and firmware safeguards stay the ones in the spec today. The SSH installer moves to the same order: every question, one summary, then an unattended run. Restarting Klipper stops being a question: a run that changed something restarts it.

### Fewer questions

- **What's left:** the touchscreen and the SSH installer ask about automatic updates and, while it is on, QIDI's AI detection.
- **QIDI Box:** a new install that finds QIDI Boxes turns Box support on without asking, and later runs leave it as the operator set it. `enable_box` is QIDI's per-print "use the Box" flag, which every print start rewrites. Forcing it on at every run would undo an external-spool choice for prints started from Fluidd or OrcaSlicer.
- **Tool-slot mappings:** a mapping that doesn't line up isn't asked about. Operators map tools to slots however they want, so existing mappings are left alone.
- **Reboot:** the reboot after host tuning isn't a question. A USB run restarts the printer at the end.

### Screens

`screens.html` in this change holds the approved screens and their wording, in Montserrat at the panel's 800×480. The toolhead-fix question is gone, since board firmware goes with every install. The set covers:

- checking;
- what it found, and the older-firmware variant;
- the two questions;
- ready;
- installing, and Box firmware;
- done;
- update available;
- up to date;
- SSH install and automatic update progress;
- busy;
- unsupported firmware;
- board not supported, with the printer-support issue link as a QR code;
- install stopped;
- the log.

### Drawing

The touchscreen front end extends `update_screen.py` rather than adding Pillow. `scripts/render_touchscreen_images.py` renders each screen's fixed parts and a glyph sheet of Montserrat at build time. The printer composes them with the text that changes (versions, step names, elapsed time) and decodes them with zlib. The front end reads the Goodix touch device directly and draws nothing while idle. A screen that can't be drawn falls back to `update_screen.py`'s text, as the install screen does.

The font is Montserrat (SIL Open Font License 1.1). The font file and its license are committed for the build. The license ships with the bundle and is listed in `THIRD_PARTY_NOTICES.md`. The still images that `scripts/render_touchscreen_images.py` already draws (installing, updating, boot logo) move to Montserrat too, so every Valve Oil screen uses one typeface.

### Finish when the drive comes out

The finish screen comes after the log is written to the drive and flushed. It asks the owner to take the drive out and restarts the printer as soon as the drive's mount under `printer_data/gcodes/USB` goes away. Restart now restarts with the drive left in. That is harmless: the app starts a package from the drive only when someone picks it.

### One progress screen for every install

SSH installs and automatic updates show the same live progress screen as a USB run, in place of today's still install and update images. It shows the step, its place in the run, the elapsed time, and how the run was started. When the touchscreen is held and released stays as "The touchscreen shows the install" says; only what is drawn changes. Those runs offer no buttons but Log.

### Log

Every run writes its debug-level events to a log in `printer_data/logs/`, where Fluidd can download it. The USB installer copies it to the drive when it finishes or fails.

## Risks / Trade-offs

- [The USB path has run on one firmware per printer] → it is runtime-confirmed on Max 4 `01.01.07.01`, Plus 5 `01.01.02.01`, and Smart 5 `01.01.02.01`. Older supported Max 4 firmware is taken to behave the same. The full USB install is validated on each printer before release.
- [The app empties `/home/qidi/update/` before any of our code runs] → the restore is the hand-off's first step and is checked by hash. A firmware with no set gets the unsupported screen and instructions to get QIDI's files back.
- [A QIDI firmware update adds a signature check or changes the version comparison] → the SSH install stays. The drive's zips name the firmware they support, and each new QIDI firmware is probed before a release supports it.
- [The app restarts itself while the installer starts] → the installer runs in its own systemd unit and waits for the restarts before it takes the screen.
- [Power loss during the run] → no worse than an SSH install: the same journal, backup, and recovery sentinel apply.
- [The drive image grows by about 600 MB with QIDI's packages] → it is the price of offline firmware updates. The drive is extracted once, and USB sticks are far larger.
- [Redistributing QIDI's packages and board images] → they are the files QIDI's own firmware zips carry, from the same mirror the installer already downloads from.
