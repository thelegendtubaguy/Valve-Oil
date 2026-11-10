## Why

Owners can't tell how long their printer takes to reach printing temperature or how long the bed keeps moving afterwards. The bed and the frame keep expanding well after the bed reaches its target. The nozzle is both the probe and the Z endstop, so a bed still moving after the print-start mesh changes the first layer. Today people guess at a wait or copy someone else's. The Soak My Bed Klipper plugin answers this by measuring meshes while the bed heats and plotting them afterwards. This change builds the same idea as Valve Oil's own code, with live results in Fluidd, chamber temperatures, and runs saved for comparison.

## What Changes

- Add an operator-run bed soak test. It measures one mesh before heating, then heats the bed and, optionally, the chamber to the chosen temperatures and measures meshes until the bed stops moving or a time limit is reached.
- Report how long the bed and the chamber took to reach temperature, when the bed stopped moving, and when the printer was ready to print. For each mesh it also reports how far the bed moved in height, how much its shape changed, and how fast it was still moving.
- Save each run so runs at different bed and chamber temperatures can be compared.
- Flag probe points that needed retries or don't move with the rest of the bed, and probe readings that jump, and keep them out of the results.
- Record the retried points of every bed mesh, print start's included, and mark them on Fluidd's Bed mesh card.
- Make probe retries stop at `samples_tolerance_retries` and fail as upstream Klipper does. QIDI's code resets the count on the first retry, so a point whose samples never agree is probed forever. This applies to print start too, which now fails instead of hanging.
- Leave the printer as it was found. No saved mesh profile changes and no config save is left pending. The previously loaded mesh is restored, and the heaters turn off unless the operator asks to keep them on.
- Add a Bed soak card under Bed mesh on the Fluidd fork's Tune page. It sets up the test, follows it live, shows the results, and lists past runs.
- Inform only. Print start, its waits, and its mesh are unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: the bed soak test, problem points from any bed mesh, and the probe retry limit.

## Impact

- `installer/klipper/tltg/klipper_fixes.py`, and new `installer/klipper/tltg/bed_soak.py`, `installer/klipper/tltg/probe_report.py`, and `installer/klipper/tltg-optimized-macros/bed_soak.cfg`.
- The Fluidd fork's Tune page and Bed mesh card, and the pinned `system_optimizations.fluidd_build` in `installer/package.yaml`.
- Run files under `~/printer_data/config/tltg_bed_soak/`.
- `docs/fixes.md`, `docs/tools.md`, `docs/settings.md`, `docs/under-the-hood.md`, and `CHANGELOG.md`.
- No stock config, slicer G-code, or firmware change.
