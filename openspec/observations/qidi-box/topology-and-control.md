# QIDI Box topology and control observations

Evidence qualifiers are defined in `openspec/observations/README.md`. These observations apply to QIDI Max 4 firmware `01.01.06.04`.

## Active include graph

`config/printer.cfg` loads Valve Oil macros before the vendor Box stack:

```ini
[include vo-macros/*.cfg]
[include box.cfg]
[multi_color_controller]
```

`[include box.cfg]` creates Box MCU, slot, heater, sensor, fan, RFID, autofeed, `box_extras`, tool-wrapper, unload, and material-helper objects. `[multi_color_controller]` registers the public multi-color facade and publishes Moonraker state.

Vendor names are occupied across the final Klipper config even when their include appears after Valve Oil macros. Repository macros must not redefine vendor object or command names.

## Vendor ownership

| Surface | Owner | Observed role |
|---|---|---|
| `EXTRUDER_LOAD`, `EXTRUDER_UNLOAD`, `SLOT_UNLOAD`, slot preload/sync and stepper timing | `box_stepper.so` | Primary feeder and toolhead-assisted filament motion; core distances, speeds, accelerations, and branch logic are compiled. |
| `BOX_PRINT_START`, cutter, cleanup, dryer, retry, auto-reload, runout, and resume | `box_extras.so` | High-level local orchestration and saved-variable writes. |
| FM17550 reads | `box_rfid.so` | SPI RFID scheduling, query, retry window, and raw response handling. |
| `MCB_*`, limit events, feed assist, anti-wrap | `box_autofeed.so` | Separate MCU assist path; visible values do not tune primary `box_stepper.so` load/unload motion. |
| `MULTI_COLOR_*`, state machine, local/remote adapter, Moonraker schema | `multi_color_controller.so` | Public command/state facade. |
| USB topology, Box config mutation, `box_count`, restart requests | `box_detect.so` and qidiclient | Detection and generated include/config management. |
| Pins, stock wrapper macros, visible helper values | `config/box.cfg` | Hardware/config surface; not primary motion ownership. |
| Material/color/vendor lookup | `config/officiall_filas_list.cfg`, saved variables, qidiclient | Metadata resolution. |

`box_stepper.so`, `box_extras.so`, `box_autofeed.so`, `box_rfid.so`, `box_detect.so`, and `multi_color_controller.so` are captured aarch64 Cython extension modules. Their static symbols and strings locate behavior; fake-harness and runtime evidence determine readable behavior.

## Transport identity

**Runtime-confirmed.** A two-Box machine presented two `mcu_box*` objects that are each a USB CDC-ACM Klipper MCU whose USB product string is `QIDI_BOX_V2_1.2.1`. That string is also embedded in the `QD_MAX4_BOX` firmware image, so the USB Box MCU runs the Klipper-fork firmware described in `mcu-firmware.md`. Both boxes sat behind the same internal hub tree, one of them behind a nested hub. `mcu_box2` and `mcu_box1` are not interchangeable names: each is bound to one box by hardware serial in `box.cfg`.

The firmware bundle also ships an RS-485 Box MCU image (`QD_MAX4_BOX485`), which is a separate, non-Klipper, polled STM32F103 application. On a machine with two USB boxes, the `[box485]`/`[box2base]` stack is still loaded and probes for an RS-485 device about every two seconds, logging `Can not find 485 device`. That probe does not indicate a fault or an attached 485 Box.

Link-loss behaviour, failure signatures, and what the host can attribute are recorded in `link-failures.md`.

## Generated object topology

Plain Python `box_config.py` expands `[box_config boxN]` using:

```text
base slot = N * 4
box display index = N + 1
RFID reader base = N * 2 + 1
```

Each Box generates:

