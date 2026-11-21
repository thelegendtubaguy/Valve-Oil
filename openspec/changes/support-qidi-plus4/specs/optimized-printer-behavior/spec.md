## ADDED Requirements

### Requirement: Plus 4 macros follow QIDI's Plus 4 macros
On the Plus 4, the optimized macros SHALL keep QIDI's Plus 4 macro names, coordinates, currents, speeds, and dwells, and SHALL change only behavior that the optimized macros already change on another supported printer.

#### Scenario: Plus 4 print start keeps QIDI's sequence without fixed dead time
- **WHEN** a print starts on a Plus 4
- **THEN** it homes, levels with `Z_TILT_ADJUST`, meshes, and primes in QIDI's order and at QIDI's temperatures
- **AND** the Box `runout_N` variables are cleared without a `G4` dwell after each one

#### Scenario: Plus 4 moves stay within its travel
- **WHEN** the optimized print-start, cut, chute, wipe, or park moves run on a Plus 4
- **THEN** they use QIDI's Plus 4 coordinates, all inside its configured X −1.5–307 and Y −2–325 travel

#### Scenario: Valve Oil's macros follow QIDI's
- **WHEN** Klipper loads `printer.cfg` on a Plus 4 with Valve Oil installed
- **THEN** Valve Oil's include follows `[include box.cfg]`, so its definitions follow every QIDI macro file
