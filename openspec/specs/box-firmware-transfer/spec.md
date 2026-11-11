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
`box-firmware.sh`, `mainboard-firmware.sh`, and, for USB Boxes only, the end of an install or update, including the install an automatic update runs, SHALL be the only callers of the transfer tools. Automatic updates MUST reach the Box tool only through the install flow and MUST NOT reach the mainboard tool, and `--yes` MUST NOT reach the mainboard tool.

#### Scenario: Automatic update
- **WHEN** an automatic update installs a release
- **THEN** the mainboard transfer tool does not run, and the Box transfer tool runs only to install the bundled Box firmware

### Requirement: Transfers admit only known images and bootloaders
The tools SHALL program only the pinned stock or patched application for that board, and only through a bootloader whose exact build has been validated, using backups of that board's bootloader and application. The mainboard fix is offered only on the Max 4. An erasing transfer MUST require explicit write confirmation. It MUST NOT resend the start command or an unacknowledged first data packet, because the bootloader's write position only rewinds at reset.

#### Scenario: Known image through an admitted bootloader
- **WHEN** the image, backups, and write confirmation are valid and the installed bootloader matches its backup
- **THEN** the tool resets the board into its bootloader and transfers the image in acknowledged 1 KiB packets

#### Scenario: Unknown input
- **WHEN** the image, backup, confirmation, or bootloader identity is not admitted
- **THEN** no reset or erase command is sent

### Requirement: Earlier Box and mainboard builds are updated from QIDI's stock image
A Box or mainboard application that matches an earlier build of this project listed in the manifest SHALL be admitted for backups and replaced, and its patched image SHALL be built from QIDI's stock application for that board found in that board's firmware backups or the printer's update folder.

#### Scenario: Earlier Valve Oil Box firmware is updated
- **WHEN** `box-firmware.sh install` finds a Box running an earlier build of this project
- **THEN** it builds the current image from QIDI's verified stock application and installs it
- **AND** with no verified stock application available it stops before any service is stopped or Box is reset

#### Scenario: Earlier mainboard fix is updated
- **WHEN** `mainboard-firmware.sh install` finds the mainboard reporting an earlier build of the closed-loop reply fix
- **THEN** it builds the current image from QIDI's verified stock mainboard application and installs it
- **AND** with no verified stock application available it stops before any service is stopped or the mainboard is reset

### Requirement: Installation evidence uses readback
A Box or mainboard flash SHALL be reported as installed only when a full application readback matches the selected image and the bootloader readback matches the pre-transfer backup. Transfer acknowledgements and the reported version string are not installation evidence.

#### Scenario: Readback differs
- **WHEN** the application readback differs from the image or the bootloader changed
- **THEN** verification fails regardless of acknowledgements or version

### Requirement: Unrecognized builds are captured without reset
When readback finds a Box or mainboard bootloader or application that is not admitted, the tool SHALL keep a private read-only capture and a shareable summary without flash binaries or MCU unique identifiers, SHALL ask the operator to attach only that summary to a support issue, and MUST NOT reset, erase, or program that board.

#### Scenario: Unrecognized bootloader
- **WHEN** a board's bootloader matches no admitted build
- **THEN** that board is reported as not changed with the summary's location and where to open a support issue, and no reset, erase, or programming command is sent to it

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