- `box_stepper slot<N*4>` through `slot<N*4+3>`;
- `heater_generic heater_box<N+1>` and `verify_heater heater_box<N+1>`;
- `temperature_sensor heater_temp_a_box<N+1>` and `heater_temp_b_box<N+1>`;
- `box_heater_fan heater_fan_a_box<N+1>` and `heater_fan_b_box<N+1>`;
- `controller_fan board_fan_box<N+1>`;
- `box_rfid card_reader_<N*2+1>` and `card_reader_<N*2+2>`.

For `box0`, generated slots are `slot0` through `slot3`, heater/sensor/fan names end in `box1`, and RFID readers are `card_reader_1` and `card_reader_2`. For `box1`, generated slots are `slot4` through `slot7`, names end in `box2`, and readers are `card_reader_3` and `card_reader_4`.

A one-Box generated graph creates two reader objects while `multi_color_controller.LocalAdapter.connect()` was recovered looking up readers `1` through `4`. **Harness-recovered:** `box_stepper` reads slot `N` through `card_reader_{N//2+1}`, so each reader serves two adjacent slots.

An older include inventory named `temperature_sensor box1_env`; generated-source and Moonraker captures identify `heater_temp_a_box1` and `heater_temp_b_box1` as the active generated NTC sensor names, with a separate `aht20_f heater_box1` object.

## Observed topologies

### 2026-05-07 one-Box runtime

- `mcu mcu_box1`, slots `0` through `3`, heater/sensors/fans for box 1, and one local controller were visible.
- `multi_color_controller.hardware.box_count=1`, `connected=true`, and `system.mode=local`.
- `slot2` was loaded and synced.

### 2026-06-13 two-Box runtime

- `mcu_box1` owned slots `0` through `3`; `mcu_box2` owned slots `4` through `7`.
- `heater_box1`, `heater_box2`, and corresponding temperature objects were visible.
- Controller and saved-variable `box_count` both reported `2`; `enable_box=1`.
- Material metadata existed for slots `4` through `7`, but `value_t4` through `value_t7` were absent after QIDI Client added the second Box.
- Harnessed `BOX_PRINT_START EXTRUDER=4` and `EXTRUDER=7` defaulted missing mappings to `slot16`; defining `value_t4='slot4'` or `value_t7='slot7'` dispatched to the physical slot.

The installer reconciles missing mappings; the absent second-Box mappings are vendor behavior, not an alternate desired mapping.

## Local controller path

The observed controller mode is `local`. `LocalAdapter` maps public operations to stock G-code:

| Operation | Local dispatch |
|---|---|
| load / unload | `E_LOAD SLOT=N` / `E_UNLOAD SLOT=N` |
| box eject | `E_BOX SLOT=N` |
| swap | `E_UNLOAD`, then `E_LOAD` |
| print start | `BOX_PRINT_START EXTRUDER=N HOTENDTEMP=T` |
| RFID | `SLOT_RFID_READ SLOT=slotN`, `INIT_RFID_READ` |
| sync / unsync | saves `slot_sync='slotN'` / `slot_sync='slot16'` |
| cleanup | `CLEAR_FLUSH`, `CLEAR_OOZE`, `CUT_FILAMENT` |
| heater/dryer | `BOX_TEMP_SET`, `ENABLE_BOX_DRY`, `DISABLE_BOX_DRY`, `DISABLE_BOX_HEATER` |

Stock `T0` through `T15` and `UNLOAD_T0` through `UNLOAD_T15` resolve `value_tN` and dispatch load/unload only when `enable_box=1`.

Stock `UNLOAD_FILAMENT` performs cutter, chute travel, mapped unload, `G1 E25 F300`, heater-off, ooze cleanup, and flush cleanup.

## Remote controller path

`RemoteAdapter` is recovered but was not active on the observed machine. It uses newline-delimited JSON over USB serial, defaults to `115200` baud, searches `/dev/ttyACM*` and `/dev/ttyUSB*`, and probes with:

```json
{"cmd":"ping"}
```

