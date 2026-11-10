## ADDED Requirements

### Requirement: Bed soak test
Optimized configuration SHALL provide an operator-run bed soak test that sets up as print start does, measures one full-bed mesh with the bed and chamber heaters off, then heats the bed and, optionally, the chamber and measures further meshes until it ends. The nozzle SHALL stay at print start's probing temperature throughout, and Z tilt adjust SHALL run only during setup.

#### Scenario: Test runs to the end
- **WHEN** an operator starts the test while the printer is idle, giving a bed target, an optional chamber target, a mesh size, a time limit, whether to stop once the bed stops moving, and whether to keep heating afterwards
- **THEN** the nozzle heats to print start's probing temperature, and the printer homes, wipes the nozzle at the chute, runs Z tilt adjust, homes Z again, and measures one mesh, all before the bed and chamber heaters turn on
- **AND** the bed and chamber temperatures, the step the test is on, and each new mesh's results are available live while it runs
- **AND** with stopping enabled, the test ends once the bed has stopped moving and the chamber, if heated, has reached its wait temperature or stopped warming. Otherwise it ends at the time limit
- **AND** the run's settings, temperatures, meshes, and results are saved as a run file that stays after the test

#### Scenario: Test is refused
- **WHEN** a print is running or paused, a test is already running, a target is outside that heater's limits, or a chamber target is given on a printer without a chamber heater
- **THEN** nothing moves or heats, and the console says why

#### Scenario: Operator stops the test
- **WHEN** an operator stops the test
- **THEN** the heaters the test turned on turn off at once, unless the operator asked to keep heating, and the test ends no later than the end of the mesh being measured
- **AND** the results so far are reported and saved

#### Scenario: Something else takes over the printer
- **WHEN** a print starts, or something other than the test changes the bed, chamber, or nozzle target, while the test runs
- **THEN** the test ends, leaves the heaters as they now are, and says why

#### Scenario: Homing or probing fails
- **WHEN** homing, Z tilt adjust, or a probe fails during the test
- **THEN** the test ends, its heaters turn off, and the console and the saved run say what failed

### Requirement: Bed soak results
The bed soak test SHALL report, counting from when the heaters turned on, when the bed reached print start's bed wait temperature, when the chamber reached print start's chamber wait temperature, when the bed stopped moving, and when the printer was ready to print, the later of the chamber and stopped-moving times. For each mesh it SHALL report the whole bed's height change since the first mesh, its shape change with that height change removed, and its speed of change since the previous mesh.

#### Scenario: Bed stops moving
- **WHEN** after the bed reaches its wait temperature, the bed's speed stays under the stopped-moving limit for three meshes in a row through the end of the test
- **THEN** the stopped-moving time is the first mesh of that stretch

#### Scenario: Bed starts moving again
- **WHEN** the bed's speed rises back over the stopped-moving limit after three meshes under it
- **THEN** the stopped-moving time is cleared and taken again from the next stretch under the limit

#### Scenario: Chamber can't reach its target
- **WHEN** the chamber stops warming before it reaches its wait temperature
- **THEN** the results name the highest chamber temperature reached instead of a chamber time, and the bed's times are still reported

### Requirement: Bed soak problem points
The bed soak test SHALL flag probe points and meshes whose readings can't be trusted, show them with the results, and keep them out of the reported bed movement.

#### Scenario: Point needs a retry
- **WHEN** a point's probe samples disagree and the probe retries
- **THEN** that point is flagged in that mesh with its number of tries and how far apart its samples were

#### Scenario: Point out of line with the bed
- **WHEN** a point's change between two meshes taken after the bed reached its wait temperature differs from the change of the points around it by more than the out-of-line limit
- **THEN** that point is flagged in that mesh, and it doesn't change the mesh's speed

#### Scenario: Probe readings jump
- **WHEN** after the bed reached its wait temperature, the check point at the bed centre, probed between meshes, moves during one mesh by more than the jump limit beyond the movement of the meshes before and after it
- **THEN** that mesh isn't used, heights after it are corrected by the size of the jump, and the results say when the jump happened and how large it was

#### Scenario: Bed heating unevenly
- **WHEN** the bed moves unevenly while it heats, before it reaches its wait temperature
- **THEN** no point is flagged out of line and no jump is found for those meshes

#### Scenario: Results with problems
- **WHEN** the test ends with a point flagged in more than one mesh, or after a jump
- **THEN** the results open with a warning that names the points and the jump and suggests checking the plate and running the test again

### Requirement: Probe retries stop at the configured limit
A probe point whose samples keep disagreeing SHALL be retried at most `samples_tolerance_retries` times and then fail with `Probe samples exceed samples_tolerance`, as upstream Klipper does. The load cell SHALL be re-zeroed for each retry after the nozzle has lifted off the bed. The fix SHALL apply only when QIDI's probe sampling code matches the vendor original it was written for, and SHALL be logged like the other Klipper fixes.

#### Scenario: Samples never agree
- **WHEN** a point's samples still disagree after `samples_tolerance_retries` retries, during any probe, mesh, or Z tilt adjust
- **THEN** the command fails with `Probe samples exceed samples_tolerance` instead of probing that point forever

#### Scenario: Samples agree after a retry
- **WHEN** a point's samples agree within that number of retries
- **THEN** each retry's samples are measured from the bed, not from where the nozzle lifted to, and probing carries on

#### Scenario: Changed vendor probe code
- **WHEN** QIDI's probe sampling code differs from the original the fix was written for
- **THEN** it is left unchanged and `klippy.log` says the fix was skipped

### Requirement: Problem points from any bed mesh
Optimized configuration SHALL record, for every bed mesh measured, including print start's, the points whose probe samples disagreed and needed a retry, with each point's number of tries and sample spread. It SHALL publish them for the most recent mesh measured since Klipper started, without changing how the mesh is probed.

#### Scenario: Mesh with retried points
- **WHEN** a bed mesh is measured and a point needs a retry
- **THEN** the point, its tries, and its sample spread are available with the time the mesh was measured, until the next mesh is measured or Klipper restarts

#### Scenario: Fluidd shows them
- **WHEN** the package's Fluidd build is served and the loaded mesh is the most recent one measured
- **THEN** the Bed mesh card marks those points on the mesh and lists them, and Bed mesh controls shows how many there were

### Requirement: Bed soak leaves the printer as found
When the bed soak test ends for any reason, no saved mesh profile SHALL have been added, changed, or removed, the test SHALL have left no config save pending, and the mesh loaded before the test SHALL be loaded again. The nozzle heater SHALL be off. The bed and chamber heaters SHALL be off unless the operator asked to keep heating and the test reached its end normally. Print start, its waits, and its mesh SHALL be unchanged by the test and its results.

#### Scenario: Test ends
- **WHEN** a bed soak test ends after measuring meshes
- **THEN** the bed mesh profiles and pending config save state match what they were before the test, and the previously loaded mesh is loaded

### Requirement: Bed soak in Fluidd
The package's Fluidd build SHALL show a Bed soak card under Bed mesh on its Tune page when the bed soak module is present.

#### Scenario: Operator uses the card
- **WHEN** an operator opens the Tune page
- **THEN** the card starts and stops the test, follows it live, shows the results and problem points, and lists, opens, compares, and deletes saved runs
