## Purpose

Keep QIDI's own firmware packages off a printer running Valve Oil, and deliver each QIDI firmware and its board firmware through a Valve Oil release that supports it.

## ADDED Requirements

### Requirement: QIDI package updates are blocked while Valve Oil is installed
On a printer whose THR and mainboard are admitted, a normal install or update SHALL patch a reviewed touchscreen executable so that the app cannot install a QIDI firmware package by any of its paths, independently of optional OS optimization policy. Like the other guarded host changes, the patch SHALL change only a recognized reviewed build or this package's patch of it, and SHALL keep QIDI's original for restoration. A USB Box the release does not admit SHALL NOT stop the block.

#### Scenario: A package update is started from the touchscreen
- **WHEN** a blocked touchscreen app starts an online update, an update from a USB drive, or a mandatory-update file
- **THEN** no file under QIDI's update folder, no installed QIDI package, no running service, and no board firmware changes

#### Scenario: The online-update notice is hidden
- **WHEN** install or update blocks a reviewed touchscreen build
- **THEN** that build no longer announces QIDI's online update, and its per-board firmware notices and per-board update still work

#### Scenario: The touchscreen executable is not a reviewed build
- **WHEN** install or update finds a touchscreen executable that is not a reviewed QIDI build
- **THEN** the executable is left unchanged, and the app installs QIDI packages as it does without Valve Oil

#### Scenario: The block is removed
- **WHEN** Valve Oil is uninstalled
- **THEN** QIDI's original touchscreen executable is restored, and the app installs QIDI packages as it did before the install

### Requirement: Each release delivers one target QIDI firmware per printer
`installer/package.yaml` SHALL name, for each printer, one target firmware, its package's identity on this project's mirror, and the firmware versions it can be updated from. An install or update on one of those versions SHALL bring the printer to the target firmware and install Valve Oil for it in the same run. Packages MUST come only from this project's mirror and MUST match the pinned SHA-256 before anything on the printer changes.

#### Scenario: Printer on an older supported firmware
- **WHEN** an install or update runs on an idle printer whose firmware the release can update from
- **THEN** the pinned package is downloaded, verified, and installed, the THR and mainboard end on the firmware pinned for the target, and Valve Oil is installed for the target firmware
- **AND** USB Boxes, which QIDI's package removes from `box.cfg` until the touchscreen app finds them again, get their pinned firmware at the next install, or at the next update check on a printer with automatic updates on
- **AND** the firmware manifest and board images in QIDI's update folder are the target package's

#### Scenario: Firmware the release cannot update from
- **WHEN** the printer's firmware is newer than the target or is not listed
- **THEN** the operation is refused before any change, as for any unsupported firmware

#### Scenario: The package cannot be fetched or verified
- **WHEN** the mirror is unreachable, the download does not match the pinned size and SHA-256, or the package is not a flat set of plain files whose manifest and SOC package name the printer and the target firmware
- **THEN** the firmware update does not start, the firmware and boards are left as they are, and nothing from the package runs or is written outside the installer's cache and its unpacking folder
- **AND** Valve Oil is still installed or updated for the printer's current firmware
- **AND** the firmware update stays waiting, with the reason, and is tried again at the next install, or at the next update check on a printer with automatic updates on

#### Scenario: The printer is not safe to update
- **WHEN** a print is running or paused, a heater target is nonzero, free space is short, the THR or mainboard is not admitted, an earlier board or installer operation needs recovery, or sudo would need a password the detached update cannot ask for
- **THEN** the firmware update does not start, nothing changes for it, the reason is reported and recorded as waiting work, and Valve Oil is installed or updated for the current firmware

### Requirement: A firmware update survives its own side effects
A firmware update SHALL run independently of the session that started it, SHALL keep its working files where QIDI's package does not delete them, and SHALL record enough to finish after an interruption.

#### Scenario: Started over SSH
- **WHEN** an operator starts an install that includes a firmware update from an SSH session and the session ends
- **THEN** the update continues to completion and its progress and result can be read afterwards
- **AND** the next install from a terminal follows an update still running, or reports the result of one no session saw finish, instead of starting another

#### Scenario: An interactive install
- **WHEN** an interactive install finds a firmware update it can start
- **THEN** it says which firmware it installs, which boards it flashes, that the printer can't be used and must not be powered off while it runs, and roughly how long it takes, and asks once
- **AND** an install with `--yes` and the install an automatic update runs do not ask
- **AND** an install with `--dry-run` reports the same plan without downloading, stopping, or changing anything

#### Scenario: The touchscreen app is stopped
- **WHEN** the update has stopped the touchscreen app
- **THEN** the screen shows a message not to power the printer off, upright on each printer's screen, until the app starts again
- **AND** a screen the app had put to sleep is lit for the message

