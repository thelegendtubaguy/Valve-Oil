## MODIFIED Requirements

### Requirement: THR changes are limited to the FIFO correction and its version
The generated THR SHALL request another task when the LIS2DW FIFO holds samples and rearm polling when it is empty, preserving scheduler wake semantics. It SHALL be generated from one of QIDI's THR images with its own patch manifest: `02.02.01.08` (Max 4 and Plus 5) or `02.02.01.09` (Smart 5). Its dictionary SHALL differ from stock only in the version, `02.02.01.08-valve-oil.1` or `02.02.01.09-valve-oil.1`, with matching `identify` lengths. The package MUST keep every other payload, the firmware manifest, and internal filenames. Generation MUST NOT flash hardware or invoke the installer.

#### Scenario: FIFO handling changes without replacing vendor components
- **WHEN** a recognized package is patched and verified
- **THEN** the THR differs only at the approved instruction edits and its data dictionary, and handles empty and nonempty FIFO states correctly
- **AND** sample reporting and overflow accounting remain intact
- **AND** the dictionary the THR serves through `identify` is identical to stock apart from the version
- **AND** all other archive members are byte-identical to the input
