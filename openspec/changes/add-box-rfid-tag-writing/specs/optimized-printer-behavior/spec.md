## ADDED Requirements

### Requirement: Writing a slot's filament to a spool tag
On a USB Box running the Valve Oil Box firmware, optimized configuration SHALL, on operator request and without slot motion, write a slot's filament and colors to the tag on that slot's reader in a layout the Box reads back, then read the tag back and report whether the write took. It MUST NOT write a tag that is locked, a Bambu Lab tag, or a tag holding another brand's data.

#### Scenario: Blank tag is written
- **WHEN** an operator asks to write a slot's filament and turns a spool with a blank MIFARE Classic 1K or NTAG213/215/216 tag past the slot's reader
- **THEN** the tag holds the slot's filament and colors, the read-back confirms it, and QIDI's next read of that spool sets the slot from it

#### Scenario: Tag can't be written
- **WHEN** the tag is locked, is a Bambu Lab tag, holds another brand's data, or leaves the field before the read-back confirms the write
- **THEN** no further data is written and the console says why

#### Scenario: Box without the Valve Oil Box firmware
- **WHEN** an operator asks to write a tag in a Box on any other firmware
- **THEN** nothing is written and the console says the Box firmware is needed