Each command includes `cmd`, generated `timestamp`, generated `id`, and used a `5.0 s` harness timeout. `slotN` values normalize to integers.

Remote command fields:

| Command | Additional fields |
|---|---|
| `load_filament`, `unload_filament` | `slot`, `options` |
| `swap_filament` | `from_slot`, `to_slot`, `options` |
| `box_unload`, `read_rfid`, `sync_to_extruder` | `slot` |
| `unsync_from_extruder`, `init_rfid`, `auto_reload`, `try_resume`, `init_mapping`, `disable_heater`, `clear_runout`, `clear_flush`, `clear_ooze` | none |
| `start_drying` | `box`, `temp`, `hours` |
| `stop_drying` | `box` |
| `reload_all` | `first` |
| `retry` | `rfid` |
| `tighten`, `cut_filament` | `tool` |
| `print_start` | `extruder`, `hotendtemp` |
| `resume_print` | `temp` |
| `set_temp` | `temp_params` containing Box/target parameters |

## Detection and config mutation

Static-recovered `box_detect.so` symbols and strings indicate monitoring under `/dev/serial/by-id/` for QIDI Box Klipper MCUs and markers `QIDI_BOX_V1`, `QIDI_BOX_V2`, and `mcu_box_to_v2`. It references:

```text
/home/qidi/printer_data/config/box.cfg
/home/qidi/printer_data/config/box1.cfg
/home/qidi/printer_data/config/box2.cfg
/home/qidi/printer_data/config/saved_variables.cfg
```

Recovered functions and strings indicate Box config/include updates, `box_count` persistence through `SAVE_VARIABLE`, and Klipper firmware-restart requests. The include matcher is `\[include box(\d+)\.cfg\]`. Klipper imports `box_detect.so`, but its calls are commented out, so it doesn't write `box.cfg`.

### The touchscreen app's box.cfg writer

**Static-recovered** from the Max 4 `01.01.07.01` app (`qidiclient` `b5285d1a…`) and the Plus 5 `01.01.02.01` app (`6c09a58a…`), which have the same code at different offsets:

- **Detection.** The detector lists tty devices with a `usb-Klipper_` device link and keeps those whose USB model starts with `02.03` or contains `QIDI_BOX_V`, in reverse udev enumeration order, which sorts by sysfs path. A Box's number in `box.cfg` therefore follows its USB port, not its serial number. It publishes each Box's serial number and version for the firmware page and Box notices. When a Box's version is older than the Box image in `~/update`, it counts 0 Boxes and writes nothing.
- **Change test.** It builds `box.cfg` and compares the result with its own last result, not with the file, and returns whether they differ from a single instruction (`mov w20, #1`; Max 4 file offset `0x264288`, Plus 5 `0x263438`).
- **Writes.** At startup, a difference from the file on disk makes it write `box.cfg` and run `systemctl restart klipper` without checking for a print. On a udev tty event for a Box, a change with Klipper's idle state `Ready` or `Idle` saves `box_count` and, 4 seconds after the last event, writes the file and restarts Klipper without checking again. The restart routine sets the extruder, bed, and chamber targets to 0 and waits up to about 30 seconds before restarting; other app paths use it too. The write goes through a temporary file and a rename.
- **Layout.** No Box gives `# No Box Config`. Otherwise, for up to five Boxes, a `[box_config box<i>]` section from one template on `mcu_box<i+1>` with slots `4i` to `4i+3`, then `[box_extras]` and `[box_autofeed]` on the last Box's MCU, then `[mcu mcu_box<n>]` with the Box's `/dev/serial/by-id` path and `restart_method : command` for each Box in order. `config/box.cfg` in this repository is that output for two Boxes, with the serial numbers redacted.
- **`box_count`.** The app also saves its detected count after each Moonraker subscription and Klippy state change.

