## MODIFIED Requirements

### Requirement: Installs bring USB Boxes to the bundled Box firmware
After the rest of an install or update, including one run with `--yes` and the install an automatic update runs for a new release, the installer SHALL run the `box-firmware.sh install` flow for every configured USB Box without the `FLASH` prompt. Dry runs, uninstall, restore, and automatic update checks that install nothing MUST NOT flash Box firmware.

#### Scenario: Install flashes Boxes that need it
- **WHEN** an install finishes on an idle printer with zero heater targets and a USB Box on QIDI's stock firmware or an earlier build of this project
- **THEN** that Box is backed up, flashed with the bundled firmware, and verified by readback without a question, Boxes already current are not flashed again, and previously active services are restored

#### Scenario: Automatic update installs a release
- **WHEN** an automatic update installs a new release on an idle printer with a USB Box that needs the bundled firmware
- **THEN** that Box is flashed and verified as for an install, and the automatic-update timer is running again afterward

#### Scenario: Automatic update finds a Box captured as unsupported
- **WHEN** an automatic update installs a release and the newest capture of a USB Box found a bootloader or application that release still does not admit
- **THEN** that Box is skipped without stopping services or capturing it again, and the output names the earlier capture's summary and where to open a support issue
- **AND** once a release admits what the capture found, the next automatic update that installs it flashes that Box

#### Scenario: Automatic updates and dry runs
- **WHEN** an automatic update check finds the release already installed, or the installer runs with `--dry-run`
- **THEN** no Box serial session, reset, or programming occurs

### Requirement: An install's Box flash never hides a failure
When the install's Box flow stops before any Box write, the install SHALL still succeed and SHALL report why the Boxes were not updated. When a Box write cannot be verified, the install SHALL fail with the rescue instructions.

#### Scenario: Install can't flash safely
- **WHEN** an install finishes while a heater target is nonzero, Klipper is not ready, or an earlier build needs QIDI's stock image and none is found
- **THEN** no Box is touched, the install succeeds, and the output says the Box firmware was not updated and why

#### Scenario: Install finds a Box it doesn't support yet
- **WHEN** an install leaves a USB Box unchanged because its bootloader or firmware is not admitted, or an automatic update skips it for an earlier such capture
- **THEN** the capture's shareable summary is copied to Klipper's log folder as `tltg_box_firmware_box<N>.json`, and from Klipper's next start `tltg_printer_warnings.warnings` lists a `warning` entry for that Box that names the file and where to open a support issue
- **AND** the copy, and with it the warning, is removed once an install finds that Box on the bundled firmware or the Box is no longer in `box.cfg`

#### Scenario: Install's Box flash fails
- **WHEN** a Box transfer or readback fails during an install or an automatic update's install after programming may have started
- **THEN** the install fails, Klipper, the touchscreen, and the automatic-update timer stay stopped, and the output names the `rescue` command
