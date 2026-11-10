## Context

Reading spool tags shipped in `26.11.10.1` (archived change `add-box-rfid-tag-support`). The Valve Oil Box firmware's `fm17550_select` wakes and selects any ISO 14443A tag, and `fm17550_transceive` runs one Transceive or MFAuthent exchange of up to 32 frame bytes and returns the reader's error flags, Status2 (bit 3 shows a MIFARE login), the received bit count, and up to 44 received bytes. NTAG WRITE, MIFARE login, and the two-step MIFARE WRITE all fit those commands, so writing needs no firmware change.

`tltg_box_rfid` already decodes QIDI's v1 block, QIDI-layout data in NTAG pages 4 to 7, and the Anycubic and Elegoo NTAG layouts (`openspec/observations/qidi-box/material-metadata.md`). Each reader has a reactor mutex shared by QIDI's polling and `TLTG_BOX_RFID_REPORT`, and the report already reads a slot's tag on request while the spool is turned by hand.

## Goals / Non-Goals

**Goals:** write a slot's filament and colors to blank MIFARE Classic 1K and NTAG213/215/216 tags in a layout the Box reads back; verify each write by reading it back; never write a tag that is locked or carries another brand's data.

**Non-Goals:** writing Bambu Lab tags (their data is RSA-signed); unlocking password-protected tags; RS-485 Boxes; changing QIDI's read; ISO 15693 tags such as OpenPrintTag, which the FM17550 can't talk to.

## Decisions

### Write recipes

- **QIDI tags (MIFARE Classic 1K):** select, MIFARE login to block 4 with key A `FFFFFFFFFFFF` (`fm17550_transceive` MFAuthent, Status2 bit 3 confirms), `A0 04` (4-bit ACK `0x0A`), then the 16-byte v1 block (ACK `0x0A`), then read block 4 back. Emulation covers this path; on hardware only a refused login (a Bambu tag) has been seen, so the first blank MIFARE Classic 1K tag must confirm it. A v1 block on a MIFARE Classic tag also reads on QIDI's stock firmware.
- **NTAG tags:** `A2 <page> <4 bytes>` per page (ACK `0x0A`), then read back. Anycubic layout: pages 4 to 31 as in `material-metadata.md`, black written as `01 01 01`. Elegoo layout: pages 16 to 24, plus the NDEF URI in pages 4 to 9 if wanted. QIDI layout: pages 4 to 7.
- Anycubic's own tags are password write-protected (`AUTH0 = 4`, configuration locked), so only blank NTAG213/215/216 tags can carry the Anycubic layout. The Elegoo tag seen has no password.

### Each write runs under the reader's mutex

The write takes the same reactor mutex as QIDI's polling and reports, so a QIDI read can't interleave with it. The operator turns the spool past the reader as for `TLTG_BOX_RFID_REPORT`; the write doesn't move the slot, because QIDI's read feeds filament toward the hub and is refused when the hub is occupied.

## Risks / Trade-offs

- [A tag leaves the field mid-write and keeps part of the new data] → each MIFARE block or NTAG page is written whole; the read-back reports a partial write, and writing again repeats it.
- [Clone MIFARE Classic tags behave differently from NXP's] → test genuine and clone tags before release.
- [A write on an Elegoo tag replaces another brand's data] → only blank tags and tags already holding the layout being written are written.

## Hardware still to test

Genuine NXP and clone MIFARE Classic 1K, NXP NTAG213, 215, and 216.

## Open Questions

- Which layout to write on NTAG tags: QIDI's (smallest, already read by this project) or Anycubic's or Elegoo's (also read by those brands' printers).
- The command name and arguments, and where the Fluidd control sits.
- Whether other Anycubic tags share the write password seen on the first one.
