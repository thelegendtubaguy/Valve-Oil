## MODIFIED Requirements

### Requirement: Only the board scripts and installs reach the transfer tools
`box-firmware.sh`, `mainboard-firmware.sh`, and, for USB Boxes only, the end of an install or update, including the install an automatic update runs, SHALL be the only callers of the transfer tools. Automatic updates MUST reach the Box tool only through the install flow and MUST NOT reach the mainboard tool, and `--yes` MUST NOT reach the mainboard tool.

#### Scenario: Automatic update
- **WHEN** an automatic update installs a release
- **THEN** the mainboard transfer tool does not run, and the Box transfer tool runs only to install the bundled Box firmware
