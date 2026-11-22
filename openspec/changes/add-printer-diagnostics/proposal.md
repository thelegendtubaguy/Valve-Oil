## Why

QIDI's code changes printer state behind Klipper's back, and nothing notices until a print fails. On 2026-11-22 two such faults reached users at once: Klipper's object exclusion dropped a filament change's moves inside a cancelled part, and QIDI's Box auto-feed left a slot motor's step clock up to 51 s off Klipper's, so unloads stuck after long prints. Both were in operators' `klippy.log`s for days. They were found only by reading logs line by line and taking QIDI's Box firmware and host modules apart, then confirmed by reproducing them on a printer (`openspec/observations/qidi-box/hardware-and-protocols.md`, "Auto-feed and the slot's step clock"; `openspec/observations/qidi-platform.md`, "Cancelled parts and filament changes"). Each fault left a clear signature in the log well before anyone was hurt by it.

## What Changes

- Valve Oil UI gets a **Diagnostics** button. It checks the printer's logs for every known fault signature and shows what it found in plain language, with what to do and a link to the docs, plus a report to attach to an issue. The same checker runs on a log someone sends in.
- Valve Oil's Klipper modules watch for the faults themselves and write a `vo_` line in `klippy.log` the moment one happens, with a printer warning when the operator has to act. The first watchers cover the Box slot step clocks, auto-feed re-armed during a feed, the Box switching a slot motor during Valve Oil's own slot moves, extruder pulls Klipper didn't move, and Box replies nothing on the host handles.
- A standing audit of QIDI's firmware and host modules, one shared resource at a time, records in `openspec/observations/` every way QIDI changes that resource and every place that assumes it hasn't. Each fault it finds gets a watcher and a signature.
- A Max 4 soak run with randomized timing checks the same invariants after every step, not only whether loads and unloads finish.

## Capabilities

### New Capabilities

- `printer-diagnostics`: the fault signatures, the Diagnostics check and report, and the live fault lines and warnings.

### Modified Capabilities

None. Fixes for the faults the program finds change `optimized-printer-behavior` in their own changes.

## Impact

- `installer/runtime/`: the signature catalog and the log checker, shared by the `tltg-status` service and a developer script.
- `installer/klipper/extras/`: fault watchers in the Box modules, beside the unload logging in `box_flow_recovery.py`.
- `ui/`: the Diagnostics screen, in English, Spanish, and Russian.
- `scripts/`: the log checker for attached logs and the soak runner.
- `openspec/observations/`: one audit section per shared resource.
- `docs/troubleshooting.md`, `docs/valve-oil-ui.md`, `docs/under-the-hood.md`, and `CHANGELOG.md` when each part ships.
