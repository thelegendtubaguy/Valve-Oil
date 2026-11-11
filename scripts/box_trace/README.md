# QIDI Box tracer

`dev_box_trace.py` is a Klipper module that logs what QIDI's Box code does during loads, unloads, tool changes, and ejects. It only observes and changes no motion. Use it to see which moves, speeds, lengths, and settings a printer's Box firmware actually uses. It's for development only, and the installer never ships it.

```sh
scripts/box_trace/box_trace.sh install qidi@<printer-ip>   # copy the module, restart Klipper
# run Box loads and unloads on the printer
scripts/box_trace/box_trace.sh fetch qidi@<printer-ip>     # copy the log to tltg-local/box-trace/
scripts/box_trace/box_trace.sh remove qidi@<printer-ip>    # delete the module, restart Klipper
```

`install` and `remove` restart the Klipper service, so X/Y/Z end up unhomed. They refuse while a print is running or paused. Installing the package deletes the tracer's config file, so run `install` again after it.

## The log

The log is `~/printer_data/logs/dev_box_trace.log` on the printer. Each line starts with the Unix time and the local time, and lines are indented by call depth.

| Line | Meaning |
|---|---|
| `> slot0.do_home(endstops['b_endstop'], 3000, 85, 50, False)` | A call into QIDI's Box objects, with its arguments |
| `< slot0.do_home -> None after 3.214s` | Its result and duration, or the exception it raised |
| `key ('extruder_load_length_1', 3000) -> 3000` | A `box_extras.get_value_by_key(name, default)` lookup and its answer, which is the saved variable if one is set |
| `gcode 'MOVE_TO_TRASH\n'` | A G-code script run from inside a command, such as one QIDI's Box code sends |
| `response '...'` | A console message |
| `pos {...}` | Slot motor, toolhead, and extruder positions, sampled 10 times a second while anything moves |
| `status <name> {...}` | A change in a filament switch or in a `box_extras` status field |

Speeds come from the `pos` lines: the change in a slot motor's position between samples divided by the time between them.

At startup it logs each Box object's attributes and the methods it wrapped. While idle it logs about four lookups a second; methods that run constantly while idle are left unlogged (`MUTED_METHODS`), and any method is logged at most 10 times a second.

## Things that broke earlier versions

- Only bound methods and plain functions are wrapped. Klipper's cffi pointers, such as a stepper's `trapq`, report `callable()` as true, and replacing one shut Klipper down at the first Box move.
- Status reads are never logged. Moonraker and the tracer itself read `get_status` several times a second.
- `RESTART` and `FIRMWARE_RESTART` keep an already-loaded module, so a changed module needs a service restart. A Klipper that stopped in shutdown also needs `FIRMWARE_RESTART` after the service restart. `box_trace.sh` does both.
