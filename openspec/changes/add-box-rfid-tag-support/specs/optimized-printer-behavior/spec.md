## ADDED Requirements

### Requirement: QIDI Box reads Anycubic, Elegoo, and Bambu Lab tags
On a USB Box running the TLTG Box firmware, optimized configuration SHALL answer QIDI's RFID reads by reading the tag according to its type, decoding Anycubic, Elegoo, and Bambu Lab tags to a filament database ID and colors, and handing QIDI's parser the result in QIDI's v1 layout. QIDI's confirmation, slot saving, and clients SHALL stay unchanged, and a Box on any other firmware SHALL keep QIDI's read.

#### Scenario: QIDI spool is recognized
- **WHEN** QIDI reads a Box slot holding a QIDI-tagged spool
- **THEN** the slot's filament and color are set exactly as QIDI's own read sets them

#### Scenario: Anycubic or Elegoo spool is recognized
- **WHEN** QIDI reads a Box slot holding an Anycubic or Elegoo spool whose tag passes the reader
- **THEN** the slot's filament is the database entry with that brand and material name, falling back to the brand's base material and then QIDI's Generic entry, and its color is the tag's

#### Scenario: Bambu Lab spool is recognized
- **WHEN** QIDI reads a Box slot holding a Bambu Lab spool whose tag passes the reader
- **THEN** the tag is read with the MIFARE keys derived from its UID, or the default key on a copy written to a blank tag, and never written
- **AND** the slot's filament is the database entry named for the tag's Bambu filament ID, falling back to the tag's material name as for other brands, and its colors are the tag's one or two colors

#### Scenario: Bambu copy opens with QIDI's key
- **WHEN** QIDI's default-key login opens a tag whose block 4 holds text rather than a QIDI block
- **THEN** the tag is read as a Bambu Lab tag, and its text is never handed to QIDI's parser

#### Scenario: Unusable tag is not misread
- **WHEN** the tag opens with neither QIDI's nor Bambu Lab's MIFARE keys, or its layout is not recognized
- **THEN** QIDI's parser receives empty data, never an earlier tag's, and the slot keeps its saved filament

#### Scenario: Tag leaves the reader mid-read
- **WHEN** a tag stops answering partway through its read
- **THEN** QIDI's parser receives no tag, so QIDI keeps polling

#### Scenario: Stock Box firmware
- **WHEN** a Box runs QIDI's or an earlier TLTG firmware
- **THEN** RFID reads behave exactly as stock

### Requirement: Anycubic, Elegoo, and Bambu Lab filaments in QIDI's database
At each Klipper start, optimized configuration SHALL add to every QIDI `filament-*.json` the catalog's Anycubic, Elegoo, and Bambu Lab 1.75 mm filaments for that nozzle size that the file lacks. It MUST NOT change or remove existing entries, and MUST NOT add an entry whose name or filament ID is already present. Catalog IDs SHALL use `QFX`, `QFY`, and `QFZ` and SHALL never be reassigned.

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
Optimized configuration SHALL save a report in Klipper's log folder for each distinct tag a Box with the TLTG Box firmware reads but cannot use, and `TLTG_BOX_RFID_REPORT SLOT=` SHALL read a slot's tag without slot motion into a report. Reports MUST NOT contain tag UID bytes.

#### Scenario: Unknown tag during a QIDI read
- **WHEN** QIDI reads a tag whose layout is unknown, that opens with no known key, or whose type is unsupported
- **THEN** one report with the tag's type and readable data is saved for that tag contents

#### Scenario: Report on request
- **WHEN** an operator runs `TLTG_BOX_RFID_REPORT` and turns the spool past the slot's reader
- **THEN** the full readable tag memory and its decoding are saved without the spool's serial number, and the console names the file
