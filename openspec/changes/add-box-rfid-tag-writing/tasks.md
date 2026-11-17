## 1. Decisions

- [x] 1.1 Choose the tag layout: QIDI's v1 block on both tag types, with an optional name field.
- [x] 1.2 Choose the operator command and its arguments, and where the control sits, from the hardware results.

## 2. Writing

- [x] 2.1 Write the QIDI v1 block and the name field to MIFARE Classic 1K blocks 4 to 6 and NTAG213/215/216 pages 4 to 15 through `fm17550_select` and `fm17550_transceive`, under the reader's mutex.
- [x] 2.2 Read each write back and report the result; refuse locked tags, Bambu Lab tags, tags with another brand's data, and Boxes without the Valve Oil Box firmware.
- [x] 2.3 Give the slot this printer's ID for a name field's entry when the read finds one.
- [x] 2.4 Add the `VO_BOX_RFID_TEST` diagnostic command.
- [x] 2.5 Read Creality, TigerTag, and OpenTag3D tags, with emulated tags from their published layouts.
- [x] 2.6 Add Creality's filaments to the catalog (`--only Creality`), and Creality's and TigerTag's code tables.

## 3. Hardware

- [x] 3.1 Write and read back genuine NXP MIFARE Classic 1K and NTAG213, 215, and 216 tags on a test Box, and record the reader's answers to write frames.
- [x] 3.2 Check that a written spool sets its slot through QIDI's insert read.
- [x] 3.3 Scan each reader while each adjacent spool turns, and record which tags it sees and how often.
- [x] 3.4 Repeat the writes with two tags of the same type on one reader (1A/1B, 1C/1D).

## 4. Operator control

- [x] 4.1 Add `VO_BOX_RFID_WRITE` and its **Write spool tag** dialog on the Valve Oil UI's QIDI Box screen.
- [x] 4.2 Try the dialog on the Plus 5, on an empty slot and a loaded one.
- [x] 4.3 Keep `VO_BOX_RFID_TEST` as a diagnostic command, without its card, and add the **Write spool tag** dialog to the Fluidd fork's QIDI Box card.
- [x] 4.4 Release the Fluidd fork with the dialog (`v1.37.7-tltg-a9fb708`) and pin it in `installer/package.yaml`.

## 5. Release

- [x] 5.1 Update `docs/qidi-box.md`, `docs/settings.md`, and `docs/valve-oil-ui.md`.
- [x] 5.2 Bump the package version and add the `CHANGELOG.md` section.
- [x] 5.3 Run `openspec validate --all --strict`, the installer core tests, the Valve Oil UI checks, and the bundle smoke test.
