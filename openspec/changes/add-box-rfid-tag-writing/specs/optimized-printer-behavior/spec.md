## ADDED Requirements

### Requirement: Writing a slot's filament to a spool tag
On a USB Box running the Valve Oil Box firmware, optimized configuration SHALL, on operator request, write a filament from the printer's filament database, by default the slot's own, and up to four colors to the tag of the spool in that slot, then read the tag back and report whether the write took. It MUST NOT write a tag that is locked, a Bambu Lab tag, or a tag holding another brand's data.

#### Scenario: Blank tag is written
- **WHEN** an operator asks to write a slot's filament and a spool with a blank MIFARE Classic 1K or NTAG213/215/216 tag comes past the slot's reader
- **THEN** the tag holds the filament and colors, the read-back confirms it, and QIDI's next read of that spool sets the slot from it

#### Scenario: Empty slot's spool is turned
- **WHEN** the slot is empty and its spool sits on the rollers with no filament fed
- **THEN** the slot's motor turns the spool backward until the tag is written or about one turn has passed

#### Scenario: Slot with filament fed
- **WHEN** filament is fed from the slot
- **THEN** no slot motor moves, and the operator turns the spool by hand while the write waits for the tag

#### Scenario: Tag names an entry this printer holds under another ID
- **WHEN** a written tag carries its entry's name and this printer's filament database has an entry of that name with another ID
- **THEN** reading the spool sets the slot to this printer's entry for that name

#### Scenario: Tag can't be written
- **WHEN** the tag is locked, is a Bambu Lab tag, holds another brand's data, or leaves the field before the read-back confirms the write
- **THEN** no further data is written and the console says why

#### Scenario: Box without the Valve Oil Box firmware
- **WHEN** an operator asks to write a tag in a Box on any other firmware
- **THEN** nothing is written and the console says the Box firmware is needed

## RENAMED Requirements

- FROM: `### Requirement: QIDI Box reads Anycubic, Elegoo, and Bambu Lab tags`
- TO: `### Requirement: QIDI Box reads vendor spool tags`
- FROM: `### Requirement: Anycubic, Elegoo, and Bambu Lab filaments in QIDI's database`
- TO: `### Requirement: Vendor filaments in QIDI's database`

## MODIFIED Requirements

### Requirement: QIDI Box reads vendor spool tags
On a USB Box running the Valve Oil Box firmware, optimized configuration SHALL answer QIDI's RFID reads by reading the tag according to its type, decoding Anycubic, Elegoo, Bambu Lab, Creality, TigerTag, and OpenTag3D tags to a filament database ID and colors, and handing QIDI's parser the result in QIDI's v1 layout. QIDI's confirmation, slot saving, and clients SHALL stay unchanged, and a Box on any other firmware SHALL keep QIDI's read.

#### Scenario: QIDI spool is recognized
- **WHEN** QIDI reads a Box slot holding a QIDI-tagged spool
- **THEN** the slot's filament and color are set exactly as QIDI's own read sets them

#### Scenario: Anycubic or Elegoo spool is recognized
- **WHEN** QIDI reads a Box slot holding an Anycubic or Elegoo spool whose tag passes the reader
- **THEN** the slot's filament is the database entry with that brand and material name, falling back to the brand's base material, then Generic's entry by name, then a Generic or QIDI entry of that material type, and its color is the tag's

#### Scenario: TigerTag or OpenTag3D spool is recognized
- **WHEN** QIDI reads a Box slot holding a spool with a TigerTag or OpenTag3D tag that passes the reader
- **THEN** the slot's filament is found from the tag's brand and material as for Anycubic and Elegoo spools, and its colors are the tag's up to four

#### Scenario: Bambu Lab spool is recognized
- **WHEN** QIDI reads a Box slot holding a Bambu Lab spool whose tag passes the reader
- **THEN** the tag is read with the MIFARE keys derived from its UID, or the default key on a copy written to a blank tag, and never written
- **AND** the slot's filament is the database entry named for the tag's Bambu filament ID, falling back to the tag's material name as for other brands, and its colors are the tag's one or two colors

#### Scenario: Creality spool is recognized
- **WHEN** QIDI reads a Box slot holding a Creality spool whose tag passes the reader
- **THEN** the tag is opened with the MIFARE key Creality derives from its UID, as key A or key B, or the default key on a copy, and its encrypted record is decrypted and never written
- **AND** the record's material code picks the Creality or Generic filament it names

#### Scenario: Bambu copy opens with QIDI's key
- **WHEN** QIDI's default-key login opens a tag whose block 4 holds text rather than a QIDI block
- **THEN** the tag is read as a Bambu Lab tag, and its text is never handed to QIDI's parser

#### Scenario: Unusable tag is not misread
- **WHEN** the tag opens with none of the known MIFARE keys, or its layout is not recognized
- **THEN** QIDI's parser receives empty data, never an earlier tag's, and the slot keeps its saved filament

#### Scenario: Tag leaves the reader mid-read
- **WHEN** a tag stops answering partway through its read
- **THEN** QIDI's parser receives no tag, so QIDI keeps polling

#### Scenario: Stock Box firmware
- **WHEN** a Box runs QIDI's firmware or the earlier shutdown-fix build
- **THEN** RFID reads behave exactly as stock

### Requirement: Vendor filaments in QIDI's database
At each Klipper start, optimized configuration SHALL add to every QIDI `filament-*.json` the catalog's Anycubic, Elegoo, Bambu Lab, and Creality 1.75 mm filaments for that nozzle size that the file lacks. It MUST NOT change or remove existing entries, and MUST NOT add an entry whose name or filament ID is already present. Catalog IDs SHALL use `QFX`, `QFY`, `QFZ`, and `QFW` and SHALL never be reassigned.

#### Scenario: Database after a QIDI update
- **WHEN** a QIDI firmware update has replaced the filament files and the optimized configuration is installed again
- **THEN** the next Klipper start adds the catalog filaments again, and QIDI's entries are unchanged

#### Scenario: QIDI already names the filament
- **WHEN** QIDI's stock database has an entry with a vendor filament's name, such as Bambu ABS
- **THEN** the catalog leaves that filament out, and its tags set QIDI's entry

#### Scenario: Operator entry with the same name
- **WHEN** the database already has an entry with a catalog filament's name or ID
- **THEN** that entry is kept and the catalog filament is not added

### Requirement: Spool tag reports
Optimized configuration SHALL save a report in Klipper's log folder for each distinct tag a Box with the Valve Oil Box firmware reads but cannot use, and `VO_BOX_RFID_REPORT SLOT=`, also answering to its earlier name `TLTG_BOX_RFID_REPORT`, SHALL read a slot's tag without slot motion into a report. Reports MUST NOT contain tag UID bytes.

#### Scenario: Unknown tag during a QIDI read
- **WHEN** QIDI reads a tag whose layout is unknown, that opens with no known key, or whose type is unsupported
- **THEN** one report with the tag's type and readable data is saved for that tag contents

#### Scenario: Report on request
- **WHEN** an operator runs `VO_BOX_RFID_REPORT` and turns the spool past the slot's reader
- **THEN** the full readable tag memory and its decoding are saved without the spool's serial number, and the console names the file
