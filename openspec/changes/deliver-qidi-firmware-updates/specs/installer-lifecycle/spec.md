## MODIFIED Requirements

### Requirement: Safe unattended updates
The update check SHALL be scheduled on every installation. Installing a release, starting a firmware update, or flashing a board unattended SHALL require durable operator enrollment; without it the check only publishes what is waiting. Unattended work SHALL use the same admission, ownership, recovery, activation, and persisted host-policy rules as direct installation, and advance release state only after successful activation.

#### Scenario: Enrolled release installation is idle and integrity checked
- **WHEN** an enrolled updater observes a new release checksum, a published release newer than the installed one, or missing or drifted optimized installation state
- **THEN** update proceeds only while the printer is idle, with no print running or paused and every heater target 0, and after archive integrity and path safety are verified
- **AND** missing or drifted state runs the current release installer even when its checksum is unchanged
- **AND** the release is downloaded and unpacked under the installer's cache in the printer user's home, never `/tmp`, and nothing of the download is left once the bundle is in place or the attempt fails
- **AND** the bundled noninteractive installer performs the change
- **AND** release state advances only after install and required Klipper activation succeed
- **AND** any host reboot is considered only after that state is durable and printer idleness is revalidated

#### Scenario: No-op and failure paths preserve release state
- **WHEN** release discovery fails, durable enrollment is absent, activation remains pending, or child installation fails
- **THEN** no release is falsely recorded as installed and no reboot is scheduled
- **AND** absent enrollment records the fetched checksum without installing whether checksum state is missing or changed
- **AND** a matching enrolled release reports current only after critical optimized installation state passes verification
- **AND** an already-current release reconciles the OS optimizations while idle

#### Scenario: Update plumbing is ownership-bound
- **WHEN** Valve Oil is installed, repaired, or uninstalled
- **THEN** only installer-owned scheduling, durable enrollment, and checksum state is created, repaired, or removed
- **AND** enrollment is recorded outside firmware-cleaned configuration
- **AND** an earlier release's automatic-update units, which it wrote only when automatic updates were turned on, keep the printer enrolled when an install replaces them
- **AND** uninstall removes enrollment before privileged timer cleanup so a surviving service cannot install or reconcile
- **AND** plumbing failure is reported separately from successful configuration or uninstall work

#### Scenario: A check without enrollment changes nothing
- **WHEN** the update check runs on a printer that is not enrolled
- **THEN** it publishes what is waiting and records the fetched checksum
- **AND** it installs no release, starts no firmware update, reconciles nothing, reads or flashes no board, starts, stops, or restarts no service, uses no sudo, and schedules no reboot

#### Scenario: Automatic updates are switched on or off
- **WHEN** the operator switches automatic updates in Fluidd, in Valve Oil UI, or with `auto-update.sh`
- **THEN** only the enrollment changes, without a password, and it takes effect at the next check
- **AND** the check stays scheduled either way

### Requirement: THR transfer is admitted and byte-verified
The THR transfer SHALL require supported firmware, idle ready state, zero heater targets, exclusive service ownership, and exact known THR application/bootloader identities. It MUST preserve stock recovery files before erase and verify the complete resulting application and unchanged bootloader before success. It MUST NOT change `printer.cfg`.

#### Scenario: Known stock or patched application is detected
- **WHEN** safe preconditions hold and readback matches an admitted image
- **THEN** stock firmware receives only the THR image pinned for the target firmware with retained recovery files
- **AND** an already-patched board is verified without another erase or transfer
- **AND** a board with an earlier build listed in the manifest receives the pinned patch of QIDI's stock THR image from the THR firmware backups or the printer's update folder, with that stock image as the transfer's recovery backup, and without one no erase or transfer occurs
- **AND** only previously active services are restored and printer readiness is checked

#### Scenario: Klipper is still restarting
- **WHEN** the operation starts while Klipper is restarting or Moonraker cannot yet report its state
- **THEN** the installer waits for Klipper to become ready before admission, allowing at least 60 seconds
- **AND** an active or paused print or a nonzero heater target fails admission without waiting
- **AND** if Klipper does not become ready in time, no service is stopped and no THR serial session or programming occurs

#### Scenario: Installed bootloader is unrecognized
- **WHEN** an update of an existing installation reads a THR bootloader that matches no admitted identity
- **THEN** the installer reports that this project's THR firmware is unavailable for this bootloader and THR firmware was not changed
- **AND** it asks the operator to attach the capture's shareable summary to a GitHub issue
- **AND** the read-only capture stays with the recovery files, only previously active services are restored, and no firmware blocker remains
- **AND** the configuration installation result is unaffected

#### Scenario: Interrupted transfer falls back to stock firmware
- **WHEN** a recovery-mode transfer reports that the stock fallback image was programmed
- **THEN** the installer verifies complete readback against the stock application, restores previous service states without a firmware blocker, keeps input shaper calibration, and reports that this project's THR firmware was not applied

