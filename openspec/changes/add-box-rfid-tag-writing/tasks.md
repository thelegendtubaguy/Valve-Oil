## 1. Decisions

- [ ] 1.1 Choose the NTAG layout, the command name and arguments, and the Fluidd control.

## 2. Writing

- [ ] 2.1 Write the QIDI v1 block to MIFARE Classic 1K tags and the chosen layout to NTAG213/215/216 tags through `fm17550_select` and `fm17550_transceive`, under the reader's mutex.
- [ ] 2.2 Read each write back and report the result; refuse locked tags, Bambu Lab tags, tags with another brand's data, and Boxes without the Valve Oil Box firmware.

## 3. Hardware

- [ ] 3.1 Write and read back genuine NXP and clone MIFARE Classic 1K tags, and NXP NTAG213, 215, and 216, on a test Box.
- [ ] 3.2 Check that a written spool sets its slot through QIDI's insert read, and that a MIFARE Classic tag written this way reads on QIDI's stock Box firmware.

## 4. Fluidd

- [ ] 4.1 Add the control to the Box card in the Fluidd fork and pin the release in `installer/package.yaml`.

## 5. Release

- [ ] 5.1 Update `docs/qidi-box.md` and `docs/settings.md`.
- [ ] 5.2 Bump the package version and add the `CHANGELOG.md` section.
- [ ] 5.3 Run `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
