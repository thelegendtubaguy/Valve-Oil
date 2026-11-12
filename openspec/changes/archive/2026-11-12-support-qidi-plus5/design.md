## Context

Evidence lives in `openspec/observations/qidi-platform.md` under QIDI Plus 5. In short:

- **Same as the Max 4:**
  - Board images: MCU, THR, BOX, BOX485, and BUFFER in `01.01.02.01` are byte-identical to Max 4 `01.01.07.01`. A stock Plus 5 runs mainboard `02.01.01.12` (STM32F407) and THR `02.02.01.08` (STM32F103xE on `/dev/ttyS4`).
  - Host: RK3308BS board with 486 MB RAM, the same partition names, and kernel 5.10.160 from the same builder and toolchain. The build is `#3 … Aug 23`, not the Max 4's `#4 … Sep 3`.
  - Host defects: the unauthenticated `adbd` root shell on port 5555 and the `rockchip.service` `rk3208` failure that leaves `/` mounted `sync`.
  - Klipper: 228 of 234 `klippy` files, Fluidd, the Klipper and Moonraker virtualenvs, `box.cfg`, the `[box485]` stack, and the SOC package's maintainer scripts. Those scripts also wipe the config directory on update, as on the Max 4.
- **Different:**
  - `printer.cfg`:
    - X/Y: open-loop TMC2240 with sensorless homing.
    - Travel and Z: travel X −7–326 and Y −5–340, Z 310, Z `rotation_distance` 4.
    - Extruder: direction inverted, current doubled.
    - THR pins: filament switch and hotend fan move to other THR pins.
    - Limits: `max_velocity` 600 and `max_accel` 20000.
  - Stock macros: homing, `M84`, park, cutter, wiper, and fans.
  - Klipper files: `print_stats_manager.py` without the `tpwmthrs` reset, `stepper_enable.py` without the 0.3 s early enable, and changed `box_extras.so` and `box_stepper.so`.
  - Screen and camera: a 480×800 framebuffer behind a landscape-mounted screen, and an icSpring camera at 640×480.
  - AI detection: none.
  - Slicer start G-code: different, using `M1006 Z`.
- **Stock Plus 5 firmware history:** `01.01.01.06` and `01.01.01.07` ship no BOX485 or BUFFER image. `01.01.02.01` is the current release.

## Goals / Non-Goals

**Goals:**

- One installer that admits Plus 5 `01.01.02.01` with its own baseline, and leaves Max 4 behavior unchanged.
- The same optimized macro names on both printers, with Plus 5 internals derived from QIDI's Plus 5 macros.
- No Max 4-specific hardware assumption reaches a Plus 5: closed-loop commands, the X/Y belt-speed cap, zram modules, or THR and Box bootloader admissions.

**Non-Goals:**

- Plus 5 firmware `01.01.01.06` and `01.01.01.07`. Owners update to `01.01.02.01` first.
- New optimizations specific to the Plus 5. The first release ports existing behavior; Plus 5 tuning follows measurement.
- RS-485 Box support beyond what the Max 4 already has.

## Decisions

**D1. Develop on a feature branch, and admit only after a hardware pass.** Admission, stock snapshot, patch variants, and the Plus 5 macro folder land together, because a firmware listed in `printers:` is installable. Work that can't run without the printer stays on the feature branch until the hardware tasks pass. Work that is printer-neutral, such as screen rotation, can merge to `dev` earlier.

**D2. The baseline comes from `Qidi-Plus5-Defaults` commit `25001402`.** The snapshot is the `config/` tree of the `01.01.02.01` payload, laid out like `installer/stock/qidi-max4-defaults/`. Patch variants are computed against it. Where a Plus 5 value differs from the Max 4 value a patch expects, the patch either gets a Plus 5 variant or doesn't apply to the Plus 5. It never matches on the Max 4 value.

**D3. Gate on hardware facts, not on the printer name, where a fact exists.**
- The step-loss monitor already stays inert without `cl_interface`.
- zram already loads only on a declared kernel build string. The Plus 5's `#3` differs from the Max 4's `#4` only in its build string and the bytes it shifts, so both are declared.
- THR and Box fixes already require a recognized bootloader. The project Plus 5's THR has the Max 4 test printer's `46358f30…` build and the same stock application, so the THR fix is offered on Plus 5 `01.01.02.01` as on Max 4 `01.01.07.01`. Other Plus 5s are taken to carry the same toolhead board and bootloader until one reports otherwise; an unrecognized build is still refused before reset.
- The 565 mm/s cap on `max_velocity` comes from the closed-loop motors' belt-speed limit, so that patch is Max 4-only. The Plus 5 keeps QIDI's 600 unless measurement says otherwise.
- Z tilt and bed mesh `speed` are not a hardware limit: QIDI's 150 mm/s is the same on both printers. The Plus 5 gets the Max 4's 565 mm/s, which is below its own top speed and is the shared chute travel speed it already runs. Probing travel already uses each printer's `max_accel` (20000 on the Plus 5), which homing restores.
- The mainboard closed-loop fix is offered only on printers with closed-loop X/Y.

