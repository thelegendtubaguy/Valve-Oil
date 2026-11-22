## Context

Both faults of 2026-11-22 had log signatures:

- **Dropped moves.** A Box unload whose extruder pull-backs "finished" in 0 s, because Klipper dropped them inside a cancelled part.
- **Slot step clock.** In QIDI's Box auto-feed replies (`last_step_clk`, `STEP-set_last_step`), a slot's saved clock changed in the second print start re-armed auto-feed during a feed (`[MCB] STATE` 2), then stayed off for the rest of the print.

QIDI's host code doesn't handle those replies, so Klipper logs them raw as `mcu 'mcu_boxN': got {...}`. Reading them took an hour per log by hand. An operator's `klippy.log` runs to 20–90 MB, and the printer has four slow cores and 486 MB of memory. A full scan with `awk` has run for minutes on the printer.

`vo-status` already serves Valve Oil UI's Updates screen and builds the **Report a problem** support bundle.

## Goals / Non-Goals

**Goals:**
- A fault QIDI's code causes shows up in `klippy.log` as a `vo_` line when it happens, in words, before it fails a print where possible.
- An operator can check their own printer from Valve Oil UI.
- A log someone sends gets the same check in seconds.
- Every fault found becomes a permanent signature.

**Non-Goals:**
- Fixing faults in this change. Each fix goes to `printer-behavior` in its own change.
- Telemetry or anything that leaves the printer without the operator sending it.
- Watching Klipper's own behavior where QIDI hasn't changed it.

## Decisions

### Watchers write their own lines; the checker reads those first

Each watcher runs in the Valve Oil Klipper module that owns the subsystem and writes one `vo_<module>:` line per event, with the numbers needed to judge it. The checker then mostly needs those short lines, found with a bounded scan from the last `Start printer at`, or a time window the operator picks. It reads QIDI's raw lines only for signatures that have no watcher, such as logs from releases before the watcher shipped.

### Read Box replies where Klipper hands over the unhandled ones

Klipper's serial reader gives each reply to the handler registered for its name and oid, and every other reply to its default handler, which logs it as `got {...}`. QIDI's code leaves `last_step_clk`, `STEP-set_last_step`, and `MCB_CONFIG_STEPPER` unhandled, so `vo_box_watch` wraps each Box MCU's default handler: it reads those three, counts every unhandled reply by name, and passes each on, so the raw lines stay in the log. It registers no handler of its own, so it never takes a message from QIDI's code; if QIDI's code starts handling one, the reply stops reaching the default handler and the watcher stands down for it without a check at start-up. The serial reader runs handlers in its own thread, so the wrapper only hands each reply to Klipper's reactor.

Where QIDI does handle a message, such as `MCB_STATE` (`_on_state`), the watcher wraps the handler QIDI registered on the serial reader, which still runs. The wrapper also counts the feeds each Box starts (`MCB_STATE` 2), which Valve Oil's line for each unload from the toolhead reports; the counter shipped in 26.11.22.2 parsed QIDI's log line for the buffer switch instead, which counts requests the Box ignores while its auto-feed is off.

A slot's clock changed behind Klipper when the Box reports a clock for it that differs from the one it last reported while Klipper's position of the slot, the clock of Klipper's newest step of it, and its homing ends are unchanged. Klipper's own step history holds only about 30 s, so the watcher keeps the newest step's clock rather than asking whether any step is recent: a re-arm in the seconds after a load's purge is still caught.

### One signature catalog, two runners

Signatures live in one Python module, run by `vo-status` on the printer and by `scripts/` on a developer's machine. Each entry gives:
- its id;
- the log lines that show it, the `vo_` line first and QIDI's raw lines as fallback;
- what it means, what the operator should do, and which release fixed it, if any;
- a docs anchor.

The checker streams the file and never holds it in memory, stops at a size or time budget, and runs at the service's low priority. The status service runs it from the installer bundle in `~/valve-oil`, which every install keeps, as a separate process under `nice` and `ionice`, so the service file stays one file and a check can't take the service's memory.

A finding's severity follows the release that ran when it happened, read from the `variable_package_version` line of the config Klipper logs at each start, and the release installed now: a fault the running release repaired is "repaired", one the installed release fixes is "fixed", any other is a "fault". A session that logged Valve Oil's own repair, such as a step clock reset before an unload, counts as repaired whatever release its config named, as a sideloaded build keeps the version it replaced. Raw QIDI lines are read only in a Klipper session without the watcher's start-up line, so a fault is never counted twice.

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

## Risks / Trade-offs

- **Box replies nobody handles yet.** Registering a handler for a reply QIDI starts handling later would take it from QIDI. Each handler checks at start-up that the message is still unhandled, and stands down if it isn't.
- **Log size and CPU.** Scans are bounded and run at low priority. The checker never runs on its own during a print.
- **False alarms erode trust.** A signature ships only with a log that shows it and a log that doesn't.

### Where Diagnostics lives, how far it looks, and the support bundle

- Diagnostics is in Valve Oil UI only, at the top of System → Diagnostics. The Fluidd fork can call the same service route later; nothing in the check depends on the UI.
- By default it reads the whole log within its budget, the newest 48 MB of `klippy.log` and the rotated logs beside it, since both faults of 2026-11-22 sat in logs for days. The operator can pick since Klipper last started, which seeks back to that start instead of reading the file, or the last 24 hours of the log, timed from its newest line rather than the printer's clock. On the Max 4 the whole-log check of 48 MB took 25 s, and since the last start under 1 s of reading.
- The support bundle carries the last Diagnostics report when there is one. It doesn't run a check, since the bundle already reads tens of megabytes of logs.
