## ADDED Requirements

### Requirement: Valve Oil touchscreen replaces QIDI's
Where the Valve Oil touchscreen app is installed, it SHALL be the app on the printer's touchscreen across reboots and package updates, and the package MUST NOT offer a control that swaps it with QIDI's app. QIDI's app SHALL stay on the printer unmodified, and uninstall SHALL return the touchscreen to it.

#### Scenario: Installed
- **WHEN** an install that includes the Valve Oil touchscreen finishes and the printer reboots
- **THEN** the touchscreen shows the Valve Oil app, and QIDI's app is still on the printer unmodified

#### Scenario: QIDI firmware update
- **WHEN** a QIDI firmware update replaces QIDI's app or re-enables its service
- **THEN** the next install or update check puts the Valve Oil app back on the touchscreen

#### Scenario: Uninstall
- **WHEN** the Valve Oil touchscreen is uninstalled
- **THEN** QIDI's app takes the touchscreen and no Valve Oil touchscreen service remains

### Requirement: Printer functions outside the screen keep working
With the Valve Oil touchscreen installed, functions QIDI's app performs for the printer besides drawing the screen SHALL keep working, except those the docs list as not provided by the Valve Oil touchscreen.

#### Scenario: QIDI Box is connected
- **WHEN** an operator connects a QIDI Box to a printer with the Valve Oil touchscreen installed
- **THEN** the Box becomes usable as it would on a printer with QIDI's app on the screen

### Requirement: Prints run from the touchscreen
The Valve Oil app SHALL list print files with their time and filaments, start a chosen file, and show a running print's progress, time left, and feeding slot. It SHALL pause, resume, and tune a print, and SHALL cancel one only after a deliberate confirmation. During a print start it SHALL show the start's stages by name with the current one marked.

#### Scenario: Print started from the touchscreen
- **WHEN** an operator starts a file from the Valve Oil app
- **THEN** the print starts as it would from Fluidd, and the screen shows the start's stages and then the print's progress

#### Scenario: Accidental cancel
- **WHEN** an operator touches Cancel during a print without confirming
- **THEN** the print continues

### Requirement: Print options on the touchscreen
Before starting a file, the Valve Oil app SHALL let an operator choose the bed mesh (adaptive, full bed, or any mesh saved in Klipper, by name) and, for that print, timelapse and the polar cooler on printers that have one, starting from the choices made for the previous print. While printing, Tune SHALL set the heater targets, speed, flow, Z offset, and the part, aux, and exhaust fans the printer has.

#### Scenario: Box slots or the external spool
- **WHEN** an operator starts a file from the Valve Oil app with each filament matched to a Box slot or the external spool
- **THEN** the app saves each tool's slot and whether the print uses the Box before the print starts, as QIDI's app does, and refuses to start when a filament is matched to an external spool with no filament detected

#### Scenario: Saved mesh chosen
- **WHEN** an operator picks a saved mesh by name and starts the print
- **THEN** the print uses that mesh and probes no other

### Requirement: Continue from another slot after a runout
When a print pauses because a Box slot ran out, the Valve Oil app SHALL show which slot ran out, suggest a slot holding the same filament, and let the operator continue from any loaded slot or load a new spool into the empty one.

#### Scenario: Operator picks another slot
- **WHEN** an operator continues a paused print from another slot
- **THEN** the print resumes feeding from that slot, and Fluidd shows that slot as the one feeding

### Requirement: Moving and homing on the touchscreen
The Valve Oil app SHALL home X, Y, and Z each on their own and all together. Its Z controls SHALL call moving the bed toward the nozzle up and away from it down, and SHALL send Z all the way up and all the way down, to 10 mm short of max Z, in one touch each.

#### Scenario: Home Z before X and Y
- **WHEN** an operator touches Home Z while X or Y is not homed
- **THEN** the printer homes X and Y first, then Z

### Requirement: QIDI Box slots on the touchscreen
The Valve Oil app SHALL show each QIDI Box slot's filament, color, and state as Fluidd shows them, and SHALL let an operator set a slot's filament and load or unload a slot. The materials and brands it offers SHALL be the lists edited in Valve Oil UI.

#### Scenario: Slot changed on the touchscreen
- **WHEN** an operator sets a slot's filament from the Valve Oil app
- **THEN** Fluidd shows the same filament for that slot

### Requirement: Klipper's stop reason on the touchscreen
When Klipper stops with an error, the Valve Oil app SHALL show Klipper's own message and the action that restarts it.

#### Scenario: Klipper shuts down during a print
- **WHEN** Klipper shuts down with the Valve Oil app on the touchscreen
- **THEN** the touchscreen shows the shutdown reason Klipper reported and offers a firmware restart

### Requirement: The touchscreen app yields to printing
The Valve Oil app SHALL run with lower priority than the print-critical services, off the CPUs reserved for Klipper, and within a CPU and memory limit.

#### Scenario: Print with the Valve Oil touchscreen installed
- **WHEN** a print runs with the Valve Oil app on the touchscreen
- **THEN** the app's service stays within its limits and off Klipper's CPUs
