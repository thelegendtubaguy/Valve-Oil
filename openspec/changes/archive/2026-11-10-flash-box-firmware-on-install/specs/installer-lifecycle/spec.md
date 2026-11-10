## REMOVED Requirements

### Requirement: Box firmware runs only through its dedicated script
**Reason**: Installs and updates now flash USB Boxes, so Anycubic and Elegoo tags read without a separate SSH step.
**Migration**: Replaced by "Box firmware script actions" and "Installs bring USB Boxes to the bundled Box firmware". Automatic updates still never flash a Box.

## ADDED Requirements

### Requirement: Box firmware script actions
The installer bundle SHALL provide `box-firmware.sh` with `status`, `install`, `restore`, and `rescue` actions, and `status` SHALL be read-only.

#### Scenario: Status
- **WHEN** the operator runs `box-firmware.sh status`
- **THEN** each configured USB Box's firmware is reported and no Box serial session, service change, or programming occurs

### Requirement: Installs bring USB Boxes to the bundled Box firmware
After the rest of an install or update, including one run with `--yes`, the installer SHALL run the `box-firmware.sh install` flow for every configured USB Box without the `FLASH` prompt. Automatic updates, dry runs, uninstall, and restore MUST NOT flash Box firmware.

#### Scenario: Install flashes Boxes that need it
- **WHEN** an install finishes on an idle printer with zero heater targets and a USB Box on QIDI's stock firmware or an earlier build of this project
- **THEN** that Box is backed up, flashed with the bundled firmware, and verified by readback without a question, Boxes already current are not flashed again, and previously active services are restored

#### Scenario: Automatic updates and dry runs
- **WHEN** an automatic update installs a release or the installer runs with `--dry-run`
- **THEN** no Box serial session, reset, or programming occurs

### Requirement: An install's Box flash never hides a failure
When the install's Box flow stops before any Box write, the install SHALL still succeed and SHALL report why the Boxes were not updated. When a Box write cannot be verified, the install SHALL fail with the rescue instructions.

#### Scenario: Install can't flash safely
- **WHEN** an install finishes while a heater target is nonzero, Klipper is not ready, or an earlier build needs QIDI's stock image and none is found
- **THEN** no Box is touched, the install succeeds, and the output says the Box firmware was not updated and why

#### Scenario: Install's Box flash fails
- **WHEN** a Box transfer or readback fails during an install after programming may have started
- **THEN** the install fails, Klipper and the touchscreen stay stopped, and the output names the `rescue` command