**D4. Plus 5 homing follows the Max 4's shape with QIDI's currents.** QIDI's sequence is: both drivers at 0.9 A, `G28.6245197` per axis, staging moves of X −5 and Y +5 mm after a 5 mm bed drop, a 2 s settle, Y-X-Y, and 10 mm backoffs. As on the Max 4, whose X/Y also home by motor stall, the optimized full home only drops the bed, then homes Y and X once each with QIDI's currents and backoffs; the staging moves, their settle, and the second Y home are dropped. Sensorless homing depends on current and speed, so no speed or current change ships without repeated homing checks. X/Y `homing_speed` goes from QIDI's 50 to 55 mm/s, single touch (`homing_retract_dist: 0`). Measured as the CoreXY step count at X160 Y160 after each home, from mid-bed and from 10 mm off both endstops: 50 mm/s (18 homes) and 55 mm/s (12) landed within 0.24 mm on both axes; 60 mm/s had 1 of 12 Y homes and 65 mm/s 2 of 9 stop about 0.6 mm (100 microsteps) early.

**D4a. What the Plus 5 port changes from QIDI's macros.**
- `G28 X`, `G28 Y`, and `G28 Z` home that one axis with the same per-axis steps. QIDI's override ignores them, and the common macros expect them to work.
- Homing clears the bed mesh, as on the Max 4. It doesn't call QIDI's undefined `BEEP`.
- The cut drops QIDI's 2 s exit dwell, as on the Max 4.
- The cut follows the Max 4's shape: the extruder motor is released and settles 400 ms, then the toolhead goes diagonally to X 15 Y −2 and presses the cutter at X −2 once. From the chute it first leaves straight forward to Y 320. The press keeps QIDI's Plus 5 speed, F5000 from X 15, because the Plus 5's X motor is open-loop and the Max 4's 333 mm/s strike could cost it steps. QIDI's Plus 5 cut pressed the cutter twice with a 1 s hold after each press.
- The move to the waste chute goes from the bed straight to the chute entrance at X 77 Y 320 in one move at the shared `trash_travel_speed_xy` (565 mm/s) and `trash_travel_accel` (20000), then pushes into Y 338 with acceleration 10000 at the shared `trash_final_approach_speed_xy`. From the chute outside X 76–110 it first leaves straight forward to Y 308. QIDI's Plus 5 move went back to Y 308 at the toolhead's X, then across to X 77, then slowly for the whole 30 mm into the chute; after an X/Y home that meant sliding back along the right side. QIDI's own `MOVE_TO_TRASH`, which its tool change, unload, pause, and cancel call, is replaced by this move on the Plus 5 and by QIDI's path on the Max 4, so both printers keep one macro under the same name. On both printers the run to the chute entrance (Y 320 on the Plus 5, Y 383 on the Max 4) uses the shared chute travel speed and acceleration in place of QIDI's 200 mm/s and 10000, and the move leaving the chute and the push into it keep QIDI's speeds with acceleration 10000.
- Every other coordinate, speed, current, and dwell is QIDI's Plus 5 value, apart from the 55 mm/s X/Y homing speed in D4, the chute travel speed, and the Z tilt and bed mesh travel speed. Both chute moves push in at the shared `trash_final_approach_speed_xy`.
- Of the printer.cfg patches, the Plus 5 gets X/Y homing speed 55 mm/s, Z homing retract 5 mm, Z tilt and bed mesh travel at 565 mm/s, the error cancel macro, the hotend-fan tachometer interval, and quiet timelapse frames. Its own patch adds `stepper_x` and `stepper_y` to the mainboard fan's `controller_fan` steppers: QIDI ran the fan only with Z, while the TMC2240 X/Y drivers on the mainboard warm up whenever X/Y are on.
- The X/Y drivers get `driver_SLOPE_CONTROL: 2` from `[tmc2240 stepper_x|y]` sections in the Plus 5's `printer_profile.cfg`, which Klipper merges into QIDI's sections (its config parser runs with `strict=False`). QIDI's line is commented out, so no guarded patch can set it, and `printer.cfg` stays untouched. Holding still with the mainboard fan on, the drivers ran X 89.6 / Y 82.0 °C at slope 0, 72.2 / 67.9 at 1, 63.6 / 60.9 at 2, and 60.0 / 58.3 at 3, and back to 88.7 / 81.0 at 0. Moving, slope 2 ended 12–16 °C below slope 0. No value raised driver error flags, MCU retransmits, or accelerometer noise, and nothing was audible. Slope 2 homed X/Y as repeatably as slope 0 (within one full step on X), and `PROBE_ACCURACY` alternated between slopes 0 and 2 gave standard deviations of 0.0007–0.0017 mm for both. Slope 2 keeps most of the cooling with half slope 3's switching speed. Its Z homing is the same load-cell probe as the Max 4's.

