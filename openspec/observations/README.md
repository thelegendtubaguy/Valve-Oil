# External observations

`openspec/observations/` records vendor, firmware, hardware, and runtime behavior that this repository does not control.

Observation text is evidence, not desired product behavior. Desired behavior belongs in `openspec/specs/`; machine-checked repository behavior belongs in `openspec/contracts/`.

Evidence qualifiers:

- **Runtime-confirmed**: captured from live config, Moonraker status, or a controlled before/after command run.
- **Config-confirmed**: present in captured stock config or generated source.
- **Harness-recovered**: emitted by compiled vendor code running against fake Klipper objects; constants and script shape are stronger than branch predicates.
- **Static-recovered**: found in compiled symbols, strings, or disassembly; proves presence or ownership, not execution.
- **Unresolved**: evidence is insufficient to claim behavior.

## Files

- `qidi-platform.md`: firmware, services, Moonraker, Fluidd, and platform behavior.
- `qidi-client-account-bind.md`: touchscreen account binding and the login QR code.
- `moonraker-file-manager.md`: QIDI's Moonraker file manager.
- `qidi-box/`: QIDI Box hardware, protocols, firmware, and state.

When evidence conflicts, live runtime evidence outranks fake harness behavior, and harness behavior outranks static naming. Hardware motion, heating, autofeed, RFID, retry, and failure-path assumptions require controlled live validation before repository behavior is changed.