#### Scenario: Admission fails or firmware work is interrupted
- **WHEN** firmware admission fails or firmware work does not reach verified readback
- **THEN** no unknown image is programmed and verified configuration installation is preserved
- **AND** potentially changed or unverified firmware retains recovery evidence, stopped serial-owning services, and a durable blocker against later installer operations
- **AND** configuration restore and uninstall never claim to revert MCU firmware

#### Scenario: Dry run
- **WHEN** the installer runs with `--dry-run`
- **THEN** it reports the proposed THR work without service changes, serial access, or firmware backup creation

### Requirement: Installs bring USB Boxes to the bundled Box firmware
After the rest of an install or update, including one run with `--yes` and the install an automatic update runs for a new release, the installer SHALL run the `box-firmware.sh install` flow for every configured USB Box without the `FLASH` prompt. An enrolled printer's update check SHALL run the same flow when a configured USB Box is not on the bundled firmware. Dry runs, uninstall, restore, and update checks on a printer that is not enrolled MUST NOT flash Box firmware.

#### Scenario: Install flashes Boxes that need it
- **WHEN** an install finishes on an idle printer with zero heater targets and a USB Box on QIDI's stock firmware or an earlier build of this project
- **THEN** that Box is backed up, flashed with the bundled firmware, and verified by readback without a question, Boxes already current are not flashed again, and previously active services are restored

#### Scenario: Automatic update installs a release
- **WHEN** an automatic update installs a new release on an idle printer with a USB Box that needs the bundled firmware
- **THEN** that Box is flashed and verified as for an install, and the automatic-update timer is running again afterward

#### Scenario: Update check finds a Box connected since the last release
- **WHEN** an enrolled printer's update check finds the release already installed and a configured USB Box on QIDI's stock firmware that `box-firmware.sh restore` did not put there
- **THEN** that Box is flashed and verified as for an install
- **AND** when that attempt stops before any write, later checks don't stop services for that Box again until it reports another version or another release is installed, and the waiting work says the next install tries again

#### Scenario: Automatic update finds a Box captured as unsupported
- **WHEN** an automatic update installs a release and the newest capture of a USB Box found a bootloader or application that release still does not admit
- **THEN** that Box is skipped without stopping services or capturing it again, and the output names the earlier capture's summary and where to open a support issue
- **AND** once a release admits what the capture found, the next automatic update that installs it flashes that Box

#### Scenario: Automatic updates and dry runs
- **WHEN** an update check finds the release already installed and every configured USB Box current or restored by the operator, or the printer is not enrolled, or the installer runs with `--dry-run`
- **THEN** no Box serial session, reset, or programming occurs

### Requirement: An install's Box flash never hides a failure
When the install's Box flow stops before any Box write, the install SHALL still succeed and SHALL report why the Boxes were not updated. When a Box write cannot be verified, the install SHALL fail with the rescue instructions.

#### Scenario: Install can't flash safely
- **WHEN** an install finishes while a heater target is nonzero, Klipper is not ready, or an earlier build needs QIDI's stock image and none is found
- **THEN** no Box is touched, the install succeeds, and the output says the Box firmware was not updated and why

#### Scenario: Install finds a Box it doesn't support yet
- **WHEN** an update of an existing installation leaves a USB Box unchanged because its bootloader or firmware is not admitted, or an automatic update skips it for an earlier such capture
- **THEN** the capture's shareable summary is copied to Klipper's log folder as `tltg_box_firmware_box<N>.json`, and from Klipper's next start `tltg_printer_warnings.warnings` lists a `warning` entry for that Box that names the file and where to open a support issue
- **AND** the copy, and with it the warning, is removed once an install finds that Box on the bundled firmware or the Box is no longer in `box.cfg`

#### Scenario: Install's Box flash fails
- **WHEN** a Box transfer or readback fails during an install or an automatic update's install after programming may have started
- **THEN** the install fails, Klipper, the touchscreen, and the automatic-update timer stay stopped, and the output names the `rescue` command

## REMOVED Requirements

### Requirement: THR firmware programming requires per-run opt-in
**Reason**: Every printer now receives this project's THR firmware, so the interactive question, the install-only option, and the rule that `--yes` and unattended updates never program MCU firmware no longer apply.
**Migration**: `qidi-firmware-updates` "Boards receive this project's firmware" covers when the THR is flashed and the input shaper warning that follows. The dry-run and `printer.cfg` guarantees move to "THR transfer is admitted and byte-verified". `--flash-thr-fifo` is accepted and ignored.

## RENAMED Requirements

- FROM: `### Requirement: Optional THR transfer is admitted and byte-verified`
- TO: `### Requirement: THR transfer is admitted and byte-verified`
