## 1. Signature catalog and log checker

- [x] 1.1 Build the signature catalog module, with entries for dropped extruder pulls inside a cancelled part, a Box slot step clock changed by an auto-feed re-arm during a feed, an unload stopped with the toolhead sensor never clearing, and QIDI's `QDE_` codes with the events just before them.
- [x] 1.2 Write the streaming checker with its size and time budget, and a developer script that runs it on an attached log.
- [ ] 1.3 Test each signature against an operator log that shows it and one that doesn't, with the logs trimmed to the lines needed and hardware identifiers removed. Done for the step clock, the re-arm, the stuck unload, and QIDI's codes; the cancelled-part pulls are tested with the lines `box_flow_recovery` writes, until a log that shows them exists.

## 2. Live watchers

- [x] 2.1 Box slot step clocks: handle `last_step_clk` and `STEP-set_last_step` on each Box MCU, keep each slot's last good clock, and write a `vo_` line naming the slot and the offset in seconds when it changes outside a host reset. Stand down if QIDI handles those messages.
- [x] 2.2 Auto-feed re-armed while a feed is running: wrap QIDI's `_on_state` and `auto_start` and log the slot when it happens.
- [x] 2.3 The Box switching a slot motor's enable pin, or running feed steps, during Valve Oil's jog, feed motion, spool roll, and RFID moves.
- [x] 2.4 Move the Box buffer feed counter from log parsing to a handler or wrapper.
- [x] 2.5 Count Box replies nothing on the host handles, by message name, and log the count at each print end.
- [x] 2.6 Raise printer warnings only for faults Valve Oil doesn't repair before they fail the next operation. None of the first watchers' faults qualifies, so they raise none.
- [ ] 2.7 Confirm each watcher on the Max 4 with the step-clock reproduction from the auto-feed observations.

## 3. Diagnostics in Valve Oil UI

- [x] 3.1 Add a `vo-status` endpoint that runs the checker on the printer's logs.
- [x] 3.2 Add the Diagnostics screen: findings in plain language, what to do, the docs link, and a downloadable report. Text in English, Spanish, and Russian.
- [x] 3.3 Decide the open questions in `design.md`.

## 4. Vendor audits

- [x] 4.1 Slot step clocks and positions: finish the edge cases left open by the auto-feed findings, a feed starting while host moves are queued and the per-Box shared save slot.
- [x] 4.2 Slot motor enable pins: every place QIDI's firmware and host code switch them.
- [x] 4.3 Motion queue ownership of each slot stepper across load, unload, auto-feed, and restart.
- [x] 4.4 Saved variables written by more than one module.
- [x] 4.5 The toolhead board's and mainboard's custom commands, starting with the closed-loop drivers.
- [x] 4.6 Box replies nothing on the host handles.
- [x] 4.7 Record each audit in `openspec/observations/` with evidence qualifiers, and add a watcher and signature for each fault it finds.

## 4a. Fixes the audits call for

4a.1 to 4a.10 shipped in 26.11.22.3 with their own spec changes, and 4a.11 with this change; the audit notes are in `openspec/observations/`.

- [x] 4a.1 Before the unload's step-clock reset, abort auto-feed with `MCB_AUTO_ABORT`, which always reaches the Box. `auto_abort()` sends nothing once QIDI's own abort has cleared `bind_stepper`, so an abort QIDI's unload landed in the Box's 10 ms coast leaves auto-feed armed through the pull-back.
- [x] 4a.2 Stop QIDI's `auto_start` from re-arming auto-feed on the slot it is already bound to, as the start G-code's `T` command does after the purge. That re-arm is what put the 2026-11-22 slot clocks off.
- [x] 4a.3 Abort auto-feed and reset the slot's step clock before Valve Oil's own slot moves (jog, RFID turn, stranded-filament retract), and arm it again afterwards when the slot was bound.
- [x] 4a.4 When a Valve Oil unload stop raises out of QIDI's unload, unsync the slot from the extruder and switch its motor off in Klipper, as QIDI's flow does on success.
- [x] 4a.5 `vo_homing_guard`: don't query the X/Y drivers while a closed-loop motor upgrade is running (`cl_interface.is_any_upgrading()`).
- [x] 4a.6 After a failed X/Y touch, run QIDI's recover script (drivers back to run current and the stall detection off) before the error is raised, in Valve Oil's homing too.
- [x] 4a.7 Compare retained filament IDs as strings: `'QFA94'|int` is 0, so the keep-loaded check treats every material as the same.
- [x] 4a.8 `restore.sh`: restore `saved_variables.cfg` with Klipper stopped, or Klipper's next save undoes it.
- [x] 4a.9 Valve Oil UI's "Detection on insertion" toggle: send `SET_ENABLE_RFID_READ` so RS-485 Boxes get it too.
- [x] 4a.10 After a QIDI firmware update, re-quote or drop the `saved_variables.cfg` lines QIDI's `postinst` writes without quotes, which stop Klipper from starting.
- [x] 4a.11 `vo_box_watch`: after a step-clock reset, compare the Box's next clock with Klipper's last step instead of taking it as the new baseline.

## 5. Release

- [x] 5.1 Docs: `docs/troubleshooting.md`, `docs/valve-oil-ui.md`, and `docs/under-the-hood.md`.
- [x] 5.2 Bump the package version and add the `CHANGELOG.md` section for what ships.
- [x] 5.3 Run `openspec validate --all --strict`, the installer core tests, the UI checks, and the bundle smoke test.
