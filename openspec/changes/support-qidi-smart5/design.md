## Context

Evidence lives in `openspec/observations/qidi-platform.md` under QIDI Smart 5, all of it from QIDI's `01.01.02.01` package. In short:

- **Same as the Plus 5:**
  - Host software: Fluidd, both virtualenvs, `system_stats`, `/etc` files, the SOC package's maintainer scripts, and all but three Moonraker binaries.
  - Box stack: the BOX, BOX485, and BUFFER images, `box.cfg`, `[box485]`, and most Box extras.
  - THR family: `stm32f103xe` on `/dev/ttyS4` with the same pins for the filament switch, hotend fan, accelerometer, and nozzle load cell.
  - Klipper: everything but 23 files, CRLF `.cfg` files, and the same macro library.
- **Different:**
  - Mainboard: an i.MX RT1050 on `/dev/ttyS1` running QIDI's `02.01.01.110`, with no `[include MCU_ID.cfg]`.
  - Motion: X, Y, and Z run through `cuc.so` with no TMC objects; X and Y home toward their minimums by motor stall with QIDI's checked second homing (`SET_SECOND_HOMING`, `before_homing_gcode`, `after_homing_gcode`).
  - Geometry: travel X −2–185 and Y −4–200, one Z motor up to 182 mm, `max_velocity` 400, no Z tilt, chamber heater, auxiliary fan, or polar cooler, a bed limited to 85 °C.
  - Positions: chute at X 100 Y 199 with its wiper along Y 180–199; cutter at the front right (X 175–180 at Y 1); scrape strip behind the bed at X 79–95, Y 185–187.
  - THR firmware `02.02.01.09`, whose code moved; the FIFO bug remains but the existing fix doesn't apply.
  - `homing.py` (`9c2d0a47…`), `stepper_enable.py` (the Max 4's early enable), `box_extras.so`, `box_stepper.so`, and `multi_color_controller.so`.
- **SOC version shared with the Plus 5:** both ship `01.01.02.01`, released the same day.

## Goals / Non-Goals

**Goals:**

- One installer that admits Smart 5 `01.01.02.01` with its own baseline, and leaves Max 4 and Plus 5 behavior unchanged.
- The same optimized macro names on every printer, with Smart 5 internals from QIDI's Smart 5 macros.
- No Max 4 or Plus 5 hardware assumption reaches a Smart 5.

**Non-Goals:**

- New optimizations specific to the Smart 5. Homing, cut, and speed tuning follow measurement on the project printer.
- A THR FIFO fix for `02.02.01.09` or any mainboard firmware work.
- Earlier Smart 5 firmware.

## Decisions

**D1. Develop on a feature branch, and admit only after a hardware pass.** As with the Plus 5, admission, the snapshot, patch variants, and the macro folder land together, because a firmware listed in `printers:` is installable. Printer-neutral pieces such as firmware keys and the Z tilt guard can merge earlier.

**D2. Firmware keys tell printers that share a SOC version apart.** QIDI numbers each printer's firmware separately, so the version alone can't identify the printer. A printer may declare a `firmware_key_prefix`, and every firmware it lists must carry it. The installer finds the printer from the firmware manifest's SOC file name and keys the version with that printer's prefix. The key is what state records, backups, patch variants, source-patch provenance, and the THR gate use. Stock snapshot folders keep QIDI's version. The Max 4 and Plus 5 have no prefix, so their recorded state, backups, and variants stay valid. A manifest without a SOC file name identifies a printer only when one printer lists that version. Considered instead: printer-scoped patch variants with bare versions. That needs a printer argument through every variant lookup and a rule that variants naming a shared version say which printer they mean. Missing that rule would let a Plus 5 value apply to a Smart 5.

**D3. The baseline comes from `Qidi-Smart5-Defaults` commit `c8e9da1`.** The snapshot is the payload's `config/` tree in QIDI's line endings, with copyright e-mail addresses redacted. Variants are computed against it. A Plus 5 or Max 4 variant whose value differs on the Smart 5 doesn't apply there. The Smart 5 gets:
- the error cancel macro;
- the hotend-fan tachometer interval;
- quiet timelapse frames;
- the toolhead filament sensor, `RESUME`, cut, chute, `M106`, `m109.0`, `_km_apply_print_offset`, and `homing_override` replacements.

Homing speeds, retract distances, Z retract, and speed limits stay QIDI's until measured. The board fan needs no change: every Smart 5 motor shares one enable pin.

**D4. Smart 5 homing is QIDI's sequence without its staging moves.** Each axis gets QIDI's single touch at QIDI's homing current (its `_BEFORE_HOME_*` and `_AFTER_HOME_*` macros), then QIDI's checked second homing, then QIDI's 20 mm backoff. X homes before Y, as in QIDI's macros. As on the other printers, a full home only lowers the bed 5 mm before homing; QIDI's X+5 and Y+5 moves and its `M84` before homing are left out. `G28 X`, `G28 Y`, and `G28 Z` home one axis, and homing clears the bed mesh. The `homing.py` source patch shortens only the 1 s dwell before each X/Y home to 0.25 s, as on the Max 4 and Plus 5. Whether the first single touch and the dwell are needed is checked on hardware.

**D5. Smart 5 nozzle moves keep QIDI's Smart 5 positions.**
- The cut is QIDI's: Y 1, X 175, a slow press to X 180 at F60, a 4 mm retract, the 2 s hold, and X 160. The optimized cut homes X/Y if needed where QIDI's undefined `_CG28` did nothing.
- The chute move runs from the bed straight to the chute entrance at X 100 Y 179 at the shared chute travel speed and acceleration, then pushes in at the shared final-approach speed. From behind the bed outside X 80–100 it first leaves straight forward to Y 179, as QIDI's does.
- The wipe is QIDI's: Y 180–199 strokes into the chute, and its pad sweep across X 66–100.
- The rear-bed scrape probes the strip at X 79 Y 187 and scrapes 0.1 mm above the probed height, as QIDI's `CLEAR_NOZZLE` does, because the strip's height relative to the bed is unknown.
- Behind Y 180 the toolhead stays between X 66 and X 100, the range QIDI's own moves use; a contract test walks every Smart 5 motion macro and slicer pack move to hold it there.

**D6. Z tilt runs only where the printer has one.** The common print-start branches and the bed soak test run `Z_TILT_ADJUST`, with its sub-status and bed temperature report, only when Klipper has a `z_tilt` object. `TLTG_CORNER_BED_SCREW_CHECK` stops with a message where no screw positions are set up. Max 4 and Plus 5 behavior is unchanged.

**D7. Slicer packs live in `smart5/` subfolders and follow QIDI's Smart 5 profiles.**
- Compared with the Plus 5 packs, they drop the chamber arguments and `M141`.
- The prime line runs right to left from at most X 160 so it stays clear of the front-right cutter, with QIDI's X 110 → X 70 at Y 0 as the fallback.
- Filament changes keep QIDI's Smart 5 flush wipes, five chute strokes after each flush.
- The timelapse layer change leaves the chute to Y 179 instead of QIDI's out-of-range Y 380.

**D8. Gate features on what is known for the Smart 5.**
- THR FIFO fix: its gate uses firmware keys, so Smart 5 `01.01.02.01` isn't offered it. Its THR image needs its own fix.
- Mainboard closed-loop fix: it already requires a `QD_MAX4_` SOC file.
- zram: it already loads only on declared kernel builds, and no Smart 5 kernel is declared.
- 565 mm/s cap: it has no Smart 5 variant.
- Box motion: release defaults are empty for the Smart 5, and `tltg_box_feed_motion` already stays inactive on its unlisted `box_stepper.so`.
- Box firmware: the flasher already requires an admitted bootloader. Its stock-image lookup now accepts any printer's file prefix, including `S5_`.
- Klipper fixes: each replaces QIDI code only when its source hash matches. The Smart 5's sources match every fix the Max 4 gets, including the early-enable timing fix.

## Data to collect on a running Smart 5

All read-only unless noted. Results go into the Smart 5 observations.

| Question | How | Unblocks |
|---|---|---|
| Kernel build against the zram modules | `boot` partition image and `/proc/kallsyms` | zram on the Smart 5 |
| Framebuffer geometry, UI rotation, touch axes | capture `/dev/fb0`; `EVIOCGABS` on `event0` | screen card |
| THR bootloader build | `flash_thr_firmware.py snapshot` (stops Klipper) | a `02.02.01.09` THR fix |
| Stock `printer.cfg` against the live file, `MCU_ID.cfg` | diff, with serials redacted | patch variants, preflight |
| Stock homing duration and repeatability | timed `G28` repeated, `klippy.log` | D4 |
| Cutter, wiper, chute, scrape, and park clearances; extruder above the cutter | watch stock moves cold at Z 70 | D5, cold cut |
| Load cell and probe | `WEIGHTING_DEBUG_QUERY`, `PROBE_ACCURACY` | probe diagnostics |
| Idle and printing host load, memory | main-thread and memory measurements | zram need |
| Klipper objects | `printer/objects/list` | cooling and exhaust guard |
| Box load, unload, and tool change | Box tracer, then a load and unload | Box motion defaults |
| Bed screws | photos and positions | bed screw check |
| Board revisions | photos of board silkscreen | observations only |

## Risks / Trade-offs

- **[Firmware keys change what the installer records for a Smart 5]** → Keys only gain a prefix for printers that declare one; Max 4 and Plus 5 state and backups keep bare versions, and a test runs a full Smart 5 install and uninstall.
- **[Motor-stall homing on new drivers]** → QIDI's currents, speeds, touches, and retract distances are kept (D4); only the staging moves and the dwell change, and both are checked on hardware before admission.
- **[Positions copied from QIDI's macros, not measured]** → Every Smart 5 move uses a position QIDI's own macros use, and the rear-window test keeps rear moves inside QIDI's range.
- **[Common macros with Smart 5 guards]** → The guards test for the Klipper object, so the Max 4 and Plus 5 run the same commands as before; the start-print contracts still require `Z_TILT_ADJUST` on those printers.

## Open Questions

- Does the Smart 5's toolhead put the extruder above the cutter, as the cold cut assumes?
- Does the Smart 5's framebuffer use the Plus 5's portrait layout, so the screen card's rotation applies?
- Does QIDI's first single touch per axis matter for repeatability, or can the checked second homing stand alone?