**Runtime-confirmed** on the test Max 4 on 2026-11-13 (`01.01.07.01`, two USB Boxes): after Klipper reset a Box board, the detector saw 0, 0, 1, then 2 Boxes as they came back over about 4 seconds, and because its last result had followed the 0- and 1-Box states, it wrote an unchanged `box.cfg` and restarted Klipper 4 seconds after the last event. The same pattern appeared seven times on 2026-11-12.

**Runtime-confirmed** on the test Max 4 with Valve Oil's patched app and Box monitor (two USB Boxes, update log and `klippy.log`):

- A Valve Oil firmware update from `01.01.06.05` to `01.01.07.01` on 2026-11-14 wrote `box.cfg` at 01:42:02, after QIDI's package and before the board step, and flashed and verified both Boxes, one after the other. Klipper was stopped from 01:36 and started once for the boards, at 01:51:24, with no restart from the app afterwards. The next starts came from the Valve Oil install the update ran (01:54:35, then Klipper's own MCU restart for a config CRC mismatch at 01:55:10) and from the reboot after it.
- At the boot of 2026-11-15, Klipper started once (17:32:36) and was not restarted, the Box monitor logged `box.cfg already lists the Boxes`, `box.cfg` was unchanged since 2026-11-14 01:42:02, and saved `box_count` was 2.
- Unplugging Box 2's USB cable on 2026-11-15 (udev, the monitor's journal, `klippy.log`, and the app's log): 8 s after the tty was removed, the monitor wrote `box.cfg` for one Box and restarted Klipper. Klipper then restarted Box 1's and the mainboard's MCUs itself (`Attempting automated MCU 'mcu_box1' restart: CRC mismatch`), since `[box_extras]` and `[box_autofeed]` moved to Box 1's MCU; the monitor, started by those boards' udev events, found `box.cfg` current and restarted nothing. The app saved `box_count` 1 and restarted nothing. Plugging the cable back in, the monitor waited 17 s for the installer lock (held by the update check), then wrote `box.cfg` for two Boxes, byte for byte the file from before the unplug, so Box 2 kept its number, and restarted Klipper; the same MCU restart and unchanged monitor run followed, and `box_count` went back to 2.
- Shortly after the replug, the touchscreen's firmware page showed both Boxes' versions as `Unknown-02.03.01.21`, the mainboard and THR as before, and later, without any further action, the versions without the prefix (operator report). The app's log read both Boxes as `02.03.01.21` at each Klipper start. **Unresolved:** what sets the prefix, and whether QIDI's unpatched app shows it after a hotplug too.

**Config-confirmed:** the Max 4 `01.01.07.01` SOC package installs a `box.cfg` of four commented `# [include box<n>.cfg]` lines with CRLF line ends and no final newline, which the app replaces at its next start. The Max 4 `01.01.06.05` app's `box.cfg` has the same Box sections followed by `[gcode_macro T<n>]` and `[gcode_macro UNLOAD_T<n>]` tool macros.

Treat concurrent vendor detection mutation as possible where the app is not Valve Oil's patched build. Installer changes involving Box config or saved mappings require idle-state and current-topology validation.

## Repository-controlled integration boundary

- Valve Oil's fresh-load start delegates feeder movement to `BOX_PRINT_START`, then performs repository-owned purge/cleanup.
- Retained-filament start bypasses vendor start only after physical continuity and metadata checks.
- External-spool start avoids Box commands when the stack is absent or disabled.
- Repository macros may wrap vendor behavior but do not replace RFID/autofeed/retry logic or redefine vendor names.
- The only repository code that changes compiled feeder motion is `vo_box_feed_motion`. Wherever QIDI's Box code provides the traced methods and plausible arguments, it rewrites the arguments of each slot's three-stage load home and two-stage unload home from operator saved variables. It doesn't change the load pre-stage, plain `do_home`/`do_move`, or vendor motion keys.
- `config/box.cfg` remains stock-mapped and sensitive; exact MCU serial identifiers are never tracked.
