# Valve Oil's Klipper

`klippy/` is upstream Klipper v0.14.0 (`c9559fa04`) with QIDI's Max 4 01.01.07.01 Klipper and Valve Oil's fixes on top. `printers/` holds the files that differ on the Plus 5 and Smart 5 (01.01.02.01); see [`printers/README.md`](printers/README.md). Valve Oil installs it beside QIDI's `~/klipper`, which it leaves untouched, and operators choose which one runs (see [`docs/klipper.md`](../docs/klipper.md)).

QIDI's closed modules (`klippy/extras/*.so`) aren't here. The installer copies them from the printer's own `~/klipper` for firmware whose modules were checked against this tree, and uses QIDI's Klipper on any other firmware.

## How it was made

The tree was built on a branch of upstream Klipper: upstream v0.14.0, then QIDI's Max 4 01.01.07.01 `klippy/` sources (line endings normalized) merged with conflicts resolved, then the port commits. In short:

- Motion (toolhead, stepper, mcu, `motion_queuing`, chelper, kinematics, input shaping), the probe family, `led`, `neopixel`, `lis2dw`, `virtual_sdcard`, `webhooks`, `configfile` and the rest are v0.14 with QIDI's behaviour put back: Box feed and unload, the acceleration cap, the extruder enable switch, reverse Z homing, homing tolerance and retries, `static_out` pins, the boot reason query, QIDI's 3MF, plate and power-loss printing, QIDI's neopixel board protocol, TESTING_PEI and load-cell re-zeroing.
- `homing.py` keeps QIDI's homing core (with Valve Oil's homing changes); v0.14's split `home_rails` and Z homing through `[probe]` aren't merged.
- QIDI's compiled modules use the pre-0.14 interfaces, which are kept as compatibility shims: removed `MCU` methods and `register_response`, `gcode.Coord(*args)`, integer `homing_axes`, `fan.set_speed(print_time, value)`, the old `manual_home` signature, `BulkDataQueue(mcu, name, oid)`, `stepper.PrinterRail`, the 2024 `probe.ProbeSessionHelper`, and step generation for `box_stepper.so`'s own motion queue (`MCU_stepper.flush_compat_trapq`, `trapq_get_queued_end`).
- `lis2dw` sends QIDI's THR firmware its older `config_lis2dw` when the board's dictionary has it.
- Valve Oil's fixes that used to be patched in at runtime are part of the source: the stepper enable timing, the probe retry limit, the reactor timer scan, the status update savings, the Box heater settings, and the print_stats_manager reset guard. MCU code that upstream calls deprecated is logged instead of shown as a warning, because QIDI's board firmware can't be rebuilt from upstream.

## Testing changes

Klipper's batch mode runs this tree with QIDI's real configs and compiled modules: `klippy.py printer.cfg -i test.gcode -o out.serial -d mcu.dict` in an arm64 Debian bullseye container with Python 3.9, the printers' Python. Compare the step streams against QIDI's stock tree on the same G-code. Batch mode can't trigger endstops or probes, so homing, probing and Box flows that wait on sensors need a printer.

`chelper/` changes need a new `c_helper.so`: see [`chelper/README.md`](../chelper/README.md).