#### Scenario: Power is lost during the update
- **WHEN** the printer restarts with a firmware update unfinished
- **THEN** the update resumes without operator action, and no other installer operation runs until it finishes, except a board's rescue
- **AND** once QIDI's package is installed, the resumed update stops Klipper, the touchscreen, and Moonraker again and flashes the boards without waiting for Klipper, finding the mainboard at its USB position when its new firmware has renamed it
- **AND** while QIDI's update folder still describes the firmware being replaced, it holds no SOC package the touchscreen app's own board update could install
- **AND** a board transfer that cannot be verified keeps that board's rescue record and instructions
- **AND** after a few boots that each stopped partway, the update is recorded as failed and the printer is left running

#### Scenario: A step fails after the printer changed
- **WHEN** installing QIDI's package or flashing a board fails
- **THEN** the update is recorded as failed at that step with what to do next, and a printer warning and the Valve Oil section show it
- **AND** it is continued by the next install or update now rather than at every boot, and only while nothing prints and no heater has a target
- **AND** the screen says the update did not finish while the touchscreen app stays stopped
- **AND** a board's rescue may run only once the update has stopped with that failure
- **AND** after a board write that cannot be verified only Moonraker is started again; after a failure that wrote nothing to a board, Klipper and Moonraker are started again too
- **AND** when the mainboard is left on a version the target firmware's Klipper can't use, the touchscreen is started as well and the result says to update the mainboard from the touchscreen's firmware page before running the installer again

#### Scenario: A recorded update this release cannot continue
- **WHEN** an install or update finds a firmware update recorded by another release, for another target firmware or in a newer format
- **THEN** a record of which nothing was installed yet is dropped and the release plans its own update, and any other is kept with the command that installs the release that can finish it
- **AND** the installer bundle that release left is not replaced

#### Scenario: The operator's saved calibration
- **WHEN** a firmware update installs QIDI's package, which replaces `printer.cfg` and may reset `saved_variables.cfg`
- **THEN** the `SAVE_CONFIG` block from before the update is back in the new `printer.cfg` before the boards are flashed and Valve Oil is installed, with the operator's values over QIDI's and the body changed only as Klipper's `SAVE_CONFIG` would change it, and saved variables QIDI's package reset are back
- **AND** a saved section the new firmware's config no longer has, or an option Valve Oil's install sets, is not carried, and the result names it with the path of the config backup
- **AND** an update resumed after a power loss puts back the calibration captured before QIDI's package first ran, never QIDI's
- **AND** a calibration that can't be restored is reported the same way and doesn't fail the update

#### Scenario: Uninstall after a firmware update
- **WHEN** a firmware update installs QIDI's package, which deletes Valve Oil's installed state with the config folder
- **THEN** the install that follows still holds what the earlier install recorded about the host before it changed it, for every host change that is still as that install left it, and keeps the operator's earlier choices about the optional OS optimizations and AI detection
- **AND** a later uninstall undoes those host changes as it would have before the update
- **AND** a host file QIDI's package replaced is recorded as it is after the package, so uninstall puts back the new firmware's file

#### Scenario: Valve Oil's install for the new firmware fails
- **WHEN** QIDI's package and the boards are done but installing Valve Oil for the target firmware fails
- **THEN** the update ends, every service is started again, and the installer's own recovery and the next install or automatic update apply

### Requirement: Boards receive this project's firmware
A firmware update, an install, and an automatic update SHALL bring the THR and every USB Box on both printers, and the mainboard on the Max 4, to this project's build for the target firmware, without a question and without a per-run option. A board with no build of this project SHALL receive QIDI's image for the target firmware when its version changes. A board already on its pinned image MUST NOT be flashed.

#### Scenario: Board on QIDI's firmware or an earlier build
- **WHEN** a run finds an admitted board on QIDI's firmware or an earlier build of this project
- **THEN** the board is backed up, flashed with the pinned image, and verified by readback, under the same idle, heater, and service-ownership rules as the board scripts

#### Scenario: The THR was flashed
- **WHEN** a run has flashed the THR
- **THEN** the saved input shaper calibration stays in effect and a printer warning says to run input shaper calibration, until the operator has

#### Scenario: Automatic update
- **WHEN** an enrolled printer's automatic update finds boards that need their pinned image and the printer passes the update checks
- **THEN** those boards are flashed unattended

#### Scenario: Closed-loop motor drivers
- **WHEN** a Max 4 firmware update's target package carries another closed-loop driver version than the installed firmware's, and both drivers report the installed firmware's driver version (or already the target), the expected hardware, and idle
- **THEN** before QIDI's package is installed, with the touchscreen and Moonraker stopped and the motors off, the run sends each driver not on the target QIDI's image, checked against its pinned size and SHA-256, through QIDI's `UPGRADE_MOTOR`, X then Y
- **AND** a driver counts as updated only once it reports the target version and idle

