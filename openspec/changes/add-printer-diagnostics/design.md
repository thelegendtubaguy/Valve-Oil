## Context

Both faults of 2026-11-22 had log signatures:

- **Dropped moves.** A Box unload whose extruder pull-backs "finished" in 0 s, because Klipper dropped them inside a cancelled part.
- **Slot step clock.** In QIDI's Box auto-feed replies (`last_step_clk`, `STEP-set_last_step`), a slot's saved clock changed in the second print start re-armed auto-feed during a feed (`[MCB] STATE` 2), then stayed off for the rest of the print.

QIDI's host code doesn't handle those replies, so Klipper logs them raw as `mcu 'mcu_boxN': got {...}`. Reading them took an hour per log by hand. An operator's `klippy.log` runs to 20–90 MB, and the printer has four slow cores and 486 MB of memory. A full scan with `awk` has run for minutes on the printer.

`tltg-status` already serves Valve Oil UI's Updates screen and builds the **Report a problem** support bundle.

## Goals / Non-Goals

**Goals:**
- A fault QIDI's code causes shows up in `klippy.log` as a `vo_` line when it happens, in words, before it fails a print where possible.
- An operator can check their own printer from Valve Oil UI.
- A log someone sends gets the same check in seconds.
- Every fault found becomes a permanent signature.

**Non-Goals:**
- Fixing faults in this change. Each fix goes to `optimized-printer-behavior` in its own change.
- Telemetry or anything that leaves the printer without the operator sending it.
- Watching Klipper's own behavior where QIDI hasn't changed it.

## Decisions

### Watchers write their own lines; the checker reads those first

Each watcher runs in the Valve Oil Klipper module that owns the subsystem and writes one `vo_<module>:` line per event, with the numbers needed to judge it. The checker then mostly needs those short lines, found with a bounded scan from the last `Start printer at`, or a time window the operator picks. It reads QIDI's raw lines only for signatures that have no watcher, such as logs from releases before the watcher shipped.

### Catch Box replies with handlers where Klipper allows it

Klipper modules can register a response handler for a message name on an MCU. QIDI's code leaves `last_step_clk` and `STEP-set_last_step` unhandled, so a Valve Oil handler gets them directly, with no log parsing. Doing so stops Klipper's raw `got {...}` line for those messages, so the handler writes the same numbers in its own `vo_` line.

Where QIDI does handle a message, such as `MCB_STATE` (`_on_state`), the watcher wraps QIDI's method the way Valve Oil's Box modules already wrap theirs. The Box buffer counter shipped in 26.11.22.2 parses QIDI's log line instead. It moves to a handler or wrapper once one is proven on the printer.

### One signature catalog, two runners

Signatures live in one Python module, run by `tltg-status` on the printer and by `scripts/` on a developer's machine. Each entry gives:
- its id;
- the log lines that show it, the `vo_` line first and QIDI's raw lines as fallback;
- what it means, what the operator should do, and which release fixed it, if any;
- a docs anchor.

The checker streams the file and never holds it in memory, stops at a size or time budget, and runs at the service's low priority.

### Warnings only when the operator must act

A watcher raises a printer warning only for a state that will fail the next operation and that Valve Oil doesn't already repair. For example, after 26.11.22.2 a slot clock that's off is reset before the next unload, so the watcher logs it but doesn't warn.

### Audits are recorded, not shipped

Each audit pass takes one resource QIDI's code and Valve Oil both touch:
- the slot step clocks and positions;
- the slot motors' enable pins;
- which motion queue owns each slot;
- saved variables written from more than one place;
- the toolhead board's and mainboard's custom commands;
- Box replies nothing on the host handles.

A pass works from the Box, toolhead-board, and mainboard firmware images, and QIDI's `.so` files under the harness. It records each finding in `openspec/observations/` with its evidence qualifier. A finding becomes a requirement only once it's confirmed on a printer.

### The soak checks invariants

The soak runner drives a Max 4 through Box cycles: print starts whose purge pulls the buffer, pauses, cancelled parts, restarts, and power cuts as in the Max 4 stranding campaign. After every step it checks the watchers' lines and the signatures, as well as whether the step finished. It runs unattended over SSH.

## Risks / Trade-offs

- **Box replies nobody handles yet.** Registering a handler for a reply QIDI starts handling later would take it from QIDI. Each handler checks at start-up that the message is still unhandled, and stands down if it isn't.
- **Log size and CPU.** Scans are bounded and run at low priority. The checker never runs on its own during a print.
- **False alarms erode trust.** A signature ships only with a log that shows it and a log that doesn't.

## Open Questions

- Whether Diagnostics also goes in the Fluidd fork's settings, or only in Valve Oil UI.
- How far back the checker looks by default: since the last start, the last day, or the whole file.
- Whether the support bundle includes the Diagnostics result automatically.
