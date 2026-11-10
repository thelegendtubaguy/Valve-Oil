## Context

Each USB Box has two FM17550 readers (`card_reader_{slot//2+1}`), driven by QIDI's Klipper-fork Box firmware `02.03.01.21`. `box_rfid.so` polls `fm17550_read_card_cb` every 0.3 s for up to 30 s and accepts a block only after two identical reads. That command selects the tag with REQA, logs in to MIFARE Classic block 4 with key `FFFFFFFFFFFF`, reads 16 bytes, and halts the tag. When the login fails it skips the read and returns status 1 with the previous buffer.

QIDI's parser accepts two 16-byte layouts and saves `filament_slotN` and `color_slotN`:

| Layout | Selector | Fields |
|---|---|---|
| Legacy | byte 2 high nibble `0` | byte 0 old material ID `1..50`, byte 1 old color ID `1..24`, mapped through `LEGACY_RFID_*_MAP` |
| v1 | byte 2 high nibble `>= 1` | byte 0 `A..Z`, byte 1 `0..99` (ID `QF<b0><b1:02d>`), byte 2 low nibble color count `0..4`, RGB colors from byte 3, byte 15 `<= 127` |

The v1 parser does not check that the ID exists in the database, and it does not verify byte 15.

## Goals / Non-Goals

**Goals:** read QIDI, Anycubic, and Elegoo tags; put both brands' filaments in QIDI's database; keep QIDI's read flow, slot saving, touchscreen, and Fluidd paths unchanged; collect reports for unknown tags.

**Non-Goals:** writing tags and keeping operator database edits across QIDI updates (a later change; the firmware commands already allow both); RS-485 Boxes; Bambu, Creality, and other encrypted or signed formats; changing QIDI's read timing or motion.

## Decisions

### Firmware: two generic commands, QIDI's read untouched

The new commands are deliberately low level. Tag formats live in the host module, where they can change without reflashing.

- `fm17550_select oid=%c` returns `status` (0 no tag, 1 selected, 2 reader not responding) and 20 bytes: ATQA, three cascade levels of UID bytes with BCC, and three SAK bytes. It sends WUPA, not REQA, so a tag that QIDI's read has just halted still answers, and it leaves the tag selected with CRC on.
- `fm17550_transceive oid=%c data=%*s` takes the reader command (`0x0c` Transceive or `0x0e` MFAuthent), a timeout, and up to 32 frame bytes. It returns the reader's error flags, Status2 (bit 3 shows a MIFARE login), the received bit count, and up to 44 received bytes. NTAG READ, NTAG WRITE (4-bit ACK), MIFARE login, and the two-step MIFARE WRITE all go through it.

The handlers are C (`firmware/box-rfid/`) compiled for Cortex-M4 that call QIDI's own FM17550 routines by absolute address, so the output needs no relocations. They replace the bodies of QIDI's `fm17550_write_card_cb` (select) and `fm17550_init_cb` plus `fm17550_read_regs_cb` (transceive). Those commands and `fm17550_read_fifo_cb` leave the data dictionary. Commands 18 and 19 point at `debug_nop` because their old bodies now hold new code. Two existing response encoders are retargeted to `oid=%c status=%c data=%*s`. The image size is unchanged.

Alternatives rejected: patching QIDI's read to skip the login for SAK `0x00` would still return only 16 bytes, which is too little for Anycubic data, and would change vendor behavior; a full Anycubic decoder in firmware would need a reflash for every format change.

### Firmware build and verification

`scripts/generate_box_firmware_manifest.py` compiles the handlers, checks they fit their caves, rebuilds and zopfli-compresses the dictionary, and writes hash-locked patches to `firmware/max4-box-rfid-v1.json`. Installs use only the manifest. `scripts/check_box_firmware.py` runs the handlers in emulation against QIDI's image with QIDI's FM17550 routines replaced by a frame-level tag model (NTAG, MIFARE Classic, no tag, and an unresponsive reader), alongside the existing identify, transfer, and shutdown checks.

### Upgrading the earlier fix

The manifest lists the shutdown fix image as `superseded`. The transfer tool admits it, status reports it as an earlier build, and install builds the new image from QIDI's stock image found in the Box firmware backups or `~/update`. Without a stock copy, install refuses before stopping any service.

### Host: answer QIDI's read with a QIDI block

QIDI's read on insert or `READ_RFID` feeds the slot toward the hub, which turned an Anycubic spool far enough for its tag to read.

The module wraps each reader's `fm17550_read_card` query. When the Box runs the new firmware, each poll selects the tag and reads it by type:

- SAK `0x08`: MIFARE login and block 4, returned as is. This also fixes QIDI's stale-buffer result when the login fails.
- SAK `0x00` (the last cascade level's; a 7-byte UID reports `0x04` first): NTAG pages 4 to 31. Anycubic and Elegoo data is decoded and mapped to a database ID and colors; QIDI-layout data in pages 4 to 7 is returned as is.
- Anything else, or no tag: QIDI's no-tag result.

The result goes back to QIDI's parser as a v1 block, so slot saving, two-read confirmation, `info_from_rfid`, and every client stay QIDI's. A Box on stock or earlier firmware gets QIDI's query unchanged.

### Elegoo tags

Elegoo tags are NTAG213-class with no password. Pages 16 to 24 hold the data, big-endian: a `0x36` header and `EE` manufacturer bytes, the material as decimal ASCII codes, a 16-bit subtype, RGB color with a finish byte, nozzle temperatures, and diameter and weight. The material and subtype codes come from `Savion/elegoo-rfid-editor` (MIT) and are mapped to ElegooSlicer filament names in the catalog.

### Anycubic and Elegoo filaments in QIDI's database

`scripts/generate_filament_catalog.py` builds the catalog from OrcaSlicer's Anycubic profiles and ElegooSlicer's Elegoo profiles: names, types, and the nozzle range of the variant that reaches highest (some base profiles understate it), with QIDI's Box, chamber, and drying fields from the QIDI entry for the same kind of material. 0.2 mm files omit filled, particle, silk, and flexible filaments, and 0.8 mm files omit silk and PEBA, as QIDI's own files do. IDs use letters QIDI does not use (`QFX`, `QFY`); QIDI's v1 parser and its RS-485 path accept any letter. The generator never reassigns an ID, because Box slots, and later tags, carry it.

The module adds missing catalog entries at each Klipper start. QIDI's `multi_color_controller` reads the 0.4 mm file after it, so the Box card and Fluidd see them at once; `qidi-client` may have started before Klipper, so the touchscreen lists them after its next start. Adding at Klipper start, instead of in the installer, re-adds them after any QIDI update once the package is reinstalled, with no installer ownership to record. Uninstall leaves them, because Box slots may refer to them.

### Tag reports

Each reader has a reactor mutex shared by QIDI's polling and `TLTG_BOX_RFID_REPORT`. Automatic reports are written once per distinct tag contents and reason, at most 20 per Klipper run. A requested report reads all NTAG pages from GET_VERSION's size, or every MIFARE sector readable with the default, MAD, or NDEF public key. UID bytes are zeroed in page data and never stored in the identity fields.

### For the later writing change

The firmware already carries everything a write needs; only host code and a Fluidd control are missing.

- QIDI tags: select, MIFARE login to block 4 with key A `FFFFFFFFFFFF` (`fm17550_transceive` MFAuthent, Status2 bit 3 confirms), `A0 04` (4-bit ACK `0x0A`), then the 16-byte v1 block (ACK `0x0A`), then read block 4 back. Emulation covers this path; on hardware only a refused login (a Bambu tag) has been seen, so the first blank MIFARE Classic 1K tag must confirm it.
- NTAG tags: `A2 <page> <4 bytes>` per page (ACK `0x0A`), then read back. Anycubic layout: pages 4 to 31 as in `material-metadata.md`, black written as `01 01 01`. Elegoo layout: pages 16 to 24, plus the NDEF URI in pages 4 to 9 if wanted.
- Anycubic's own tags are password write-protected (`AUTH0 = 4`, configuration locked), so only blank NTAG213/215/216 tags can carry the Anycubic layout. The Elegoo tag seen has no password.
- Hardware still to test: genuine NXP and clone MIFARE Classic 1K, NXP NTAG213, 215, and 216.

## Risks / Trade-offs

- [The handlers run QIDI's FM17550 routines in combinations QIDI never used (WUPA, arbitrary frames)] → QIDI's own select and read use the same routines with the same argument patterns; the emulation covers each path; hardware validation on both admitted bootloader builds precedes release.
- [A long reader exchange delays other Box MCU work] → each command is one exchange of a few milliseconds, as QIDI's read is; the host splits page reads across commands.
- [Anycubic or Elegoo changes its layout] → the decoder lives on the host, unknown layouts read as no tag, and reports collect the new layout.
- [QIDI later assigns a `QFX` or `QFY` ID] → the merge never adds an entry whose ID is taken; the generator refuses collisions with QIDI's database.
- [The touchscreen misses the new filaments until it restarts] → documented; the Box card and Fluidd see them at once.

## Open Questions

- Whether other Anycubic tags share the write password seen on the first one; it matters only for the later writing change.
- How to keep operator database edits across QIDI updates.
