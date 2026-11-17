## Context

Reading spool tags shipped in `26.11.10.1` (archived change `add-box-rfid-tag-support`). The Valve Oil Box firmware's `fm17550_select` wakes and selects any ISO 14443A tag, and `fm17550_transceive` runs one Transceive or MFAuthent exchange of up to 32 frame bytes and returns the reader's error flags, Status2 (bit 3 shows a MIFARE login), the received bit count, and up to 44 received bytes. NTAG WRITE, MIFARE login, and the two-step MIFARE WRITE all fit those commands, so writing needs no firmware change.

`tltg_box_rfid` already decodes QIDI's v1 block, QIDI-layout data in NTAG pages 4 to 7, and the Anycubic and Elegoo NTAG layouts (`openspec/observations/qidi-box/material-metadata.md`). Each reader has a reactor mutex shared by QIDI's polling and `TLTG_BOX_RFID_REPORT`, and the report already reads a slot's tag on request while the spool is turned by hand.

Each reader serves two adjacent slots (`card_reader_{slot//2+1}`), so a reader may see the tags of both spools beside it.

## Goals / Non-Goals

**Goals:** write a slot's filament and colors to blank MIFARE Classic 1K and NTAG213/215/216 tags in a layout the Box reads back, for any entry in the printer's filament database whatever its vendor; verify each write by reading it back; never write a tag that is locked or carries another brand's data.

**Non-Goals:** writing Bambu Lab tags (their data is RSA-signed) or other brands' layouts; unlocking password-protected tags; RS-485 Boxes; ISO 15693 tags such as OpenPrintTag, which the FM17550 can't talk to.

## Decisions

### Tags carry the database ID, and optionally the entry's name

Every tag gets QIDI's v1 block: the entry's `QF` ID and up to four colors. The ID names one database entry whatever its vendor (QIDI, Generic, Bambu, the catalog's Anycubic and Elegoo entries, or the operator's own), and QIDI's read saves it whether or not the database has it, so no other brand's layout is needed. Imitating a Bambu tag was rejected: without Bambu's signature it reads only here, and it would resolve through brand and material names to fewer entries with one color.

IDs an operator adds exist only in that printer's database. The write can add the entry's name in a 32-byte name field after the block: `VO`, version `01`, the name's length, then the UTF-8 name (at most 28 bytes), zero-padded. When this printer's database holds that name under another ID, the read gives the slot this printer's ID; otherwise the block's ID stands. QIDI's stock firmware ignores the field.

- **MIFARE Classic 1K:** block 4 holds the QIDI block and blocks 5 and 6 the name field, all in sector 1 under key A `FFFFFFFFFFFF`. Block 4 under that key is what QIDI's stock Box firmware reads, so a Box left on it should read these tags too; that is not tested, since every USB Box gets the Valve Oil Box firmware.
- **NTAG213/215/216:** pages 4 to 7 hold the QIDI block and pages 8 to 15 the name field.

Every write covers all 48 bytes, writing zeros where no name is wanted, so a rewritten tag never keeps a stale name.

### Which tags are written

Only a tag that is blank (zeros, or a new NTAG's empty NDEF TLVs) or already holds a QIDI block, with or without a name field, is written. Refused: MIFARE tags that don't open with the default key (locked, or a Bambu tag), copies of Bambu tags on blank tags, NTAG tags with lock bits on pages 4 to 15 or `AUTH0` at or below page 15, Anycubic and Elegoo data, and anything else.

### Write recipes

- **MIFARE Classic 1K:** select, MIFARE login to block 4 (`fm17550_transceive` MFAuthent, Status2 bit 3 confirms), then for blocks 4, 5, and 6 `A0 <block>` and the 16 data bytes, each answered by a 4-bit ACK `0x0A`.
- **NTAG:** `A2 <page> <4 bytes>` for pages 4 to 15, each answered by a 4-bit ACK.
- The reader reports each 4-bit ACK with CRCErr (`0x04`) set in its error flags, so an ACK is judged by its bit count and value alone; the read-back from a fresh select decides whether a write took.
- A tag left active by a login, a read, or GET_VERSION drops the next wake-up to idle and answers the one after, so every select after an exchange with a tag tries twice.

### Each write runs under the reader's mutex

The write takes the same reactor mutex as QIDI's polling and reports, so a QIDI read can't interleave with it. The operator turns the spool past the reader as for `TLTG_BOX_RFID_REPORT`; the write doesn't move the slot, because QIDI's read feeds filament toward the hub and is refused when the hub is occupied. One write goes to one selected tag, identified by a hash of its UID, and its read-back must come from the same tag.

### Turning the spool for a test

`VO_BOX_RFID_TEST ... TURN=<mm> [STEP=<mm>]` turns an empty slot's spool on its roller through `tltg_box_slot_jog` and polls the reader after each step (10 mm by default), so a test needs no hand on the spool. Only a slot QIDI counts empty, with its inlet sensor clear, is turned, as for `tltg_box_spool_roll`. On the Plus 5 only negative TURN turns the spool; positive TURN left it still.

### Diagnostic command

`VO_BOX_RFID_TEST` (new commands use the `VO_` prefix) drove the hardware tests and stays for diagnosing tags, with no control in either interface:

- `ACTION=SCAN SLOT= [SECONDS=]` polls the slot's reader and lists each tag it selects: a short hash of the UID, the tag type, what it holds, what it would set the slot to, whether it may be written, and how often and when it was seen.
- `ACTION=WRITE SLOT= FILAMENT= COLORS= [NAME=1] [TAG=] [SECONDS=]` writes the first tag (or only tag `TAG`) that comes past and reads it back.

Results are published in `tltg_box_rfid`'s status under `job`.

### Reading Creality, TigerTag, and OpenTag3D tags

These tags are read on QIDI's poll like Anycubic's and Elegoo's and resolved through the same lookup: the brand's filament by name, the brand's base material, Generic's by name, then a Generic or QIDI entry of that material type. They are never written. None has been read on hardware.

- **Creality** (MIFARE Classic 1K): sector 1's key is AES-128-ECB of the UID repeated to 16 bytes under `q3bu^t1nqfZ(pf$1`, first 6 bytes, tried as key A and key B after QIDI's default key fails. Blocks 4 to 6 are a 48-character ASCII record under AES-128-ECB with `H@CFkRnz@KAtBJp2`; characters 12 to 16 are Creality's material code and 18 to 23 the RGB color (Bambu-Research-Group/RFID-Tag-Guide's `CrealityRfid.md`; the keys agree across several open readers). The printer's Python has no AES library, so the module carries a FIPS-197 AES-128, checked against FIPS-197's example and OpenSSL. Codes map to Creality's filaments or Generic materials in the catalog's `creality_materials`.
- **TigerTag** (NTAG): pages 4 to 23, format ID `0x5BF59264` or `0xBC0FCB97`, type `0x8E` for filament, then material, aspect, brand, and up to three colors, resolved offline through TigerTag's CC0 ID tables in the catalog. TigerTag's README example gives Rosa3D's brand as `0x4E19`, but its ID table's 19961 is `0x4DF9`; the table is used.
- **OpenTag3D** (NTAG215/216): an NDEF record of MIME type `application/opentag3d` whose payload gives the base material, modifier, brand, and up to four RGBA colors (opentag3d.info spec 2.004). The type 2 read covers pages 4 to 35 so the record's colors are included.
- **OpenPrintTag** uses ISO 15693 tags, which the FM17550 can't talk to.

