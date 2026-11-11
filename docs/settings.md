# Settings and commands

[← Back to the README](../README.md)

Every setting this project adds, in one place. Run these from Fluidd's console.

## Saved settings

Set with `SAVE_VARIABLE VARIABLE=<name> VALUE=<value>`. They survive restarts and updates, and work with files you've already sliced unless noted.

| Setting | Default | What it does |
|---|---|---|
| [`tltg_keep_loaded_between_prints`](print-start.md#end-of-print-filament) | `1` | `1` keeps Box filament loaded after a print; `0` cuts and unloads it. |
| [`tltg_start_bed_mesh_profile`](print-start.md#reuse-a-saved-bed-mesh) | `""` | A profile name loads that saved bed mesh at print start; empty measures a fresh mesh. Quote it: `VALUE='"default"'`. |
| [`tltg_staggered_start_heating`](print-start.md#staggered-heating) | `0` | `1` heats the bed, chamber, then nozzle in turn. Needs current start G-code. |
| [`tltg_staggered_start_heating_dwell_seconds`](print-start.md#staggered-heating) | `10` | Pause between staggered heating stages, in seconds. |
| [`tltg_pause_timeout_hours`](print-start.md#long-pauses) | `72` | Hours a print can sit paused before it's cancelled. |
| [`tltg_box_link_monitor`](troubleshooting.md#box-link-monitor) | `0` | `1` records Box disconnect details to `tltg_box_link.log`. |
| [`tltg_step_loss_monitor`](tools.md#xy-step-loss-monitor) | `0` | `1` pauses the print and warns when an X or Y motor slips, and cancels it during print start. Max 4 only. |
| [`tltg_step_loss_recovery`](tools.md#correcting-the-position-after-a-slip) | `0` | With the step-loss monitor on, `1` corrects the toolhead position after a print's first slip and keeps printing instead of pausing. Max 4 only. |
| [`tltg_resonance_sweep`](tools.md#input-shaper-calibration) | `1` | `0` runs QIDI's resonance test for input shaper calibration instead of the newer sweeping one. |

## Commands

| Command | What it does |
|---|---|
| [`TLTG_BOX_MOTION`](qidi-box.md#load-and-unload-speed) | Shows or sets QIDI Box load and unload speeds, for all slots or one. |
| [`TLTG_BOX_SLOT_JOG SLOT= DISTANCE=`](qidi-box.md#moving-a-slot-by-hand) | Moves a QIDI Box slot's filament in or out by a set distance. |
| [`TLTG_BOX_RFID_REPORT SLOT= [SECONDS=]`](qidi-box.md#spools-that-dont-read) | Reads the spool tag in a QIDI Box slot into a report you can share. Needs the Valve Oil Box firmware. |
| [`TLTG_CUT_FILAMENT`](qidi-box.md#cutting-the-filament) | Cuts the filament at the toolhead, at any nozzle temperature, homing X and Y first if needed. Not during a print. |
| [`UNLOAD_FILAMENT_CONTROL SLOT= COLD=1`, `EJECT_FILAMENT_CONTROL SLOT= COLD=1`](qidi-box.md#unloading-and-ejecting-cold) | QIDI's Box unload and eject without heating the nozzle. Not during a print. |
| [`TLTG_FILAMENT_SENSOR ENABLE=0\|1`](tools.md#external-spool-runout-sensor) | Stops or restores external spool runout pauses until Klipper restarts. |
| [`TLTG_SPOOLMAN_SLOT SLOT= SPOOL=`](spoolman.md#giving-each-slot-its-spool) | Sets the Spoolman spool for a QIDI Box slot or the external spool, which becomes the active spool whenever that slot is loaded. No parameters lists them. |
| [`TLTG_RS_BOX_REPORT`](qidi-box.md#rs-485-boxes) | Writes the QIDI RS-485 Box report to `logs/tltg_box485_report.json` and says whether RS-485 hardware was found. |
| [`TLTG_RESET_TOOL_MAPPINGS`](tools.md) | Restores the default Box tool-to-slot mapping. Idle only. |
| [`TLTG_STEP_LOSS_CORRECT`](tools.md#correcting-the-position-after-a-slip) | Moves Klipper's X/Y position by the slip the X and Y motors report since X and Y were homed, instead of homing. Needs the step-loss monitor on. Max 4 only. |
| [`TLTG_TEST_PROBE CONFIRM=1`](tools.md#nozzle-load-cell-check) | Checks the nozzle load cell without moving. |
| [`TLTG_BED_SOAK BED= [CHAMBER=]`](tools.md#bed-soak-test) | Measures how long the bed and chamber take to reach temperature and the bed to stop moving. |
| [`TLTG_BED_SOAK_STOP`](tools.md#bed-soak-test) | Stops the bed soak test. |
| [`TLTG_PROBE_ACCURACY_CENTER`](tools.md) | Probe accuracy at the bed center. |
| [`TLTG_CORNER_BED_SCREW_CHECK`](tools.md) | Levels, then shows bed screw adjustments. |
| [`SHAPER_CALIBRATE`, `TEST_RESONANCES` with `SWEEPING_PERIOD=`, `SWEEPING_ACCEL=`, `ACCEL_PER_HZ=`](tools.md#input-shaper-calibration) | Klipper's resonance test settings for one run. `SWEEPING_PERIOD=0` runs QIDI's test. |

## In your slicer

| Setting | Where | What it does |
|---|---|---|
| [Chamber **Minimal** temperature](print-start.md#minimum-chamber-temperature) | OrcaSlicer 2.4.2+ filament profile | Starts printing once the chamber reaches this, while it keeps heating toward the target. |

## Installer options

See [Installing, updating, and uninstalling](install.md) for the dry run, OS optimizations, and automatic updates.

## Add-ons

| Command | What it does |
|---|---|
| [`~/tltg-optimized-macros/addons.sh list`](addons.md#from-ssh) | Lists the add-ons and which are installed. |
| [`~/tltg-optimized-macros/addons.sh install <id>`, `update <id>`, `remove <id>`](addons.md#from-ssh) | Installs, updates, or removes an add-on, such as `octoeverywhere` or `obico`, as Fluidd's **Add-ons** settings do. |
| [`~/tltg-optimized-macros/addons.sh relink obico [--server <url>]`](addons.md#changing-the-server-or-unlinking) | Unlinks Obico, optionally moves it to another Obico server, and restarts it so it can be linked again. |
