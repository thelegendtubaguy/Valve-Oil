## RENAMED Requirements

- FROM: `### Requirement: Box changes are limited to the shutdown fix`
- TO: `### Requirement: Box changes are limited to the shutdown fix and RFID commands`

## MODIFIED Requirements

### Requirement: Box changes are limited to the shutdown fix and RFID commands
The Box image SHALL be generated only from the pinned stock `02.03.01.21` application. It SHALL differ from it only in skipping QIDI's faulty Box feeder shutdown block; in replacing the bodies of QIDI's `fm17550_write_card_cb`, `fm17550_init_cb`, and `fm17550_read_regs_cb` with the `fm17550_select` and `fm17550_transceive` handlers built from `firmware/box-rfid/`; in the command-table entries and response encoders for those commands; and in a data dictionary that differs from stock only in those commands and responses and the version `02.03.01.21-valve-oil.1`, with matching `identify` length constants. QIDI's `fm17550_read_card_cb` MUST stay unchanged. Commands whose old bodies hold new code MUST be dispatched to `debug_nop`. The bundle MUST NOT contain a QIDI Box image; the installer generates the patched image on the printer.

#### Scenario: Patched Box handles a shutdown
- **WHEN** a Box running the patched image shuts down
- **THEN** it completes its shutdown handlers and reports the original shutdown reason instead of re-entering shutdown

#### Scenario: Select reports any ISO14443A tag
- **WHEN** the host sends `fm17550_select` for a reader with a tag in its field, including a tag halted by an earlier read
- **THEN** the Box wakes the tag with WUPA, selects it through every cascade level, and returns status 1 with its ATQA, UID bytes, and SAK
- **AND** with no tag it returns status 0, and with an unresponsive reader status 2

#### Scenario: Transceive runs one reader exchange
- **WHEN** the host sends `fm17550_transceive` with Transceive or MFAuthent and a frame of 1 to 32 bytes
- **THEN** the Box runs that exchange on the selected tag and returns the reader's error flags, Status2, the received bit count, and at most 44 received bytes
- **AND** any other reader command or frame length is refused without touching the reader

#### Scenario: QIDI's read is unchanged
- **WHEN** QIDI's host software reads a tag with `fm17550_read_card_cb`
- **THEN** the Box behaves exactly as stock