**D4b. Slicer packs live in `plus5/` subfolders.** The Max 4 packs stay where operators' links point. The Plus 5 packs follow QIDI's Plus 5 profiles:
- Temperature compensation uses `M1006 Z`.
- The prime-line fallback is X180 to X140.
- Filament changes use QIDI's Plus 5 wipe.
- The OrcaSlicer pack turns the polar cooler on with `M106 P4`, as the Max 4's does: the Plus 5 can have one, on `output_pin polar_cooler`. The QIDI Studio packs carry no direct polar-cooler line on either printer.

**D4c. Box motion on the Plus 5 goes through QIDI's value lookups.** QIDI's Plus 5 `box_stepper.so` keeps the Max 4's three-stage load and two-stage unload methods but never calls them. Traced on a Plus 5 with a USB Box, a load is a `do_home` to the hub sensor at `extruder_load_length_1_speed` (85 mm/s), one `do_home` to the toolhead sensor of `extruder_load_length_2` (720 mm at 100 mm/s, acceleration 100), and, when that stops short, a `do_home` of up to 250 mm at `extruder_load_length_3_speed` (25 mm/s). An unload pulls the extruder back (`exl_2`, 100 mm), does one `do_home` back to the hub at `extruder_unload_length_1_speed` (100 mm/s, acceleration 50), and moves `extruder_unload_length_2` (−260 mm) at 80 mm/s to park. QIDI reads each value through `box_extras.get_value_by_key(name, default)` at the time of the move, so the Plus 5's settings answer those lookups and have no per-slot overrides. The Max 4 reads its hub push and park speeds through the same lookups, so `HUB_SPEED` and `PARK_SPEED` work the same way on both. On two loads the 720 mm push ran its full length and the 25 mm/s approach covered about 125 mm more, so the Plus 5 release default is an 850 mm push. The Max 4's release approach speed is the Plus 5's 25 mm/s, which reaches the same toolhead sensor.

**D5. The screen card rotates in the service.** The service knows the framebuffer's geometry and the touchscreen's axis ranges. A portrait framebuffer on a printer whose UI is landscape is rotated before encoding. Taps are mapped back through the same rotation, and the touchscreen's own axis ranges decide whether the touch coordinates are portrait or landscape. Any portrait framebuffer is turned by one constant, `PORTRAIT_UI_ROTATION`. Its value is 90° clockwise, the direction observed on the project Plus 5, whose touchscreen reports framebuffer coordinates.

**D6. Plus 5 rear moves stay in the chute window.** Behind Y 320 the Plus 5's toolhead can only be between X 75 and X 231; the Max 4's rear is open in X, so Max 4 rear moves such as its rear-bed scrape's X 380 swing don't carry over. That window is the Plus 5's only rear constraint: it has no rear-bed cable collision like the one the Max 4's rear-bed scrape avoids. The Plus 5 chute, wiper, and rear-bed scrape moves keep QIDI's Plus 5 positions (X 77–169), and a contract test walks every Plus 5 motion macro and slicer pack move to hold them inside the window.

## Data to collect on a running Plus 5

All read-only unless noted. Results go into the Plus 5 observations.

| Question | How | Unblocks |
|---|---|---|
| Kernel `#3` against the zram modules built for `#4` | `boot` partition image and `/proc/kallsyms` (sudo) | zram on the Plus 5 |
| UI rotation direction | capture `/dev/fb0` while the screen shows a known page | screen card |
| Touchscreen axis ranges and orientation | `EVIOCGABS` on `event0`; record one tap at a known corner | screen card taps |
| THR bootloader build | `flash_thr_firmware.py snapshot` (stops Klipper and the screen) | THR FIFO fix |
| Box bootloader build, if a Box is attached | `flash_box_firmware.py snapshot` | Box shutdown fix |
| Stock `printer.cfg` against the live file | diff, with serials redacted | patch variants, preflight |
| Stock homing duration and reliability | timed `G28` repeated, `klippy.log` | D4 |
| Cutter, wiper, chute, and park clearances | watch stock `CUT_FILAMENT`, wipe, and chute moves | macro folder |
| Load cell responds like the Max 4 | `WEIGHTING_DEBUG_QUERY`, `PROBE_ACCURACY` | probe diagnostics |
| Idle and printing host load, memory | the main-thread and memory measurements used on the Max 4 | zram need, priorities |
| Fan and chamber objects | `printer/objects/list` | cooling and exhaust guard |
| Box behavior with Plus 5 `box_extras.so` | harness first, then a load, unload, and tool change if a Box is attached | Box modules |
| Mainboard and THR board revisions | photos of board silkscreen | observations only |

## Risks / Trade-offs

- **[`box_extras.so` changed with the same strings]** → Package Box modules wrap its methods. Compare both binaries in the vendor harness before admission. Any wrapped method whose behavior differs gets a Plus 5 decision.
- **[Sensorless homing is sensitive to current and speed]** → Port stock values unchanged (D4).
- **[A patch that matches by section and option can still mean something else on the Plus 5]** → Variants are computed from the Plus 5 snapshot (D2), and the lifecycle matrix runs every patch against both printers' snapshots.
- **[One project printer]** → Admission waits for the hardware pass. Owner reports from issue 190 cover only read-only data.
