## MODIFIED Requirements

### Requirement: Only the board scripts and installs reach the transfer tools
`box-firmware.sh`, `mainboard-firmware.sh`, and the board step of an install, update, or firmware update, including one an automatic update runs, SHALL be the only callers of the transfer tools. An install, `--yes`, and automatic updates MUST reach the tools only through that step and only to install the image pinned for the printer's target firmware.

#### Scenario: Automatic update
- **WHEN** an automatic update installs a release or finds a board that is not on its pinned image
- **THEN** the Box and mainboard transfer tools run only to install the pinned images, and never to restore or rescue

### Requirement: Transfers admit only known images and bootloaders
The tools SHALL program only the stock or patched application pinned for that board and the printer's target firmware, and only through a bootloader whose exact build has been validated, using backups of that board's bootloader and application. This project's mainboard build is offered only on the Max 4; a Plus 5 mainboard receives only QIDI's stock image. An erasing transfer MUST require explicit write confirmation. It MUST NOT resend the start command or an unacknowledged first data packet, because the bootloader's write position only rewinds at reset.

#### Scenario: Known image through an admitted bootloader
- **WHEN** the image, backups, and write confirmation are valid and the installed bootloader matches its backup
- **THEN** the tool resets the board into its bootloader and transfers the image in acknowledged 1 KiB packets

#### Scenario: Unknown input
- **WHEN** the image, backup, confirmation, or bootloader identity is not admitted
- **THEN** no reset or erase command is sent

### Requirement: Earlier Box and mainboard builds are updated from QIDI's stock image
A Box or mainboard application that matches an earlier build of this project listed in the manifest, or QIDI's image of an earlier board version listed in the manifest as a predecessor by exact size and SHA-256, SHALL be admitted for backups and replaced with the image pinned for the printer's target firmware, made from QIDI's stock application for that board found in that board's firmware backups or the printer's update folder; rescue restores that stock application. A board on a predecessor SHALL be replaced only while the printer runs the target firmware, because the flash changes the board's version and the installed Klipper may not support it. A predecessor image MUST NOT be programmed.

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
