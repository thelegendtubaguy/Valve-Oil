# Settings and commands

[← Back to the README](../README.md)

Every setting this project adds, in one place. Run these from Fluidd's console.

## Saved settings

Set with `SAVE_VARIABLE VARIABLE=<name> VALUE=<value>`. They survive restarts and updates, and work with files you've already sliced unless noted.

Before 26.11.18.2 these were named `tltg_` instead of `vo_`, for example `tltg_keep_loaded_between_prints`. The first time Klipper starts after the update, each setting you saved is saved under its `vo_` name too, with the same value. From then on only the `vo_` name does anything; the `vo_` one is kept for going back to an earlier release.

| Setting | Default | What it does |
|---|---|---|
| [`vo_keep_loaded_between_prints`](print-start.md#end-of-print-filament) | `1` | `1` keeps Box filament loaded after a print; `0` cuts and unloads it. |
| [`vo_start_bed_mesh_profile`](print-start.md#reuse-a-saved-bed-mesh) | `""` | A profile name loads that saved bed mesh at print start; empty measures a fresh mesh. Quote it: `VALUE='"default"'`. |
| [`vo_staggered_start_heating`](print-start.md#staggered-heating) | `0` | `1` heats the bed, chamber, then nozzle in turn. Needs current start G-code. |
| [`vo_staggered_start_heating_dwell_seconds`](print-start.md#staggered-heating) | `10` | Pause between staggered heating stages, in seconds. |
| [`vo_pause_timeout_hours`](print-start.md#long-pauses) | `72` | Hours a print can sit paused before it's cancelled. |
| [`vo_box_link_monitor`](troubleshooting.md#box-link-monitor) | `0` | `1` records Box disconnect details to `vo_box_link.log`. |
| [`vo_homing_guard`](fixes.md#xy-homing-grinding-into-the-frame) | `1` | `0` stops the guard that checks each X and Y touch is armed before it moves and ends one grinding into the frame. Max 4 only. |
| [`vo_step_loss_monitor`](tools.md#xy-step-loss-monitor) | `0` | `1` pauses the print and warns when an X or Y motor slips, and cancels it during print start. Max 4 only. |
| [`vo_step_loss_recovery`](tools.md#correcting-the-position-after-a-slip) | `0` | With the step-loss monitor on, `1` corrects the toolhead position after a print's first slip and keeps printing instead of pausing. Max 4 only. |
| [`vo_resonance_sweep`](tools.md#input-shaper-calibration) | `1` | `0` runs QIDI's resonance test for input shaper calibration instead of the newer sweeping one. |
| [`vo_box_spool_roll`](qidi-box.md#turning-spools-while-the-box-heats) | `0` | `1` turns the spools of empty QIDI Box slots every 5 minutes while the Box heater is on. |
| [`vo_box_fan_threshold`](qidi-box.md#box-fans-on-a-warm-printer) | `35` | Box air temperature in °C, from `35` to `45`, above which USB QIDI Box circulation fans run. |
| [`vo_box_fan_hysteresis`](qidi-box.md#box-fans-on-a-warm-printer) | `0.5` | How far in °C, from `0` to `2`, the Box air falls below `vo_box_fan_threshold` before the fans start their minute's run-on. |
| [`vo_box_heater_pwm_cycle_time`](qidi-box.md#box-heater-switching-speed) | `0.1` | Seconds per on-off cycle of each USB QIDI Box heater, from `0.01` to `0.1`. Shorter can stop lights flickering. Takes effect when Klipper next restarts. |

## Commands

Before 26.11.18.2 these were named `TLTG_` instead of `VO_`, and several print macros `OPTIMIZED_`. The earlier names still work for now: each runs the new command and says what it's called now. The `OPTIMIZED_` macros the slicer packs call kept their names.

| Command | What it does |
|---|---|
| [`VO_KLIPPER [USE=VALVE_OIL\|QIDI]`](klipper.md#turning-it-on-and-off) | Says which Klipper is chosen and running, or switches between QIDI's Klipper and Valve Oil's, restarting Klipper. Not during a print. |
| [`VO_BOX_MOTION`](qidi-box.md#load-and-unload-speed) | Shows or sets QIDI Box load and unload speeds, for all slots or one. |
| [`VO_BOX_SLOT_JOG SLOT= DISTANCE=`](qidi-box.md#moving-a-slot-by-hand) | Moves a QIDI Box slot's filament in or out by a set distance. |
| [`VO_BOX_RFID_REPORT SLOT= [SECONDS=]`](qidi-box.md#spools-that-dont-read) | Reads the spool tag in a QIDI Box slot into a report you can share. Needs the Valve Oil Box firmware. Before 26.11.16.1 it was `TLTG_BOX_RFID_REPORT`, which still works. |
| [`VO_BOX_RFID_WRITE SLOT= [FILAMENT=] [COLORS=] [NAME=0] [SECONDS=]`](qidi-box.md#writing-spool-tags) | Writes a filament to the spool tag in a QIDI Box slot, turning an empty slot's spool until the tag comes past. Needs the Valve Oil Box firmware. |
| [`VO_BOX_RFID_TEST SLOT= ACTION=SCAN\|WRITE`](qidi-box.md#writing-spool-tags) | Diagnostic: lists the tags a QIDI Box reader sees, or writes one with every option. |
| [`VO_CUT_FILAMENT`](qidi-box.md#cutting-the-filament) | Cuts the filament at the toolhead, at any nozzle temperature, homing X and Y first if needed. Not during a print. |
| [`UNLOAD_FILAMENT_CONTROL SLOT= COLD=`, `EJECT_FILAMENT_CONTROL SLOT= COLD=`](qidi-box.md#unloading-and-ejecting-cold) | QIDI's Box unload and eject run without heating the nozzle between prints, the default; `COLD=0` heats as QIDI does. `COLD=1` is refused during a print. |
| [`VO_LIGHT_EFFECT EFFECT= [RED= GREEN= BLUE=] [BRIGHTNESS=] [SPEED=] [DIRECTION=]`](valve-oil-ui.md#the-rgb-light) | Sets the RGB light's effect on the Max 4, Plus 5, and Smart 5: `solid`, `breathe`, `flow`, `rainbow`, `comet`, `spread`, or `flash`, or `status` for QIDI's status light, `off`, or `on` for the last effect. Colors are 0 to 255, brightness 0 to 100, speed `slow`, `normal`, or `fast`, and direction 0 or 1. What it leaves out stays as it was. |
| [`VO_FILAMENT_SENSOR ENABLE=0\|1`](tools.md#external-spool-runout-sensor) | Stops or restores external spool runout pauses until Klipper restarts. |
| [`VO_SPOOLMAN_SLOT SLOT= SPOOL=`](spoolman.md#giving-each-slot-its-spool) | Sets the Spoolman spool for a QIDI Box slot or the external spool, which becomes the active spool whenever that slot is loaded. No parameters lists them. |
| [`VO_RS_BOX_REPORT`](qidi-box.md#rs-485-boxes) | Writes the QIDI RS-485 Box report to `logs/vo_box485_report.json` and says whether RS-485 hardware was found. |
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

Valve Oil keeps the printer off QIDI's servers, on every install, so a QIDI account can't be used on it. QIDI's own LAN only mode stays on. The touchscreen shows **LAN Only Mode** where the QIDI account was, its **Log in** button is greyed out, and its LAN Only switch, at the bottom of the Wi-Fi page, turns itself back on when you turn it off. QIDI's phone app doesn't work either. Fluidd, Valve Oil UI, and your slicers keep working on your network, and QIDI Studio still finds the printer there. Each attempt to turn LAN only mode off is logged in `moonraker.log` as `vo_moonraker_fixes: general.lan_mode set to ... was answered with 1; LAN only mode stays on`.

Nothing on the printer reaches QIDI or the other services QIDI's software contacts: QIDI's cloud, download, MQTT, and P2P servers, Peergine's P2P servers, the services that look up the printer's public address and location, Moonraker's announcements feed, and the time server QIDI's touchscreen app uses. Their names point nowhere in a marked block of `/etc/hosts`, connections to their addresses fail at once, and QIDI's remote access client, `frpc`, stays stopped. Moonraker no longer runs QIDI's cloud connection at all; see [MQTT and Home Assistant](#mqtt-and-home-assistant). The printer still uses its DNS servers, your network's and the public ones QIDI's firmware adds (`1.1.1.1` and `8.8.8.8`), and reaches the rest of the internet, so Valve Oil's updates and the add-ons you install, such as ZeroTier and OctoEverywhere, keep working.

Uninstall removes the block and puts QIDI's own MQTT connection back. LAN only mode stays on until you turn it off on the touchscreen.

### MQTT and Home Assistant

QIDI's Moonraker came with QIDI's own MQTT connection, which connected only to QIDI's cloud and ignored an `[mqtt]` section in `moonraker.conf`, so Moonraker couldn't talk to your own MQTT broker. Valve Oil runs Moonraker's own MQTT instead, from Moonraker `v0.9.3`, the version QIDI's Moonraker is built on. Without an `[mqtt]` section it connects nowhere.

To connect to your broker, for example for Home Assistant, add an `[mqtt]` section to `moonraker.conf` (Fluidd's **Configuration** page) and save and restart Moonraker:

```ini
[mqtt]
address: 192.168.1.10
username: printer
password: your-password
```

Every option is in [Moonraker's MQTT documentation](https://moonraker.readthedocs.io/en/latest/configuration/#mqtt).

## Add-ons

| Command | What it does |
|---|---|
| [`~/valve-oil/addons.sh list`](addons.md#from-ssh) | Lists the add-ons and which are installed. |
| [`~/valve-oil/addons.sh install <id>`, `update <id>`, `remove <id>`](addons.md#from-ssh) | Installs, updates, or removes an add-on, such as `octoeverywhere`, `obico`, `tailscale`, or `zerotier`, as Fluidd's **Add-ons** settings do. |
| [`~/valve-oil/addons.sh relink obico [--server <url>]`](addons.md#changing-the-server-or-unlinking) | Unlinks Obico, optionally moves it to another Obico server, and restarts it so it can be linked again. |
