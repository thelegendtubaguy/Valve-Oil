# Plus 4 inventory

Task 1.4: what every installed piece does on a Plus 4 `2.1.1`. Sources: the Plus 4 stock snapshot, the Plus 5 snapshot for comparison, and a static check of each Klipper extra's imports against a Plus 4 `klippy` tree assembled from `QIDITECH/klipper` `PLUS4` (`V1.7.1`, `4bb7c63`) with the `2.1.1` payload's 23 files laid over it. That assembled tree is an approximation of a `2.1.1` printer until task 2.3 compares a real one.

Classes:

- **Neutral:** installs on the Plus 4 as on the other printers.
- **Plus 4 version:** the behavior applies, but the file wraps or overrides `klipper-macros-qd` names, so `printers/plus4/` carries a Plus 4 version under QIDI's Plus 4 names.
- **Runtime-gated:** installs, and the module turns itself off or skips a fix where it doesn't recognize QIDI's code or hardware.
- **Not for the Plus 4:** depends on hardware or files the Plus 4 doesn't have; not installed there.

## Klipper extras (`installer/klipper/extras/`)

Every module's Klipper imports resolve in the assembled Plus 4 tree except where noted.

| Module | Class | Notes |
|---|---|---|
| `background_save_config` | Neutral | |
| `bed_soak` | Plus 4 version | Its probing targets the load-cell probe; the Plus 4 probes with `smart_effector`. Checked on hardware first. |
| `box485_monitor` | Not for the Plus 4 | Imports `box485_multicolor` and `box_protocol`, which the Plus 4 doesn't have (no RS-485 Box). |
| `box_eject` | Plus 4 version | Its macro calls `PARK`, a `klipper-macros-qd` macro. |
| `box_feed_motion` | Runtime-gated | The Plus 4 `box_stepper.so` (`5de15644…`) isn't a known build, so it stays inactive. |
| `box_feed_test`, `box_filament_guard`, `box_flow_recovery`, `box_heater_refresh`, `box_link_monitor`, `box_loaded_slot`, `box_mcu_temperature`, `box_rfid`, `box_slot_jog`, `box_spool_roll` | Undecided | Import cleanly. Each wraps or reads QIDI's Box objects (`multi_color_controller`, `box_extras`, `box_stepper`), which on the Plus 4 are other compiled builds. The Plus 4 builds name all 25 QIDI-specific methods and attributes checked from these modules' lookups except `_pause_print_retry_flow`, which `box_flow_recovery` treats as optional. Decided by task 1.6's harness and trace. |
| `chamber_exhaust_guard` | Undecided | Its fix is for a QIDI `M106 P3` that turns the chamber heater off; the Plus 4's `M106 P3` only sets `chamber_circulation_fan`. Its heater-rate guard may still apply. |
| `cold_cut` | Undecided | Depends on the toolhead and cutter geometry (task 2.6). |
| `filament_catalog`, `spoolman`, `macro_status`, `printer_warnings`, `pause_timeout`, `save_variables_fast`, `renamed_commands`, `update_lights`, `velocity_limit` | Neutral | |
| `homing_guard` | Not for the Plus 4 | Guards the Max 4's closed-loop X/Y homing. |
| `klipper_fixes` | Runtime-gated | Each fix replaces a QIDI function only when its source hash matches QIDI's Max 4 or Plus 5 original, and each runs inside its own `try`. In the Plus 4 tree, `probe.py` has no `ProbeSessionHelper` or `calc_probe_z_average` and `stepper_enable.py` no `ENABLE_DELAY_TIME`, so at least the probe retry and stepper-enable fixes stay off. Which Plus 4 functions carry the same defects is a later change. |
| `klipper_health` | Neutral | Uses `mcu` object methods present in the Plus 4 `mcu.py`. |
| `probe_report`, `probe_test` | Plus 4 version | Written for the load-cell probe. |
| `resonance_sweep` | Undecided | Depends on the Plus 4's `resonance_tester.py` and `shaper_calibrate.py`, not yet compared. |
| `step_loss_monitor` | Not for the Plus 4 | Closed-loop X/Y only (inert without `cl_interface`). |

## Common macros (`installer/klipper/macros/`)

