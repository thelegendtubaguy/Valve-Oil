## MODIFIED Requirements

### Requirement: Board serial operations require explicit safe ownership
The Box and mainboard transfer tools SHALL refuse serial access unless the operator confirms the printer is idle and Klipper, the touchscreen client, and the automatic-update timer are stopped. They MUST NOT modify printer configuration, control heaters or motion, or flash any board other than the addressed USB Box or the mainboard. `box-firmware.sh`, `mainboard-firmware.sh`, and, for USB Boxes only, the end of an install or update reach them; automatic updates never do, and `--yes` never reaches the mainboard tool.

#### Scenario: Serial ownership is unsafe
- **WHEN** idle confirmation is absent or a required service is still running
- **THEN** the tool fails before opening the board's serial port

#### Scenario: Automatic update
- **WHEN** an automatic update installs a release
- **THEN** neither transfer tool runs
