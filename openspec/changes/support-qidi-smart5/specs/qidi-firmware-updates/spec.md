## MODIFIED Requirements

### Requirement: Boards receive this project's firmware
A firmware update, an install, and an automatic update SHALL bring the THR and every USB Box on every printer, and the mainboard on the Max 4, to this project's build for the target firmware, without a question and without a per-run option. Each board's image SHALL come from the one board manifest that pins it for the printer's target firmware, so a printer whose THR image differs gets the build made from its own image. A board with no build of this project SHALL receive QIDI's image for the target firmware when its version changes. A board that no manifest pins for the printer's target firmware SHALL NOT be read or flashed. A board already on its pinned image MUST NOT be flashed.

#### Scenario: Board on QIDI's firmware or an earlier build
- **WHEN** a run finds an admitted board on QIDI's firmware or an earlier build of this project
- **THEN** the board is backed up, flashed with the pinned image, and verified by readback, under the same idle, heater, and service-ownership rules as the board scripts

#### Scenario: The THR was flashed
- **WHEN** a run has flashed the THR
- **THEN** the saved input shaper calibration stays in effect and a printer warning says to run input shaper calibration, until the operator has

#### Scenario: USB Boxes during a firmware update
- **WHEN** a firmware update's board step runs after QIDI's package has reset `box.cfg`
- **THEN** the update has written `box.cfg` for the USB Boxes connected, and the board step flashes and verifies each of them like the THR and the mainboard

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

#### Scenario: Smart 5 boards
- **WHEN** an install runs on a Smart 5 on its target firmware
- **THEN** its THR is read and brought to the build made from QIDI's `02.02.01.09` image, and each USB Box to the same Box build as on the other printers
- **AND** its i.MX RT mainboard is not read, flashed, or listed among the boards, and the mainboard firmware tool stops on it without touching it

### Requirement: New installs require admitted boards
A new installation SHALL read the THR, the mainboard where the release manages it, and every configured USB Box without erasing, and SHALL be refused before any change when a bootloader or firmware is not admitted by the release, or when a board can't be read.

#### Scenario: A board is not admitted on a new install
- **WHEN** a new install finds a board whose bootloader or firmware the release does not admit
- **THEN** nothing is installed, the board is not reset or programmed, and its shareable report is kept
- **AND** the output gives a link to the printer-support issue form with the printer, firmware, Box setup, printer details, and that report already filled in

#### Scenario: A board is not admitted on an existing install
- **WHEN** an update runs on an existing installation with a THR or mainboard the release does not admit
- **THEN** the installation is updated without firmware delivery and without a QIDI firmware update, and a printer warning names the board and the issue link
- **AND** QIDI's package updates are blocked on the touchscreen, as on a printer whose boards are admitted, so a QIDI update can't take the printer to firmware no release supports
- **AND** once a release admits the board, the next install or automatic update delivers firmware
