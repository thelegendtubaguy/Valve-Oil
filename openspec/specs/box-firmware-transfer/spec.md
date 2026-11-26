# box-firmware-transfer Specification

## Purpose
Provide an explicitly operated transfer path for the USB QIDI Box and the Max 4 mainboard, both Klipper boards behind QIDI's USB bootloader, with known-image and bootloader-build admission, recoverable artifacts, and byte-based installation evidence.

## Requirements

### Requirement: Board serial operations require explicit safe ownership
The Box and mainboard transfer tools SHALL refuse serial access unless the operator confirms the printer is idle and Klipper, the touchscreen client, and the automatic-update timer are stopped. They MUST NOT modify printer configuration, control heaters or motion, or flash any board other than the addressed USB Box or the mainboard.

#### Scenario: Serial ownership is unsafe
- **WHEN** idle confirmation is absent or a required service is still running
- **THEN** the tool fails before opening the board's serial port

### Requirement: Only the board scripts and installs reach the transfer tools
`box-firmware.sh`, `mainboard-firmware.sh`, and the board step of an install, update, or firmware update, including one an automatic update runs, SHALL be the only callers of the transfer tools. An install, `--yes`, and automatic updates MUST reach the tools only through that step and only to install the image pinned for the printer's target firmware.

#### Scenario: Automatic update
- **WHEN** an automatic update installs a release or finds a board that is not on its pinned image
- **THEN** the Box and mainboard transfer tools run only to install the pinned images, and never to restore or rescue

### Requirement: Transfers admit only known images and bootloaders
The tools SHALL program only the stock or patched application pinned for that board and the printer's target firmware, only through a bootloader of a validated build, and only with explicit write confirmation, using backups of that board's bootloader and application.

#### Scenario: Mainboard images per printer
- **WHEN** the tools program a mainboard
- **THEN** this project's mainboard build is offered only on the Max 4, and a Plus 5 mainboard receives only QIDI's stock image

#### Scenario: A transfer is not restarted
- **WHEN** a transfer's start command or first data packet is not acknowledged
- **THEN** the tool does not resend it, because the bootloader's write position only rewinds at reset

#### Scenario: Known image through an admitted bootloader
- **WHEN** the image, backups, and write confirmation are valid and the installed bootloader matches its backup
- **THEN** the tool resets the board into its bootloader and transfers the image in acknowledged 1 KiB packets

#### Scenario: Unknown input
- **WHEN** the image, backup, confirmation, or bootloader identity is not admitted
- **THEN** no reset or erase command is sent

#### Scenario: Bootloader differing from a validated build only in inert bytes
- **WHEN** a bootloader's SHA-256 matches no validated build, but it matches one once the bytes the manifest declares inert for that build (bytes the build never reads or runs) are put back to the build's values
- **THEN** the board is admitted, flashed, and rescued as that build, its report names the build it matched, and a bootloader with any other byte changed is not admitted

### Requirement: Earlier Box and mainboard builds are updated from QIDI's stock image
A Box or mainboard application that matches an earlier build of this project, or QIDI's image of an earlier board version listed in the manifest as a predecessor by exact size and SHA-256, SHALL be admitted for backups and replaced with the image pinned for the printer's target firmware, made from QIDI's stock application for that board. A predecessor image MUST NOT be programmed.

#### Scenario: Where the stock application comes from
- **WHEN** an earlier build or a predecessor is replaced
- **THEN** the pinned image is made from QIDI's stock application found in that board's firmware backups or the printer's update folder, and rescue restores that stock application

#### Scenario: Predecessors wait for the target firmware
- **WHEN** a board runs a predecessor and the printer doesn't run the target firmware yet
- **THEN** the board is not replaced, because the flash changes the board's version and the installed Klipper may not support it

#### Scenario: Earlier Valve Oil Box firmware is updated
- **WHEN** `box-firmware.sh install` finds a Box running an earlier build of this project
- **THEN** it builds the current image from QIDI's verified stock application and installs it
- **AND** with no verified stock application available it stops before any service is stopped or Box is reset

