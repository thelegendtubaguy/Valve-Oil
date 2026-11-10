## Context

Each USB Box has two FM17550 readers (`card_reader_{slot//2+1}`), driven by QIDI's Klipper-fork Box firmware `02.03.01.21`. `box_rfid.so` polls `fm17550_read_card_cb` every 0.3 s for up to 30 s and accepts a block only after two identical reads. That command selects the tag with REQA, logs in to MIFARE Classic block 4 with key `FFFFFFFFFFFF`, reads 16 bytes, and halts the tag. When the login fails it skips the read and returns status 1 with the previous buffer.

QIDI's parser accepts two 16-byte layouts and saves `filament_slotN` and `color_slotN`:

| Layout | Selector | Fields |
|---|---|---|
| Legacy | byte 2 high nibble `0` | byte 0 old material ID `1..50`, byte 1 old color ID `1..24`, mapped through `LEGACY_RFID_*_MAP` |
| v1 | byte 2 high nibble `>= 1` | byte 0 `A..Z`, byte 1 `0..99` (ID `QF<b0><b1:02d>`), byte 2 low nibble color count `0..4`, RGB colors from byte 3, byte 15 `<= 127` |

The v1 parser does not check that the ID exists in the database, and it does not verify byte 15.

## Goals / Non-Goals

**Goals:** read QIDI, Anycubic, Elegoo, and Bambu Lab tags; put the three brands' filaments in QIDI's database; keep QIDI's read flow, slot saving, touchscreen, and Fluidd paths unchanged; collect reports for unknown tags.

**Non-Goals:** writing tags (change `add-box-rfid-tag-writing`; the firmware commands already allow it); keeping operator database edits across QIDI updates; RS-485 Boxes; writing Bambu Lab tags (their data is RSA-signed); Creality and other encrypted formats; changing QIDI's read timing or motion.

## Decisions

### Firmware: two generic commands, QIDI's read untouched

The new commands are deliberately low level. Tag formats live in the host module, where they can change without reflashing.

- `fm17550_select oid=%c` returns `status` (0 no tag, 1 selected, 2 reader not responding) and 20 bytes: ATQA, three cascade levels of UID bytes with BCC, and three SAK bytes. It sends WUPA, not REQA, so a tag that QIDI's read has just halted still answers, and it leaves the tag selected with CRC on.
- `fm17550_transceive oid=%c data=%*s` takes the reader command (`0x0c` Transceive or `0x0e` MFAuthent), a timeout, and up to 32 frame bytes. It returns the reader's error flags, Status2 (bit 3 shows a MIFARE login), the received bit count, and up to 44 received bytes. NTAG READ, NTAG WRITE (4-bit ACK), MIFARE login, and the two-step MIFARE WRITE all go through it.

The handlers are C (`firmware/box-rfid/`) compiled for Cortex-M4 that call QIDI's own FM17550 routines by absolute address, so the output needs no relocations. They replace the bodies of QIDI's `fm17550_write_card_cb` (select) and `fm17550_init_cb` plus `fm17550_read_regs_cb` (transceive). Those commands and `fm17550_read_fifo_cb` leave the data dictionary. Commands 18 and 19 point at `debug_nop` because their old bodies now hold new code. Two existing response encoders are retargeted to `oid=%c status=%c data=%*s`. The image size is unchanged.

Alternatives rejected: patching QIDI's read to skip the login for SAK `0x00` would still return only 16 bytes, which is too little for Anycubic data, and would change vendor behavior; a full Anycubic decoder in firmware would need a reflash for every format change.

### Firmware build and verification

`scripts/generate_box_firmware_manifest.py` compiles the handlers, checks they fit their caves, rebuilds and zopfli-compresses the dictionary, and writes hash-locked patches to `firmware/max4-box-valve-oil-v1.json`. Installs use only the manifest. `scripts/check_box_firmware.py` runs the handlers in emulation against QIDI's image with QIDI's FM17550 routines replaced by a frame-level tag model (NTAG, MIFARE Classic, no tag, and an unresponsive reader), alongside the existing identify, transfer, and shutdown checks.

### Upgrading the earlier fix

The manifest lists the shutdown fix image as `superseded`. The transfer tool admits it, status reports it as an earlier build, and install builds the new image from QIDI's stock image found in the Box firmware backups or `~/update`. Without a stock copy, install refuses before stopping any service.

### Host: answer QIDI's read with a QIDI block

QIDI's read on insert or `READ_RFID` feeds the slot toward the hub, which turned an Anycubic spool far enough for its tag to read.

The module wraps each reader's `fm17550_read_card` query. When the Box runs the new firmware, each poll selects the tag and reads it by type:

