# Tools

[← Back to the README](../README.md)

Run these from Fluidd's console or macro controls after installing and restarting Klipper.

| Command | What it does |
|---|---|
| [`TLTG_TEST_PROBE CONFIRM=1`](#nozzle-load-cell-check) | Checks that the nozzle load cell responds to light pressure. No motion. |
| `TLTG_PROBE_ACCURACY_CENTER [SAMPLES=20]` | Homes, moves to the bed center (`X195 Y195` on the Max 4) at `Z10`, and runs Klipper's `PROBE_ACCURACY`. |
| `TLTG_CORNER_BED_SCREW_CHECK` | Homes, runs `Z_TILT_ADJUST`, then `SCREWS_TILT_CALCULATE`. |
| `SCREWS_TILT_CALCULATE` | Klipper's bed screw adjustment helper. |
| `TLTG_RESET_TOOL_MAPPINGS` | Restores QIDI Box tool-to-slot mappings to T0 → 1A, T1 → 1B, and so on. Idle only. |
| [`TLTG_FILAMENT_SENSOR ENABLE=0`](#external-spool-runout-sensor) | Stops the external spool's runout sensor from pausing prints. |
| [`TLTG_BOX_MOTION`](qidi-box.md#load-and-unload-speed) | Shows or sets QIDI Box load and unload speeds. |
| [`SHAPER_CALIBRATE SWEEPING_PERIOD=0`](#input-shaper-calibration) | Input shaper calibration with QIDI's resonance test instead of the newer sweeping one, for one run. |

## Nozzle load-cell check

`TLTG_TEST_PROBE CONFIRM=1` checks that the nozzle load cell responds to light pressure. It does not move, home, heat, or change calibration. In Fluidd's macro controls, enter `1` in the `CONFIRM` field.

**Before you start:** the printer must be idle with all heaters off, and the nozzle and bed below 40 °C. Make sure you can reach the nozzle and it's clear of the bed. **Do not home to prepare for this test.**

Follow the console prompts. It takes about 40 seconds:

1. Leave the nozzle alone through `DO NOT TOUCH` and `Measuring...`.
2. At `GET READY TO PRESS`, get ready. At `PRESS`, press lightly upward on the nozzle and hold.
3. When told to stop pressing, let go and leave the nozzle alone through `DO NOT TOUCH` and `Measuring...`.

The result is one of:

- `RESPONSE_DETECTED`: the readings look correct.
- `NO_RELIABLE_RESPONSE`: the sensor did not respond as expected.
- `INCONCLUSIVE`: there was not enough difference between pressing and not pressing to tell, or the test stopped early.

> [!WARNING]
> **A detected response does not verify the homing trigger or certify safe homing.** For any other result, do not press harder, and do not home or probe until the cause is understood. `QUERY_PROBE` cannot check this sensor. `TLTG_PROBE_ACCURACY_CENTER` and `TLTG_CORNER_BED_SCREW_CHECK` both home, so they are not safe tests for a suspected failed sensor.

## External spool runout sensor

```gcode
TLTG_FILAMENT_SENSOR ENABLE=0
TLTG_FILAMENT_SENSOR ENABLE=1
```

`ENABLE=0` stops the external spool from pausing the print when its runout sensor trips. The toolhead sensor still reports its events and the external spool's runout status. `ENABLE=1` turns automatic pausing back on. The setting goes back to enabled when Klipper restarts. QIDI Box runout handling stays on either way.

> [!WARNING]
> With `ENABLE=0`, ignore filament-runout warnings on the printer screen. Don't interact with them; the print continues, and the warning only reports the sensor event.

The console shows when the toolhead sensor trips and whether it will pause.

![Filament sensor console output](../.github/images/tltg-filament-sensor-console.png)

## X/Y step-loss monitor

On the Max 4, X and Y are driven by QIDI's closed-loop motors. Each has an encoder and knows how far it is behind where it was told to be, but nothing in QIDI's software asks. When a motor slips, for example when the toolhead hits a curled part or the belt binds, the motor stays behind, and the rest of the print is shifted. The motor doesn't move back, and the printer doesn't notice.

The step-loss monitor asks both motors how far behind they are several times a second and pauses the print when one slips. It's **off by default**; turn it on from Fluidd's console:

```gcode
SAVE_VARIABLE VARIABLE=tltg_step_loss_monitor VALUE=1
```

Set it back to `0` to turn it off. Both take effect without a restart.

**What it does:**

- It runs while X and Y are homed and their motors are on. It records where each motor stands after every X/Y home and checks each motor about twice a second.
- When a motor slips during a print, the print pauses. The console shows `!! The stepper_x motor slipped 14 step cycles (10.9 mm of belt): the toolhead is no longer where Klipper thinks it is. Home X and Y before moving it again. The print is paused.`, and Fluidd's red warning banner shows how far each motor has slipped in total since X and Y were homed.
- A slip with no print running shows the same message without pausing.
- The warning stays until X and Y are homed again. Klipper's position is wrong after a slip, so don't resume a paused print unless a shifted layer doesn't matter; cancel it and home instead.

**How it tells a slip from normal motion:** a motor that slips falls behind by whole steps of 4 motor steps, 0.78 mm of belt each, and stays there. Normal printing moves leave a motor at most about 0.1 mm behind, and it catches up once the move ends. The monitor reports a slip once a motor is three quarters of a step cycle (0.59 mm of belt) away from where it stood after homing, on two checks in a row. A slip of less than one step cycle isn't detected.

Each motor answers in about 60 ms, a delay in the mainboard's firmware, so a slip is reported up to about a second after it happens. The monitor only asks the motors for their position; it doesn't change any motor setting. Printers without QIDI's closed-loop X/Y motors, such as the Plus 5, aren't affected.

## Input shaper calibration

Input shaper calibration, from `SHAPER_CALIBRATE` or the printer's screen, now runs the resonance test from newer Klipper. QIDI's test shakes the toolhead back and forth around one spot from 5 to 133 Hz, stopping and reversing twice every cycle. The stops are where friction in the rails and slack in the belts matter most, and they can add peaks that aren't the printer's resonance or shift the real ones. The newer test adds a slow back-and-forth on top of the shaking, so the toolhead spends most of the test moving steadily one way. Upstream Klipper made it its default in December 2024; it's meant to give the same answer on a printer in good shape and a steadier one on a printer with some friction or binding.

On a Max 4 in good shape, two runs of each test per axis recommended `mzv` at 41.0 Hz for Y with both tests, and `mzv` at 53.6 Hz (newer test) or 54.2 Hz (QIDI's) for X. Each test repeated its own answer exactly.

**What's different:**

- The toolhead wanders up to about 20 mm either side of the test point (`X195 Y195` on the Max 4) along the axis being tested. The test takes as long as before, about two minutes per axis.
- Calibration fades out motion below 10 Hz instead of cutting it off at 5 Hz, so the slow back-and-forth isn't taken for a resonance.
- The console says which test ran, for example `Resonance test with sweeping: ACCEL_PER_HZ=150 SWEEPING_PERIOD=1.2 SWEEPING_ACCEL=400`.

To run QIDI's test instead, for example to compare the two:

```gcode
SAVE_VARIABLE VARIABLE=tltg_resonance_sweep VALUE=0
```

Set it back to `1` for the newer test. The setting applies from the next calibration, including the one on the printer's screen, without a restart.

For a single run, add these to `SHAPER_CALIBRATE` or `TEST_RESONANCES`:

| Parameter | Default | What it does |
|---|---|---|
| `SWEEPING_PERIOD=` | `1.2`, or `0` while `tltg_resonance_sweep` is `0` | Seconds for each slow back-and-forth. `0` runs QIDI's test. |
| `SWEEPING_ACCEL=` | `400` | Acceleration of the slow back-and-forth, in mm/s². |
| `ACCEL_PER_HZ=` | `accel_per_hz` in `printer.cfg`, `150` on the Max 4 | How hard the toolhead shakes: acceleration in mm/s² per Hz tested. Upstream Klipper's default is 60. Applies to both tests. |

A test that would need more acceleration than `max_accel` in `printer.cfg` stops before moving and says so; lower `ACCEL_PER_HZ`, `SWEEPING_ACCEL`, or `FREQ_END`. If Klipper's resonance test or motion planning code isn't exactly the QIDI version this was written for, QIDI's test runs and `klippy.log` says `tltg_resonance_sweep: skipped`.
