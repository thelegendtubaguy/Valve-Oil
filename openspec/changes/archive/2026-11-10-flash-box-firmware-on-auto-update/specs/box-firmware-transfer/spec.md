## MODIFIED Requirements

### Requirement: Only the board scripts and installs reach the transfer tools
`box-firmware.sh`, `mainboard-firmware.sh`, and, for USB Boxes only, the end of an install or update, including the install an automatic update runs, SHALL be the only callers of the transfer tools. Automatic updates MUST reach the Box tool only through the install flow and MUST NOT reach the mainboard tool, and `--yes` MUST NOT reach the mainboard tool.

#### Scenario: Automatic update
- **WHEN** an automatic update installs a release
- **THEN** the mainboard transfer tool does not run, and the Box transfer tool runs only to install the bundled Box firmware

### Requirement: Unrecognized builds are captured without reset
When readback finds a Box or mainboard bootloader or application that is not admitted, the tool SHALL keep a private read-only capture and a shareable summary without flash binaries or MCU unique identifiers, SHALL ask the operator to attach only that summary to a support issue, and MUST NOT reset, erase, or program that board.

#### Scenario: Unrecognized bootloader
- **WHEN** a board's bootloader matches no admitted build
- **THEN** that board is reported as not changed with the summary's location and where to open a support issue, and no reset, erase, or programming command is sent to it