#### Scenario: Closed-loop drivers not as expected
- **WHEN** either driver reports another version or hardware, or is not idle, before anything was sent
- **THEN** neither driver is sent an image, the rest of the update goes on, and the update's result and the Valve Oil section name the driver and say to update the drivers from the touchscreen's firmware page

#### Scenario: A closed-loop driver update fails
- **WHEN** a driver isn't verified after three transfers
- **THEN** the bus is reset with `FIRMWARE_RESTART`, the other driver is not touched, QIDI's package is not installed, every service is started again, and the result gives what to do and a prefilled support issue
- **AND** the next install or "update now" continues the update, sending the image again to a driver the run admitted, even when it no longer reports a version

#### Scenario: Closed-loop drivers in the status
- **WHEN** an update check runs on a Max 4 that is on the release's QIDI firmware
- **THEN** the status lists the closed-loop drivers with the version Klipper reports, up to date on the target package's version and otherwise to be updated from the touchscreen's firmware page
- **AND** the check sends the drivers nothing

### Requirement: New installs require admitted boards
A new installation SHALL read the THR, the mainboard, and every configured USB Box without erasing, and SHALL be refused before any change when a bootloader or firmware is not admitted by the release, or when a board can't be read.

#### Scenario: A board is not admitted on a new install
- **WHEN** a new install finds a board whose bootloader or firmware the release does not admit
- **THEN** nothing is installed, the board is not reset or programmed, and its shareable report is kept
- **AND** the output gives a link to the printer-support issue form with the printer, firmware, Box setup, printer details, and that report already filled in

#### Scenario: A board is not admitted on an existing install
- **WHEN** an update runs on an existing installation with a THR or mainboard the release does not admit
- **THEN** the installation is updated without the block on QIDI's package updates and without firmware delivery, and a printer warning names the board and the issue link
- **AND** once a release admits the board, the next install or automatic update applies the block and delivers firmware

### Requirement: USB Boxes connected after install are brought up to date
The update check SHALL notice a configured USB Box that is not on its pinned firmware without stopping any service, from the version Klipper reports for it and what the last read of that Box found. Only an enrolled check on a printer that passes the update checks MAY stop services, to read a Box it has not read at the version it reports and to flash it.

#### Scenario: Admitted Box on QIDI's firmware
- **WHEN** an enrolled printer's update check finds an admitted USB Box on recognized QIDI firmware and the printer passes the update checks
- **THEN** that Box is flashed and verified in that run, whether or not a new release is installed
- **AND** on a printer that is not enrolled, a printer warning says to run the installer

#### Scenario: Box with firmware the release does not recognize
- **WHEN** a USB Box has an admitted bootloader and firmware the release does not recognize
- **THEN** the Box is left unchanged with a printer warning, and a release that recognizes it flashes it

#### Scenario: Box with a bootloader the release does not admit
- **WHEN** a USB Box has a bootloader the release does not admit
- **THEN** the Box is left unchanged, a printer warning says what to do and gives the filled-in issue link, and firmware delivery continues for the rest of the printer
- **AND** when a delivered firmware changes the Box version, the warning says to update that Box from the touchscreen's firmware page
- **AND** update checks don't read that Box again until it reports another version or a release admits what was found

#### Scenario: Box restored to QIDI's firmware
- **WHEN** the operator has run `box-firmware.sh restore` on a Box
- **THEN** update checks leave that Box on QIDI's firmware until the next release is installed

### Requirement: Pending work is shown wherever the operator looks
While a release, a firmware update, or a board flash is waiting, `tltg_printer_warnings.warnings` SHALL list it without a Klipper restart, and Fluidd and Valve Oil UI SHALL show a Valve Oil section with the installed release, the printer's QIDI firmware, each board's firmware, and what is waiting. A waiting release is either a newer one or the installed release installed again, and SHALL be shown as which it is.

#### Scenario: Automatic updates are off
- **WHEN** the update check finds a newer release or a firmware update on a printer that is not enrolled
- **THEN** nothing is installed, and the warning says an update is waiting and that the installer has to be run

#### Scenario: Automatic updates are on
- **WHEN** the update check finds the same on an enrolled printer that cannot update yet
- **THEN** the warning says an update is waiting and why it has not run

#### Scenario: Valve Oil has to be installed again
- **WHEN** the update check finds the installed release no longer installed as it installs it, such as after QIDI's package emptied the config folder
- **THEN** the warning and the Valve Oil section say that Valve Oil needs to be installed again, without naming it as a newer release
- **AND** an install of that release ends the wait without waiting for the next check

#### Scenario: Only authorized same-origin requests change settings
- **WHEN** a client reads the Valve Oil section's data or changes a setting in it
- **THEN** the request is served only under the same authorization and same-origin rules as the add-ons route