| File | Class | Notes |
|---|---|---|
| The 24 files that only declare an extra's section (`background_save_config.cfg`, the 11 `box_*.cfg`, `cold_cut.cfg`, `klipper_fixes.cfg`, `klipper_health.cfg`, `macro_status.cfg`, `resonance_sweep.cfg`, `save_variables_fast.cfg`, `spoolman.cfg`, `update_lights.cfg`, `homing_guard.cfg`, `renamed_commands.cfg`, `bed_soak.cfg`, `box485_monitor.cfg`) | Follow their extra | `box_eject.cfg` also calls `PARK`. |
| `temperatures.cfg` | Plus 4 version | `Toolhead_MCU` reads `sensor_mcu: THR`, an MCU the Plus 4 doesn't have (its MCUs are `mcu` and `U_1`), so Klipper would refuse the config. Stock Plus 4 already has `[temperature_sensor GD32]`. |
| `globals.cfg` | Neutral | `_vo_globals` only. |
| `cooling.cfg` | Neutral | Its `M106` maps P0, P2, P3, and P4 only to fans that exist; the Plus 4's P0, P2, and P3 fans have the same names. |
| `printer_warnings.cfg` | Neutral | |
| `input_shaper.cfg` | Neutral | Reads QIDI's default shaper from `_vo_printer`, which the Plus 4 profile supplies. |
| `helpers.cfg` | Plus 4 version | Its bed-screw check uses `[screws_tilt_adjust]`; the Plus 4 has `[bed_screws]` and a probe offset of X 25, Y 1.3. |
| `motion.cfg` | Plus 4 version | Holds QIDI's `M203` (a `klipper-macros-qd` macro) to `max_velocity`; the Plus 4 has no `M203`. |
| `bed_mesh.cfg` | Plus 4 version | Wraps the Max 4 and Plus 5 `BED_MESH_CALIBRATE` macro; the Plus 4 uses Klipper's own with QIDI's `G29`. |
| `cancel.cfg` | Plus 4 version | Calls `_print_end_inner`. |
| `filament.cfg` | Plus 4 version | Replaces `RESUME` and uses `_km_globals` and `M1002`; the Plus 4's `RESUME` is in `gcode_macro.cfg`. |
| `heaters.cfg` | Plus 4 version | Uses `set_heater_temperature_scaled` and `_gcode_wait_wrapper`. |
| `kinematics.cfg` | Plus 4 version | Uses `_km_globals`; the Plus 4's homing is `homing_override` in `gcode_macro.cfg`. |
| `offset.cfg` | Plus 4 version | Replaces `_km_apply_print_offset`; the Plus 4 applies Z offset with `set_zoffset` and `move_subzoffset`. |
| `start_end.cfg` | Plus 4 version | Uses `_km_globals`; the Plus 4's start is the single `PRINT_START`. |
| `step_loss_monitor.cfg` | Not for the Plus 4 | |

## Guarded patches (`installer/package.yaml` `patches:`)

| Patch | Plus 4 `2.1.1` | Decision |
|---|---|---|
| X/Y `homing_speed`, `second_homing_speed`, `homing_retract_dist`, `homing_retract_speed` | 50 mm/s, single touch, sensorless | No variant until Plus 4 homing is measured (task 2.5). |
| `stepper_z_homing_retract_dist` | Already 5 | None needed. |
| `printer_max_velocity` | 600 | Max 4 only. |
| `z_tilt_speed`, `bed_mesh_speed` | 150 | No variant until probing is measured. |
| `virtual_sdcard_on_error_gcode` | `CANCEL_PRINT` | Plus 4 variant once the Plus 4 cancel path exists. |
| `hotend_fan_tachometer_poll_interval` | No tachometer on `hotend_fan` | None. |
| `board_fan_steppers` | Already `stepper_x,stepper_y` | None needed: QIDI runs the fan with X/Y on the Plus 4. |
| `timelapse_take_frame_verbose` | `timelapse.cfg` not in the payload | After task 2.2. |
| `closed_loop_*` | No `[closed_loop]` | Max 4 only. |
| `chamber_thermal_protection_sensor_max_temp` | 140 | Decided with the chamber behavior. |
| `official_fila25_*` | Already `PA6-CF` | None needed. |
| `delete_sections`: `filament_switch_sensor filament_switch_sensor` | The Plus 4's is `filament_switch_sensor fila`, a tangle sensor | Not the same sensor; none. |
| `delete_sections` in `klipper-macros-qd/*` | No such files | Plus 4 equivalents (`RESUME`, `MOVE_TO_TRASH`, `M106`, `homing_override` in `gcode_macro.cfg`) only where a Plus 4 macro replaces them. |

## Install steps and system optimizations

| Entry | Decision |
|---|---|
| `ensure_lines` `include_optimized_macros` (after `[include klipper-macros-qd/*.cfg]`) | Plus 4 variant after `[include box.cfg]` (D8). |
| `source_patches` (`qidi_homing` and the rest) | None at first: their expected hashes are QIDI's Max 4, Plus 5, and Smart 5 files. |
| `dns` | Possibly; decided on a Plus 4. |
| `apt_sources` | Never on the Plus 4 as written: it writes `bullseye` sources on a Debian 10 host. |
| `services` (`xl2tpd`, `bluetooth`, `algo_app`) | Decided from the Plus 4's unit list (task 2.1). |
| `qidiclient_static_gifs`, `rockchip_root_sync`, `zram_swap` | Not for the Plus 4: QIDI's framebuffer app, Rockchip first-boot service, and the `5.10.160` kernel. |
| `moonraker_file_manager`, `moonraker_metadata_3mf`, `moonraker_speedups` | Not at first: they target `/home/qidi/moonraker` sources and Python 3.9 wheels. |
| `fluidd_box_filament_ids`, `fluidd_filament_config`, `fluidd_build` | After checking the Plus 4's Moonraker (D7). |
| `klipper_box_config`, `klipper_print_stats_manager` | Plus 4 versions only after task 1.6; the Plus 4's Box code is compiled, and its `print_stats_manager.py` differs. |
| `valve_oil_ui` | After checking it against the Plus 4's Moonraker. |
