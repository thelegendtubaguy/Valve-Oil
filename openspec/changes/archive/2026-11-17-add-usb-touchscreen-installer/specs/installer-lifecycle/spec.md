## ADDED Requirements

### Requirement: Install from a USB drive on the touchscreen
Each release SHALL publish one USB drive image that an operator extracts to a USB drive and starts from the touchscreen of any supported printer, without SSH or a network. It SHALL run the package's install on the touchscreen with the same firmware gate, printer-state checks, backup, rollback, and firmware safeguards as an install started over SSH, and SHALL return the touchscreen to QIDI's app when the operator quits.

#### Scenario: First install from USB
- **WHEN** an operator starts the USB installer on a supported printer without Valve Oil and confirms the summary
- **THEN** the printer ends with the same installed state as an SSH install with the same answers, and the finish screen says what to do next

#### Scenario: One drive for every printer
- **WHEN** the same drive is plugged into a Max 4, a Plus 5, or a Smart 5
- **THEN** that printer's offline update page lists only the Valve Oil package for that printer

#### Scenario: Printer already has Valve Oil
- **WHEN** an operator starts the USB installer on a printer with Valve Oil installed
- **THEN** the first screen shows the installed version and the drive's version, and offers updating with the install's current settings

#### Scenario: Unsupported firmware or busy printer
- **WHEN** the printer's firmware is unsupported, a print is running or paused, or a heater has a target
- **THEN** the installer changes nothing in the package's managed state, says why on the touchscreen, and returns the touchscreen to QIDI's app when the operator quits

#### Scenario: Install finishes
- **WHEN** a USB install finishes
- **THEN** the log is on the drive before the finish screen shows, and the printer restarts when the drive is taken out or the operator chooses to restart with it in

#### Scenario: Older supported firmware with no network
- **WHEN** a USB install runs on a printer whose QIDI firmware the release updates from and the printer has no network
- **THEN** the QIDI firmware update uses the package on the drive once it matches the pinned size and SHA-256, and the install continues as it would after a downloaded update

#### Scenario: A board is not admitted
- **WHEN** a new install from USB finds a board the release does not admit
- **THEN** nothing is installed, and the touchscreen names the board and shows the printer-support issue link, filled in as an SSH install's is, as a code a phone can scan

### Requirement: USB installs keep QIDI's update folder
A run started from a USB drive on a supported firmware SHALL leave QIDI's update folder holding QIDI's firmware manifest and board images for that firmware, whether the operator installs, quits, or the run fails.

#### Scenario: Operator quits a USB run
- **WHEN** an operator starts the USB installer on a supported firmware and quits without installing
- **THEN** QIDI's update folder holds QIDI's manifest and board images for the printer's firmware

#### Scenario: The installer doesn't start
- **WHEN** QIDI's app has installed the drive's package but the installer fails to start
- **THEN** QIDI's update folder was already restored before the start was attempted

#### Scenario: Firmware with no restore set
- **WHEN** the drive's package runs on a printer whose QIDI firmware the drive carries no files for
- **THEN** the touchscreen says QIDI's update folder was emptied and how to get QIDI's files back, and nothing else changes

### Requirement: Installs ask first and then run unattended
An install started from the touchscreen or over SSH SHALL show the detected printer, QIDI firmware, and installed package version, ask every question that applies, and show one summary of the answers. It MUST NOT change the package's managed state before the operator confirms the summary, and MUST NOT need input from then until the finish screen.

#### Scenario: Operator quits before confirming
- **WHEN** an operator answers some or all questions and quits before confirming the summary
- **THEN** the package's managed state, configuration, and system changes are as they were before the installer started

#### Scenario: Step fails after confirmation
- **WHEN** a step fails after the operator confirmed the summary
- **THEN** the installer applies the same rollback as an SSH install, and the touchscreen names the failed step, says what state the printer is in, and says where the log is

### Requirement: The touchscreen shows install progress
While an install or update holds the touchscreen, whether started from a USB drive, over SSH, or as an automatic update, the screen SHALL show the current step by name, its place among the run's steps, the elapsed time, and how the run was started, with system changes and each board firmware flash as steps of their own.

#### Scenario: Box firmware is flashed
- **WHEN** a touchscreen install flashes a USB Box
- **THEN** the screen names that Box's firmware as the current step for as long as the flash runs

#### Scenario: Automatic update
- **WHEN** an automatic update holds the touchscreen
- **THEN** the screen says the printer is updating itself, from which version to which, and shows the current step

### Requirement: Installer runs keep a log
Every install, update, and uninstall run SHALL write a log of what it detected, decided, and did to the printer's log folder, where it can be downloaded without SSH. A run started from a USB drive SHALL also save that log to the drive when it finishes or fails.

#### Scenario: Run fails
- **WHEN** an install started over SSH or from a USB drive fails
- **THEN** the failure message names the log, and the log holds the failed step and its error

## MODIFIED Requirements

### Requirement: Non-owning QIDI Box reconciliation
The installer SHALL keep an available QIDI Box usable without claiming ownership of vendor saved variables or silently replacing existing non-empty tool mappings. Box availability SHALL be recognized for every supported firmware's Box transport, including the legacy USB Box configuration and the RS-485 Box stack.

#### Scenario: Optional Box state is reconciled conservatively
- **WHEN** the Box stack is available
- **THEN** a new install turns Box support on without asking, and a later install or update leaves Box support as the operator set it
- **AND** missing active mappings are created as identity mappings
- **AND** existing non-empty mappings are left as they are, and no run asks about them

#### Scenario: Box availability is transport-independent
- **WHEN** a QIDI Box is attached through either the legacy USB configuration or the RS-485 Box stack
- **THEN** Box enablement and tool-mapping reconciliation treat the Box as available
- **AND** a printer with no attached Box is not treated as Box-capable solely because firmware always loads Box controller objects

#### Scenario: Automatic reconciliation preserves vendor state
- **WHEN** noninteractive install or update observes Box topology while the printer is idle
- **THEN** missing active mappings are created and existing non-empty mappings are preserved
- **AND** busy, unavailable, or not-ready Klipper state causes no reconciliation writes
- **AND** every write uses Klipper's live `SAVE_VARIABLE` command through Moonraker, verifies its live value and, when a source-patch restart occurs, verifies it again after the replacement process; it never restores a whole saved-variable file during rollback
- **AND** saved-variable changes are excluded from installer ownership and uninstall