- SAK `0x08`: MIFARE login and block 4, returned as is. When that login fails, the Bambu Lab read below. This also fixes QIDI's stale-buffer result when both logins fail.
- SAK `0x00` (the last cascade level's; a 7-byte UID reports `0x04` first): NTAG pages 4 to 31. Anycubic and Elegoo data is decoded and mapped to a database ID and colors; QIDI-layout data in pages 4 to 7 is returned as is.
- Anything else, or no tag: QIDI's no-tag result. A page read cut short, as when the tag leaves the field mid-read, also answers no tag, so QIDI keeps polling instead of ending the read as an unrecognized label.

The result goes back to QIDI's parser as a v1 block, so slot saving, two-read confirmation, `info_from_rfid`, and every client stay QIDI's. A Box on stock or earlier firmware gets QIDI's query unchanged.

### Elegoo tags

Elegoo tags are NTAG213-class with no password. Pages 16 to 24 hold the data, big-endian: a `0x36` header and `EE` manufacturer bytes, the material as decimal ASCII codes, a 16-bit subtype, RGB color with a finish byte, nozzle temperatures, and diameter and weight. The material and subtype codes come from `Savion/elegoo-rfid-editor` (MIT) and are mapped to ElegooSlicer filament names in the catalog.

### Bambu Lab tags

Bambu Lab tags are MIFARE Classic 1K with 4-byte UIDs. Each sector's key A is 6 bytes of HKDF-SHA256 (RFC 5869) output: input the UID, salt `9a759cf2c4f7caff222cb9769b41bc96`, info `RFID-A\0`, 96 bytes split into 16 keys. Python's `hmac` and `hashlib` compute it, so the module needs no new dependency. The layout comes from the Bambu Lab RFID Tag Guide (`queengooborg/Bambu-Lab-RFID-Tag-Guide`); the module reads only:

| Block | Contents |
|---:|---|
| 1 | material variant ID (bytes 0–7, `A00-K0`) and Bambu filament ID (bytes 8–15, `GFA00`), ASCII |
| 2 | filament type, ASCII (`PLA`) |
| 4 | detailed filament type, ASCII (`PLA Basic`) |
| 5 | color as R, G, B, A |
| 16 | format `0x0002` (u16 LE), color count (u16 LE), second color as A, B, G, R |

QIDI's failed block 4 login leaves the tag idle, so the read selects it again, logs in to sector 0 with its derived key, and reads blocks 1 and 2, then logs in to sectors 1 and 4. A refused sector login is retried from a fresh select, in case the reader cannot switch sectors with Crypto1 on, then with the default key.

Owners copy Bambu tags onto blank "magic" MIFARE tags (Gen 2 CUID, FUID, Gen 4), which Bambu's AMS accepts (guide issue 3). A copy with the original UID and sector trailers is indistinguishable from the original. A copy that kept the blank tag's default key opens with QIDI's block 4 login, and Bambu text such as `PLA Basic` passes QIDI's v1 checks (`P`, 76, version 4), which would give the slot a nonexistent `QFP76`. A block 4 that is NUL-padded printable text is therefore never returned as a QIDI block; the tag is read as a Bambu tag with the default key instead. QIDI v1 blocks are binary (byte 2 is `0x11..0x14`), so QIDI tags are unaffected. Decoding requires text in block 2 (filament type), so other default-key tags are not taken for Bambu's. A sector 0 login failure means the tag is not Bambu's (reported as locked); a later failure means the tag left the field (no tag). The filament ID maps through the catalog's `bambu_ids` to a filament name; an ID the catalog lacks falls back to the detailed type, then the filament type, through the same name lookup as other brands.

Sectors 10–15 hold an RSA-2048 signature over the data, so changed data would not pass a Bambu printer's check, and the module never writes. Reports zero block 9, the spool's serial number, as well as the UID.

### Anycubic, Elegoo, and Bambu Lab filaments in QIDI's database

`scripts/generate_filament_catalog.py` builds the catalog from OrcaSlicer's Anycubic profiles, ElegooSlicer's Elegoo profiles, and BambuStudio's Bambu Lab profiles: names, types, and the nozzle range of the variant that reaches highest (some base profiles understate it), with QIDI's Box, chamber, and drying fields from the QIDI entry for the same kind of material. 0.2 mm files omit filled, particle, silk, flexible, Aero, and PA support filaments, and 0.8 mm files omit silk, PEBA, and Aero, as QIDI's own files do. Filaments that are not 1.75 mm, and names QIDI's database already has (Bambu ABS), are left out; the Bambu ID map still names QIDI's entry. Bambu Lab entries use QIDI's existing `Bambu` vendor. IDs use letters QIDI does not use (`QFX`, `QFY`, `QFZ`); QIDI's v1 parser and its RS-485 path accept any letter. The generator never reassigns an ID, because Box slots, and later tags, carry it.

The module adds missing catalog entries at each Klipper start. QIDI's `multi_color_controller` reads the 0.4 mm file after it, so the Box card and Fluidd see them at once; `qidi-client` may have started before Klipper, so the touchscreen lists them after its next start. Adding at Klipper start, instead of in the installer, re-adds them after any QIDI update once the package is reinstalled, with no installer ownership to record. Uninstall leaves them, because Box slots may refer to them.

### Tag reports

Each reader has a reactor mutex shared by QIDI's polling and `TLTG_BOX_RFID_REPORT`. Automatic reports are written once per distinct tag contents and reason, at most 20 per Klipper run. A requested report reads all NTAG pages from GET_VERSION's size, or every MIFARE sector readable with the default, MAD, or NDEF public key. UID bytes are zeroed in page data and never stored in the identity fields.

## Risks / Trade-offs

- [The handlers run QIDI's FM17550 routines in combinations QIDI never used (WUPA, arbitrary frames)] → QIDI's own select and read use the same routines with the same argument patterns; the emulation covers each path; hardware validation on both admitted bootloader builds precedes release.
- [A long reader exchange delays other Box MCU work] → each command is one exchange of a few milliseconds, as QIDI's read is; the host splits page reads across commands.
- [Anycubic or Elegoo changes its layout] → the decoder lives on the host, unknown layouts read as no tag, and reports collect the new layout.
- [QIDI later assigns a `QFX` or `QFY` ID] → the merge never adds an entry whose ID is taken; the generator refuses collisions with QIDI's database.
- [The touchscreen misses the new filaments until it restarts] → documented; the Box card and Fluidd see them at once.

## Open Questions

- How to keep operator database edits across QIDI updates.
