## 1. Signature catalog and log checker

- [ ] 1.1 Build the signature catalog module, with entries for dropped extruder pulls inside a cancelled part, a Box slot step clock changed by an auto-feed re-arm during a feed, an unload stopped with the toolhead sensor never clearing, and QIDI's `QDE_` codes with the events just before them.
- [ ] 1.2 Write the streaming checker with its size and time budget, and a developer script that runs it on an attached log.
- [ ] 1.3 Test each signature against an operator log that shows it and one that doesn't, with the logs trimmed to the lines needed and hardware identifiers removed.

## 2. Live watchers

- [ ] 2.1 Box slot step clocks: handle `last_step_clk` and `STEP-set_last_step` on each Box MCU, keep each slot's last good clock, and write a `vo_` line naming the slot and the offset in seconds when it changes outside a host reset. Stand down if QIDI handles those messages.
- [ ] 2.2 Auto-feed re-armed while a feed is running: wrap QIDI's `_on_state` and `auto_start` and log the slot when it happens.
- [ ] 2.3 The Box switching a slot motor's enable pin, or running feed steps, during Valve Oil's jog, feed motion, spool roll, and RFID moves.
- [ ] 2.4 Move the Box buffer feed counter from log parsing to a handler or wrapper.
- [ ] 2.5 Count Box replies nothing on the host handles, by message name, and log the count at each print end.
- [ ] 2.6 Raise printer warnings only for faults Valve Oil doesn't repair before they fail the next operation.
- [ ] 2.7 Confirm each watcher on the Max 4 with the step-clock reproduction from the auto-feed observations.

## 3. Diagnostics in Valve Oil UI

- [ ] 3.1 Add a `tltg-status` endpoint that runs the checker on the printer's logs.
- [ ] 3.2 Add the Diagnostics screen: findings in plain language, what to do, the docs link, and a downloadable report. Text in English, Spanish, and Russian.
- [ ] 3.3 Decide the open questions in `design.md`.

## 4. Vendor audits

- [ ] 4.1 Slot step clocks and positions: finish the edge cases left open by the auto-feed findings, a feed starting while host moves are queued and the per-Box shared save slot.
- [ ] 4.2 Slot motor enable pins: every place QIDI's firmware and host code switch them.
- [ ] 4.3 Motion queue ownership of each slot stepper across load, unload, auto-feed, and restart.
- [ ] 4.4 Saved variables written by more than one module.
- [ ] 4.5 The toolhead board's and mainboard's custom commands, starting with the closed-loop drivers.
- [ ] 4.6 Box replies nothing on the host handles.
- [ ] 4.7 Record each audit in `openspec/observations/` with evidence qualifiers, and add a watcher and signature for each fault it finds.

## 5. Soak

- [ ] 5.1 Write the soak runner for the Max 4: Box cycles with randomized timing, print starts that pull the buffer, pauses, cancelled parts, and restarts, checking the watchers' lines and signatures after every step.
- [ ] 5.2 Run it overnight on the Max 4 and record what it finds.

## 6. Release

- [ ] 6.1 Docs: `docs/troubleshooting.md`, `docs/valve-oil-ui.md`, and `docs/under-the-hood.md`.
- [ ] 6.2 Bump the package version and add the `CHANGELOG.md` section for what ships.
- [ ] 6.3 Run `openspec validate --all --strict`, the installer core tests, the UI checks, and the bundle smoke test.
