# Board firmware

[← Back to the README](../README.md)

Valve Oil puts its own firmware on the printer's boards, with fixes for QIDI's: the toolhead board (THR) on both printers, the mainboard on the Max 4, and each USB QIDI Box. Every install and update, automatic ones included, flashes each board that doesn't have it yet, without asking. A board that already has it is left alone.

| Board | QIDI Max 4 | QIDI Plus 5 |
|---|---|---|
| Toolhead board (THR) | [Valve Oil's](#toolhead-accelerometer-fix), `02.02.01.08-valve-oil.1` | [Valve Oil's](#toolhead-accelerometer-fix), `02.02.01.08-valve-oil.1` |
| Mainboard | [Valve Oil's](#mainboard-closed-loop-reply-fix), `02.01.01.12-valve-oil.1` | QIDI's, `02.01.01.12` |
| USB QIDI Box | [Valve Oil's](#qidi-box-firmware), `02.03.01.21-valve-oil.1` | [Valve Oil's](#qidi-box-firmware), `02.03.01.21-valve-oil.1` |
| Closed-loop X/Y motor drivers | [QIDI's, updated with a QIDI firmware update](#closed-loop-motor-drivers) | none |

The versions are what each board reports in Fluidd and `klippy.log`. Valve Oil's are QIDI's version followed by `-valve-oil.` and a revision number that goes up with each new build.

## How boards are flashed

Each board is flashed only while the printer is idle and ready, with every heater target at zero. Klipper and the touchscreen stop while it happens, about three minutes for the mainboard and about four minutes per Box, and the printer can't move or heat until they start again. **Keep the printer and every Box powered on until it finishes.** Once board work has started, closing the SSH session doesn't stop it, and Ctrl-C only prints `Board firmware work is in progress, so Ctrl-C is ignored until Klipper and the touchscreen run again. Keep the printer powered on.`

Before flashing, Valve Oil reads the board's bootloader and firmware and flashes only builds it recognizes. It keeps a copy of QIDI's firmware under `~/printer_data/` (`.tltg_thr_firmware_backups/`, `.tltg_mainboard_firmware_backups/`, `.tltg_box_firmware_backups/`), and reads the result back to check it matches exactly, with the bootloader unchanged. A board it can't flash safely, for example because a heater is on, keeps its firmware; the install still finishes, says why, and the next install tries again.

The THR and mainboard firmware are built for the newest QIDI firmware Valve Oil supports. On the Max 4 with an older QIDI firmware, they're flashed once Valve Oil has [updated the QIDI firmware](install.md#qidi-firmware-updates) to `01.01.07.01`; until then the installer says `THR: will be updated after the QIDI firmware update to 01.01.07.01. Its firmware is not changed now.` USB Boxes are flashed on any supported firmware.

A board whose bootloader or firmware Valve Oil doesn't recognize is never flashed. On a new install that stops the install; on an update it gets a printer warning with a prefilled support issue. See [Boards Valve Oil doesn't support yet](install.md#boards-valve-oil-doesnt-support-yet).

Uninstalling Valve Oil doesn't change firmware. To put QIDI's firmware back on a board, use that board's `restore` below.

## Toolhead accelerometer fix

On the Max 4 with QIDI firmware `01.01.07.01` and the Plus 5 with `01.01.02.01`, QIDI's toolhead board (THR) firmware reads its accelerometer incorrectly, which affects input shaper calibration. Both printers use the same toolhead board and THR firmware. Valve Oil's THR firmware fixes the LIS2DW FIFO bug.

> [!IMPORTANT]
> **After the THR is flashed, run input shaper calibration before bed leveling or printing.** Your saved calibration stays in use, including after a [QIDI firmware update](install.md#qidi-firmware-updates), which puts it back before the THR is flashed, but it was measured with QIDI's firmware. Fluidd shows the warning `Run input shaper calibration.` until a new calibration is saved. Run input shaper calibration on its own first: the screen's calibration page runs bed leveling before input shaping when both are checked. The installer does not run calibration for you.

A board with the earlier build of the fix, which reported QIDI's `02.02.01.08`, is updated too, with the new firmware built from QIDI's stock THR firmware in the THR firmware backups or `~/update`. `--flash-thr-fifo`, which earlier releases needed to flash the THR, is still accepted and does nothing.

**If your board isn't supported:** see [Boards Valve Oil doesn't support yet](install.md#boards-valve-oil-doesnt-support-yet). On an existing install, the THR and mainboard keep their firmware and Fluidd's warning links to a prefilled support issue; `tltg_thr_firmware.json` in Fluidd's **Logs** is the board's report.

**To go back to stock firmware:** see [Restore the stock THR firmware from backup](../firmware/README.md#restore-the-stock-thr-firmware-from-backup). The next install, or the next update check with automatic updates on, flashes Valve Oil's THR firmware again. Details on supported bootloaders and recovery are in [THR-only operations](../firmware/FLASHING.md).

## QIDI Box firmware

QIDI's USB Box firmware turns every Klipper shutdown of the Box, such as an emergency stop or a heater safety limit, into a restart. Klipper then reports `Lost communication with MCU 'mcu_boxN'` instead of the reason, and every other Box drops too. The Valve Oil Box firmware keeps the Box connected, so Klipper shows what actually happened.

It also lets the Box [read Anycubic, Elegoo, and Bambu Lab spool tags](qidi-box.md#spool-tags), by giving Klipper direct access to the Box's RFID readers in place of four QIDI test commands that nothing on the printer uses. QIDI's own spool tags read as before.

**When it's flashed:** after the rest of each install and update. A USB Box connected after the last install is picked up by the next update check: with [automatic updates](install.md#automatic-updates) on, the check flashes it while the printer is idle; with them off, a printer warning says new firmware is waiting for it and to run the installer. A [QIDI firmware update](install.md#qidi-firmware-updates) doesn't flash Boxes itself; the next install or update check does, once the touchscreen has found them again. RS-485 Boxes have no firmware to flash.

A Box whose bootloader or firmware isn't supported yet is left as it is, and Fluidd shows a warning for it, `Box N can't get the Valve Oil Box firmware yet.`, on every page, with a link to a prefilled [support issue](https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml). `tltg_box_firmware_box<N>.json` in Fluidd's **Logs** is the report to attach. Update checks and automatic updates then skip that Box, without stopping Klipper, until it reports another firmware or a release supports it, and the warning goes away once the Box has the Valve Oil firmware. If a QIDI firmware update Valve Oil delivers ever changes the Box firmware version, the warning also says to update that Box from the touchscreen's firmware page.

A Box with this firmware reports its version as `02.03.01.21-valve-oil.1`. The installer also updates a Box with earlier firmware from this project, such as the shutdown fix (`02.03.01.21-tltg-shutdownfix1`). It builds the new firmware from QIDI's stock Box firmware, which it takes from the backups it saved when it first flashed that Box, or from `~/update`.

To check or change a Box's firmware yourself, run these over SSH from the installer folder:

```sh
cd ~/tltg-optimized-macros
./box-firmware.sh status    # which firmware each Box runs
./box-firmware.sh install   # flash it to every USB Box (or --box N)
./box-firmware.sh restore   # put QIDI's stock Box firmware back
```

`install` and `restore` ask you to type `FLASH`. A Box you put back on QIDI's firmware with `restore` stays on it through installs and update checks until you install the next Valve Oil release, or until you run `./box-firmware.sh install` for it.

**If a flash fails:** see [Valve Oil QIDI Box firmware](../firmware/BOX_FIRMWARE.md).

## Mainboard closed-loop reply fix

On the Max 4, the X and Y motors are closed-loop: each one's driver knows where the motor is and answers questions about it over a bus that the mainboard runs. QIDI's mainboard firmware (`02.01.01.12`) passes an answer on to Klipper the moment it ends only for the status and version questions. Every other answer, including position error, encoder position, temperature, and the acknowledgement of each setting QIDI sends while homing, waits for 50 ms of silence on the bus, so it takes about 70 ms. This fix tells the mainboard how long each of those answers is, so it passes them on at their last byte. Answers it doesn't list are handled exactly as before. On the test printer, position-error, encoder, and temperature answers came back in a median 7–8 ms instead of 68–73 ms. Homing takes as long as before. The [X/Y step-loss monitor](tools.md#xy-step-loss-monitor) and QIDI's own motor checks work with or without it.

**When it's flashed:** on the Max 4, by every install and update. A mainboard with QIDI's `02.01.01.11`, from firmware `01.01.06.03` or the first `01.01.06.04`, is flashed after Valve Oil has updated the QIDI firmware to `01.01.07.01`, since the new firmware's Klipper can't use `02.01.01.11`. A mainboard with the earlier build of the fix (`02.01.01.12-tltg-clreplies1`) is updated, with the new firmware built from QIDI's stock mainboard firmware in its backups or `~/update`.

**On the Plus 5,** whose motors aren't closed-loop, the mainboard keeps QIDI's firmware. Valve Oil flashes QIDI's image only if a QIDI firmware it delivers changes the mainboard's version.

To check or change the mainboard's firmware yourself, run these over SSH from the installer folder:

```sh
cd ~/tltg-optimized-macros
./mainboard-firmware.sh status    # which firmware the mainboard runs
./mainboard-firmware.sh install   # flash Valve Oil's firmware (QIDI's on the Plus 5)
./mainboard-firmware.sh restore   # put QIDI's stock mainboard firmware back
```

The script asks you to type `FLASH`. After `restore` on the Max 4, the next install, or the next update check with automatic updates on, flashes Valve Oil's mainboard firmware again.

**If a flash fails:** see [Mainboard firmware](../firmware/MAINBOARD_FIRMWARE.md).

## Closed-loop motor drivers

The Max 4's closed-loop X and Y motor drivers have their own firmware, which QIDI's firmware packages update. Valve Oil has no build of its own for them: when a [QIDI firmware update](install.md#qidi-firmware-updates) changes their version, it updates them with QIDI's driver firmware from that package, using QIDI's own update command, as QIDI's update does. QIDI firmware `01.01.07.01` carries driver firmware `03.01.10.15`, and `01.01.06.03` to `01.01.06.05` carried `03.01.10.13`.

The drivers are updated first, before anything else in the update changes, one at a time, X then Y; each takes about a minute. Valve Oil stops the touchscreen and Moonraker first, so nothing else can talk to the printer, and turns the X and Y motors off, so home the printer again afterwards. It checks that QIDI's driver firmware file is the exact one this release names before each try.

- **A driver gets the new firmware only when it's exactly as expected:** reporting the driver firmware QIDI's older package carries, the hardware QIDI's image is for, and idle. A driver already on the new firmware is left as it is. If either driver reports anything else, Valve Oil sends neither one anything, the rest of the update goes ahead, and the update's result and the **Valve Oil** section in Fluidd and Valve Oil UI name the driver and what it reported, with a **Report this board** link. Update them from the touchscreen's firmware page, which Valve Oil leaves working.
- **A driver counts as updated only once it reports the new firmware.** One that doesn't is tried up to three times. If it still doesn't, Valve Oil leaves the other driver alone, doesn't install QIDI's package, resets the motor drivers' connection, and starts the printer again; see [The closed-loop motor driver update failed](troubleshooting.md#the-closed-loop-motor-driver-update-failed).
- **Keep the printer powered on.** If the power goes off while a driver is being updated, the update tries that driver again when the printer starts. Whether a driver cut off part way through always takes its firmware again hasn't been tested on a printer.