#### Scenario: Earlier mainboard fix is updated
- **WHEN** `mainboard-firmware.sh install` finds the mainboard reporting an earlier build of the closed-loop reply fix
- **THEN** it builds the current image from QIDI's verified stock mainboard application and installs it
- **AND** with no verified stock application available it stops before any service is stopped or the mainboard is reset

#### Scenario: Mainboard on QIDI's image of an earlier version
- **WHEN** the board step or `mainboard-firmware.sh` finds a Max 4 mainboard on QIDI's `02.01.01.11`, which `01.01.06.03` and the first `01.01.06.04` package carry, behind an admitted bootloader
- **THEN** while the printer runs that firmware the mainboard is not reset or programmed, and the board step reports it as waiting
- **AND** once the printer runs the target firmware it receives the pinned image built from QIDI's `02.01.01.12`, verified by readback, and with no verified copy of that stock application no reset or erase occurs

#### Scenario: Another image reporting a predecessor's version
- **WHEN** a board reports a predecessor's version but its application does not match the listed image
- **THEN** it is not admitted, and it is not reset or programmed

### Requirement: Installation evidence uses readback
A Box or mainboard flash SHALL be reported as installed only when a full application readback matches the selected image and the bootloader readback matches the pre-transfer backup. Transfer acknowledgements and the reported version string are not installation evidence.

#### Scenario: Readback differs
- **WHEN** the application readback differs from the image or the bootloader changed
- **THEN** verification fails regardless of acknowledgements or version

### Requirement: Unrecognized builds are captured without reset
When readback finds a Box or mainboard bootloader or application that is not admitted, the tool SHALL keep a private read-only capture and a shareable summary without flash binaries or MCU unique identifiers, SHALL give the operator a link to the printer-support issue form with the printer, its firmware, and that summary's hashes filled in, and MUST NOT reset, erase, or program that board. A Box SHALL be read whatever version it reports, since the reported version is not admission evidence.

#### Scenario: Unrecognized bootloader
- **WHEN** a board's bootloader matches no admitted build
- **THEN** that board is reported as not changed with the summary's location and the filled-in support issue link, and no reset, erase, or programming command is sent to it

#### Scenario: Box reporting a version no admitted image has
- **WHEN** `box-firmware.sh install` runs for a USB Box that reports a version none of the manifest's images has, such as its board maker's own build
- **THEN** the Box is read like any other and flashed only if its bootloader and application are admitted
- **AND** when they are not, the output also says to update that Box from the touchscreen's firmware page when its version differs from the one the printer's QIDI firmware carries, and `box-firmware.sh status` says to do that and then run `install` for that Box

#### Scenario: Operator's own Box install
- **WHEN** an operator's `box-firmware.sh install` flashes a Box, finds it current, or finds it not admitted on a printer with an admission record
- **THEN** the admission record, the printer warnings, and the Valve Oil status show that Box as an install would have recorded it: current and no longer waiting, or not admitted with its warning and support issue link

### Requirement: Unverified flashes block until rescue
When programming may have begun and readback has not verified the board, the flow SHALL leave Klipper and the touchscreen stopped and keep a pending record that blocks every installer operation except that board's rescue. Rescue SHALL program QIDI's stock application from a verified backup or the printer's update folder through the waiting bootloader and verify it by readback before restoring services and clearing the record.

#### Scenario: Interrupted mainboard transfer
- **WHEN** a mainboard transfer fails after the application was erased
- **THEN** installs, updates, and Box operations refuse to run until `mainboard-firmware.sh rescue` restores and verifies stock firmware

### Requirement: Rescue uses only admitted backups
Rescue SHALL transfer only with a backup whose bootloader and application are admitted. Without one, it MUST NOT stop services or program the board. If a pending record exists and the board enumerates as its running application, no transfer can have reached it, so rescue SHALL restore the recorded services and clear the record without programming.

#### Scenario: Rescue with only an unrecognized bootloader captured
- **WHEN** rescue runs and no backup of that board has an admitted bootloader
- **THEN** no service is stopped and no transfer is attempted
- **AND** a pending record left while the board still runs its application is cleared and its recorded services are started again