### Creality filaments in the catalog

`scripts/generate_filament_catalog.py --only Creality` adds the 34 filaments OrcaSlicer's Creality profiles give with `filament_vendor` Creality, named `Creality <profile name>`, with IDs `QFW00` to `QFW33`, and keeps every other vendor's entries as they were. Their QIDI fields follow each profile's `filament_type` with its name, so `CR-Nylon` takes QIDI's PA fields.

## Risks / Trade-offs

- [A tag leaves the field mid-write and keeps part of the new data] → each MIFARE block or NTAG page is written whole; the read-back reports a partial write, and writing again repeats it.
- [A reader writes the neighboring slot's spool tag] → writes are limited to blank and QIDI-block tags, can be limited to one tag by hash, and the scan measures how often each reader sees each neighbor.
- [Creality, TigerTag, or OpenTag3D tags decode differently on real spools than their published layouts] → unreadable tags still save a report, as before, for a fix.
- [A name field leads to a different entry on another printer] → only when that printer's entry of the same name has another ID, which is the point of the field.

## Hardware results

On the Plus 5 (2026-11-16), one blank tag per slot, each on the spool side facing the reader: genuine NXP MIFARE Classic 1K (1A), NTAG213 (1B), NTAG215 (1C), and NTAG216 (1D). Each was found by turning its spool backward; a full turn took about 450 to 650 mm of slot motion, and a tag was readable for about 10 to 30 mm of it. A tag on the spool side away from the reader was never seen. Each tag was written limited to its own hash, read back, and scanned again; every scan of either slot on a reader saw only that slot's tag, and each neighbor kept its contents. With two NTAG216 tags on reader 2 (1C and 1D), writes not limited to a tag, including a rewrite of a tag already holding a QIDI block, went to the turned slot's tag each time, and the other kept its contents. Feeding a spool with a written NTAG into 1C set the slot through QIDI's insert read (`rfid_respond:filament_id=QFB01 version=1`). Through the Valve Oil UI's dialog, 1C's tag was written on the empty slot (the Box turned the spool 140 mm, 16 s) and then set the slot through QIDI's insert read, and written again with filament fed while the spool was turned by hand (19 s), then set the slot through `READ_RFID`.

## Operator command and control

`VO_BOX_RFID_WRITE SLOT= [FILAMENT=] [COLORS=] [NAME=0] [SECONDS=]` writes the slot's saved filament and colors unless others are given, with the name field unless `NAME=0`. On an empty slot it turns the spool backward up to 800 mm, stopping once the tag is written; with filament in the slot the operator turns the spool by hand for up to `SECONDS` (60). Its progress and result are published under `tltg_box_rfid`'s `job`, as the test tool's are.

The Valve Oil UI's QIDI Box screen has a **Write spool tag** button beside **Read RFID** for every Box slot, enabled while the Box is idle. It opens a dialog to pick the vendor, filament, and up to four colors (starting from the slot's), says whether the Box turns the spool or the operator does, shows the spool turning or the time left, and ends with the tag written and checked, or why not: no tag came past (with the advice to turn the spool over), another brand's data, a Bambu Lab tag, a locked tag, or a read-back that didn't match.

The Fluidd fork's QIDI Box card has the same dialog, behind a tag button beside the pencil, shown when Klipper has `VO_BOX_RFID_WRITE` and the firmware filament database is loaded. `VO_BOX_RFID_TEST` stays as a diagnostic command with no control in either interface.
