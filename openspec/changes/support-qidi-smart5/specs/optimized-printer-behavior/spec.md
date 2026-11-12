## ADDED Requirements

### Requirement: Smart 5 motion follows its own hardware
On the Smart 5, the optimized macros SHALL use the Smart 5's homing sequence, positions, cutter, wiper, scrape, and waste-chute coordinates under the same macro names as on the other printers, and SHALL issue no TMC driver or QIDI closed-loop driver command.

#### Scenario: Smart 5 homing uses QIDI's touches without staging moves
- **WHEN** X or Y homes on a Smart 5
- **THEN** X and Y don't move before they home, and a full or X/Y home homes X, then Y
- **AND** each axis takes QIDI's single touch at QIDI's homing current, then QIDI's checked second homing, then backs off 20 mm
- **AND** no `SET_TMC_CURRENT`, closed-loop homing mode, or enforce-mode command is sent

#### Scenario: Smart 5 nozzle moves stay within its travel
- **WHEN** the optimized cut, wipe, chute, scrape, park, or print-start moves run on a Smart 5
- **THEN** they use the Smart 5's coordinates, all inside its configured X −2–185 and Y −4–200 travel

#### Scenario: Smart 5 rear moves stay inside QIDI's chute range
- **WHEN** an optimized macro or a project Smart 5 slicer pack moves the toolhead behind Y 180 on a Smart 5
- **THEN** the toolhead stays between X 66 and X 100 for the whole move, including where it crosses Y 180

#### Scenario: Smart 5 rear-bed scrape follows the probed strip height
- **WHEN** the optimized print start scrapes the nozzle behind the bed on a Smart 5
- **THEN** it probes the strip once and scrapes 0.1 mm above the probed height, as QIDI's Smart 5 nozzle clean does

#### Scenario: Smart 5 speed and homing values stay at QIDI's
- **WHEN** install or update runs on a Smart 5
- **THEN** `max_velocity`, bed mesh `speed`, X/Y homing speeds and retract distances, and Z homing retract keep the Smart 5's own values

#### Scenario: Smart 5 slicer start keeps the prime line clear of the cutter
- **WHEN** a print sliced with the project's Smart 5 OrcaSlicer or QIDI Studio pack starts
- **THEN** its start path follows the Smart 5 start-print contract, with no chamber command, and the prime line starts no further right than X 160

### Requirement: Leveling steps run only on printers that have them
The optimized macros SHALL run `Z_TILT_ADJUST` only when Klipper has a `z_tilt` object, and the bed screw check only when screw positions are configured.

#### Scenario: Print start without Z tilt
- **WHEN** an optimized print start or bed soak test runs on a printer without `[z_tilt]`
- **THEN** it skips the Z tilt step and its sub-status and continues to the bed mesh

#### Scenario: Bed screw check without screw positions
- **WHEN** `TLTG_CORNER_BED_SCREW_CHECK` runs on a printer without `[screws_tilt_adjust]`
- **THEN** it stops before homing and says the printer has no bed screw positions set up
