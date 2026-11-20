# Settings and commands

[← Back to the README](../README.md)

Every setting this project adds, in one place. Run these from Fluidd's console.

## Saved settings

Set with `SAVE_VARIABLE VARIABLE=<name> VALUE=<value>`. They survive restarts and updates, and work with files you've already sliced unless noted.

Before 26.11.18.2 these were named `tltg_` instead of `vo_`, for example `tltg_keep_loaded_between_prints`. The first time Klipper starts after the update, each setting you saved is saved under its `vo_` name too, with the same value. From then on only the `vo_` name does anything; the `tltg_` one is kept for going back to an earlier release.

| Setting | Default | What it does |
|---|---|---|
| [`vo_keep_loaded_between_prints`](print-start.md#end-of-print-filament) | `1` | `1` keeps Box filament loaded after a print; `0` cuts and unloads it. |
| [`vo_start_bed_mesh_profile`](print-start.md#reuse-a-saved-bed-mesh) | `""` | A profile name loads that saved bed mesh at print start; empty measures a fresh mesh. Quote it: `VALUE='"default"'`. |
| [`vo_staggered_start_heating`](print-start.md#staggered-heating) | `0` | `1` heats the bed, chamber, then nozzle in turn. Needs current start G-code. |
| [`vo_staggered_start_heating_dwell_seconds`](print-start.md#staggered-heating) | `10` | Pause between staggered heating stages, in seconds. |
| [`vo_pause_timeout_hours`](print-start.md#long-pauses) | `72` | Hours a print can sit paused before it's cancelled. |
| [`vo_box_link_monitor`](troubleshooting.md#box-link-monitor) | `0` | `1` records Box disconnect details to `tltg_box_link.log`. |
| [`vo_homing_guard`](fixes.md#xy-homing-grinding-into-the-frame) | `1` | `0` stops the guard that ends an X or Y homing move grinding into the frame and homes it again. Max 4 only. |
| [`vo_step_loss_monitor`](tools.md#xy-step-loss-monitor) | `0` | `1` pauses the print and warns when an X or Y motor slips, and cancels it during print start. Max 4 only. |
| [`vo_step_loss_recovery`](tools.md#correcting-the-position-after-a-slip) | `0` | With the step-loss monitor on, `1` corrects the toolhead position after a print's first slip and keeps printing instead of pausing. Max 4 only. |
| [`vo_resonance_sweep`](tools.md#input-shaper-calibration) | `1` | `0` runs QIDI's resonance test for input shaper calibration instead of the newer sweeping one. |
| [`vo_box_spool_roll`](qidi-box.md#turning-spools-while-the-box-heats) | `0` | `1` turns the spools of empty QIDI Box slots every 5 minutes while the Box heater is on. |
| [`vo_box_heater_pwm_cycle_time`](qidi-box.md#box-heater-switching-speed) | `0.1` | Seconds per on-off cycle of each USB QIDI Box heater, from `0.01` to `0.1`. Shorter can stop lights flickering. Takes effect when Klipper next restarts. |

## Commands

Before 26.11.18.2 these were named `TLTG_` instead of `VO_`, and several print macros `OPTIMIZED_`. The earlier names still work for now: each runs the new command and says what it's called now. The `OPTIMIZED_` macros the slicer packs call kept their names.

| Command | What it does |
|---|---|
| [`VO_BOX_MOTION`](qidi-box.md#load-and-unload-speed) | Shows or sets QIDI Box load and unload speeds, for all slots or one. |
| [`VO_BOX_SLOT_JOG SLOT= DISTANCE=`](qidi-box.md#moving-a-slot-by-hand) | Moves a QIDI Box slot's filament in or out by a set distance. |
| [`VO_BOX_RFID_REPORT SLOT= [SECONDS=]`](qidi-box.md#spools-that-dont-read) | Reads the spool tag in a QIDI Box slot into a report you can share. Needs the Valve Oil Box firmware. Before 26.11.16.1 it was `TLTG_BOX_RFID_REPORT`, which still works. |
| [`VO_BOX_RFID_WRITE SLOT= [FILAMENT=] [COLORS=] [NAME=0] [SECONDS=]`](qidi-box.md#writing-spool-tags) | Writes a filament to the spool tag in a QIDI Box slot, turning an empty slot's spool until the tag comes past. Needs the Valve Oil Box firmware. |
| [`VO_BOX_RFID_TEST SLOT= ACTION=SCAN\|WRITE`](qidi-box.md#writing-spool-tags) | Diagnostic: lists the tags a QIDI Box reader sees, or writes one with every option. |
| [`VO_CUT_FILAMENT`](qidi-box.md#cutting-the-filament) | Cuts the filament at the toolhead, at any nozzle temperature, homing X and Y first if needed. Not during a print. |
| [`UNLOAD_FILAMENT_CONTROL SLOT= COLD=1`, `EJECT_FILAMENT_CONTROL SLOT= COLD=1`](qidi-box.md#unloading-and-ejecting-cold) | QIDI's Box unload and eject without heating the nozzle. Not during a print. |
| [`VO_FILAMENT_SENSOR ENABLE=0\|1`](tools.md#external-spool-runout-sensor) | Stops or restores external spool runout pauses until Klipper restarts. |
| [`VO_SPOOLMAN_SLOT SLOT= SPOOL=`](spoolman.md#giving-each-slot-its-spool) | Sets the Spoolman spool for a QIDI Box slot or the external spool, which becomes the active spool whenever that slot is loaded. No parameters lists them. |
| [`VO_RS_BOX_REPORT`](qidi-box.md#rs-485-boxes) | Writes the QIDI RS-485 Box report to `logs/tltg_box485_report.json` and says whether RS-485 hardware was found. |
| [`VO_RESET_TOOL_MAPPINGS`](tools.md) | Restores the default Box tool-to-slot mapping. Idle only. |
| [`VO_STEP_LOSS_CORRECT`](tools.md#correcting-the-position-after-a-slip) | Moves Klipper's X/Y position by the slip the X and Y motors report since X and Y were homed, instead of homing. Needs the step-loss monitor on. Max 4 only. |
| [`VO_TEST_PROBE CONFIRM=1`](tools.md#nozzle-load-cell-check) | Checks the nozzle load cell without moving. |
| [`VO_BED_SOAK BED= [CHAMBER=]`](tools.md#bed-soak-test) | Measures how long the bed and chamber take to reach temperature and the bed to stop moving. |
| [`VO_BED_SOAK_STOP`](tools.md#bed-soak-test) | Stops the bed soak test. |
| [`VO_PROBE_ACCURACY_CENTER`](tools.md) | Probe accuracy at the bed center. |
| [`VO_CORNER_BED_SCREW_CHECK`](tools.md) | Levels, then shows bed screw adjustments. |
| [`SHAPER_CALIBRATE`, `TEST_RESONANCES` with `SWEEPING_PERIOD=`, `SWEEPING_ACCEL=`, `ACCEL_PER_HZ=`](tools.md#input-shaper-calibration) | Klipper's resonance test settings for one run. `SWEEPING_PERIOD=0` runs QIDI's test. |

## In your slicer

| Setting | Where | What it does |
|---|---|---|
| [Chamber **Minimal** temperature](print-start.md#minimum-chamber-temperature) | OrcaSlicer 2.4.2+ filament profile | Starts printing once the chamber reaches this, while it keeps heating toward the target. |

## Updates

Fluidd's Settings page has a **Valve Oil** section, and Valve Oil UI an [Updates](valve-oil-ui.md#updates) screen, with the **Automatic updates** switch, the **Update window**, and **Update now**. Over SSH:

| Command | What it does |
|---|---|
| [`~/valve-oil/install.sh`](install.md#updating) | Installs or updates Valve Oil, with the [QIDI firmware update](install.md#qidi-firmware-updates) when one is waiting, and follows, continues, or reports a firmware update already started. `--dry-run` shows what it would do; `--yes` runs without questions, and starts a waiting firmware update without asking. |
| [`~/valve-oil/auto-update.sh --enable`, `--disable`](install.md#automatic-updates) | Turns automatic updates on or off, without a password. The hourly update check runs either way. |
| [`~/valve-oil/auto-update.sh --status`](install.md#automatic-updates) | Says whether automatic updates are on, the update window, when the last check ran, and what's waiting. |
| [`~/valve-oil/auto-update.sh --window 2-5`, `--window any`](install.md#update-window) | Sets the hours automatic updates start in, or any hour. |
| [`~/valve-oil/auto-update.sh --timezone America/Chicago`](install.md#update-window) | Sets the timezone the update window is read in. |
| [`~/valve-oil/auto-update.sh --run`](install.md#automatic-updates) | Runs the update check now. It installs only with automatic updates on. |
| [`~/valve-oil/box-firmware.sh status`, `install`, `restore`, `rescue`](firmware.md#qidi-box-firmware) | Shows, flashes, or restores QIDI's firmware on each USB Box. |
| [`~/valve-oil/mainboard-firmware.sh status`, `install`, `restore`, `rescue`](firmware.md#mainboard-closed-loop-reply-fix) | Shows, flashes, or restores QIDI's firmware on the mainboard. |

See [Installing, updating, and uninstalling](install.md) for the dry run and the OS optimizations.

## QIDI's cloud

**LAN only mode** in the **Valve Oil** section of Fluidd's Settings is QIDI's own setting, the same one as the touchscreen's LAN Only switch. On, the printer disconnects from QIDI's cloud and stops QIDI's remote access, so the Qidi Joy app and signing in to a QIDI account don't work; Fluidd, Valve Oil UI, and your slicers keep working on your network. Off, the printer connects to QIDI's cloud again. A change made on the touchscreen shows in Fluidd the next time the page is opened.

## Add-ons

| Command | What it does |
|---|---|
| [`~/valve-oil/addons.sh list`](addons.md#from-ssh) | Lists the add-ons and which are installed. |
| [`~/valve-oil/addons.sh install <id>`, `update <id>`, `remove <id>`](addons.md#from-ssh) | Installs, updates, or removes an add-on, such as `octoeverywhere`, `obico`, `tailscale`, or `zerotier`, as Fluidd's **Add-ons** settings do. |
| [`~/valve-oil/addons.sh relink obico [--server <url>]`](addons.md#changing-the-server-or-unlinking) | Unlinks Obico, optionally moves it to another Obico server, and restarts it so it can be linked again. |
