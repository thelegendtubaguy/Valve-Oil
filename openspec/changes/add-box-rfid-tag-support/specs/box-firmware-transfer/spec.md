## MODIFIED Requirements

### Requirement: Transfers admit only known images and bootloaders
The tools SHALL program only the pinned stock or patched application for that board, and only through a bootloader whose exact build has been validated, using backups of that board's bootloader and application. A Box application that matches an earlier TLTG build listed in the manifest SHALL be admitted for backups and replaced, and its patched image SHALL be built from QIDI's stock application found in the Box firmware backups or the printer's update folder. The mainboard fix is offered only on the Max 4. An erasing transfer MUST require explicit write confirmation. It MUST NOT resend the start command or an unacknowledged first data packet, because the bootloader's write position only rewinds at reset.

#### Scenario: Known image through an admitted bootloader
- **WHEN** the image, backups, and write confirmation are valid and the installed bootloader matches its backup
- **THEN** the tool resets the board into its bootloader and transfers the image in acknowledged 1 KiB packets

#### Scenario: Unknown input
- **WHEN** the image, backup, confirmation, or bootloader identity is not admitted
- **THEN** no reset or erase command is sent

#### Scenario: Earlier TLTG Box firmware is updated
- **WHEN** `box-firmware.sh install` finds a Box running an earlier TLTG build
- **THEN** it builds the current image from QIDI's verified stock application and installs it
- **AND** with no verified stock application available it stops before any service is stopped or Box is reset
