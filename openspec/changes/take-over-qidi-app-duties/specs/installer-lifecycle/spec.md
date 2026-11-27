## MODIFIED Requirements

### Requirement: Recoverable host optimization
Every install and update SHALL apply the host OS optimizations without asking the operator about them, persist that policy, preserve recoverable preimages, and keep host-operation failures separate from a verified printer-configuration result. System optimizations SHALL remain available through the same packaged install, update, and uninstall flow without requiring a separate package or operator command.

#### Scenario: Every install and update applies the OS optimizations
- **WHEN** an install, `--yes` install, automatic update, or already-current automatic-update check runs while the printer is idle
- **THEN** it applies the OS optimizations even when an earlier install recorded declining them, and records them as enabled

#### Scenario: The unauthenticated ADB root shell is off the network
- **WHEN** install, update, or automatic-update reconciliation applies the OS optimizations
- **THEN** QIDI's ADB daemon no longer listens on any network after the operation or after reboot, and a running `usbdevice.service` keeps serving ADB on the board's USB port
- **AND** a `usbdevice.service` an earlier package version stopped and disabled is put back to its recorded state once the network port is closed, and leaves the restoration record
- **AND** uninstall reopens the network port only when the operator accepts restoring the OS optimizations

#### Scenario: Enabled policy reconciles only recognized host state
- **WHEN** system optimizations are enabled
- **THEN** each declared operation validates its live preconditions before mutation
- **AND** installer-owned drift is reconciled without replacing first restore preimages
- **AND** unowned, unknown, or user-modified state is preserved and reported
- **AND** operation failure rolls back journaled host work without deleting a verified configuration install
- **AND** incomplete host compensation retains a recovery blocker and does not report the overall operation as complete
- **AND** host backups are flushed to storage before a journal or installed state names them
- **AND** a host backup that is missing or no longer matches its recorded hash is never restored and never blocks recovery, install, or uninstall; the file it backed up keeps what it has
- **AND** when compensation fails, the reported error names the failure that started it as well as the compensation's own failure, and the retained recovery record keeps that original failure, so the later run that finishes or retries the compensation reports it too

#### Scenario: A stopped run never blocks the next
- **WHEN** an install or uninstall stops at any point without its rollback, from a power cut, a restart, or the SSH session closing
- **THEN** running it again finishes it and leaves the printer as a run that never stopped would
- **AND** a hangup of the terminal does not stop a run; its terminal output is dropped and it goes on to its end and its log
- **AND** a new install records that it started before its first change and clears the record once its state is written; Valve Oil's files with that record and no state are finished as its own, never reset as a hand-copied install, and count as an existing installation; uninstall then says to finish the install first
- **AND** a section delete found already applied with no record of its original records QIDI's section text from the bundled stock config, and uninstall puts that text back for a record that holds only the section's hash, or leaves the section deleted when the bundle has no copy
- **AND** uninstall goes on when the managed tree is already removed
- **AND** a pending Klipper restart whose record can't be read or no longer matches the files restarts Klipper and clears the record
- **AND** an earlier run's saved-variable check that no longer matches Klipper is reported and cleared, while the run that saved the values still fails on a mismatch
- **AND** a host journal that can't be read or acted on is set aside unchanged, and one whose rollback fails is retried by the next runs and set aside after the third attempt, leaving the host as it is
- **AND** an invalid legacy ledger migration journal is dropped without removing anything it names
- **AND** while migrating names from before 26.11.22.3, an active earlier automatic-update or firmware-update service that started the installer is left in place until it exits; inactive earlier service files are retired

#### Scenario: Uninstall follows the operator's host-state decision
- **WHEN** uninstall finds host restore preimages
- **THEN** it reverts only unchanged installer-owned targets: always for host changes that normal install makes independently of optional OS optimization policy, and for optional OS optimizations only when the operator accepts restoration
- **AND** the operator is asked only when optional OS optimizations have restore preimages
- **AND** targets already at their retained preimages are left unchanged
- **AND** user-modified files, symlinks, assets, or service states are preserved and reported
- **AND** declined restoration or explicit keep policy leaves optional OS optimizations unchanged
- **AND** before its idle check and pending Klipper restart, uninstall, like install, waits a bounded time for Moonraker to report printer state again, since restoring Moonraker source restarts it

#### Scenario: Changed services restart once
- **WHEN** an install, update, uninstall, or host recovery changes files that a running service loads
- **THEN** every change in the operation is in place before any service restarts, and each changed service restarts once
- **AND** adding or removing package services reloads systemd once, before those restarts, rather than once per service
- **AND** a failed restart that a change requires fails the operation like any other host-operation failure
- **AND** after restarting Moonraker, the installer waits a bounded time for Moonraker to answer and then restarts QIDI's touchscreen app, so the app does not keep showing a Moonraker outage
- **AND** a Klipper restart already pending for source activation stands in for the host change's own Klipper restart
- **AND** a step that rollback cannot undo, such as retiring QIDI bundle patches once the package Fluidd is served, runs only after the restarts before it succeed

#### Scenario: Host changes report their progress
- **WHEN** an install, update, or uninstall applies or restores host changes
- **THEN** they say the step can take a few minutes, install and update name each change as they apply it, and every operation names each service as it restarts it

#### Scenario: Synchronous root writes are repaired first
- **WHEN** enabled OS optimizations repair QIDI's Rockchip service that remounts the root filesystem with `sync`
- **THEN** the repair runs before the other host changes in the operation, so their file writes are not synchronous
- **AND** its recovery record survives the later host changes until the installed state records the repair, so an interruption before then still rolls it back

#### Scenario: Reconciliation retains bounded recovery state
- **WHEN** repeated checks find host optimizations already current
- **THEN** installer recovery-state size and restoration-backup count do not grow with the number of checks
- **AND** first restoration preimages and unresolved transaction or reboot evidence remain available
- **AND** previously installed host ledgers remain recoverable when their historical action records are compacted

#### Scenario: Host reboot is deferred until safe
- **WHEN** an applied operation requires a host reboot
- **THEN** the requirement is persisted without embedding executable commands
- **AND** reboot is scheduled only after successful transaction completion and a fresh idle-printer check, and for uninstall only with explicit authorization
- **AND** later execution clears the requirement only after post-boot verification succeeds
- **AND** dry-run, active, or unknown printer state performs no reboot
